"""Only public farm data leaves this module; owner's financial data stays private."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from ...config import get_settings
from ...models import Notification, User, UserProfile
from ..notifications import create_notification
from . import presence, rules
from .engine import BOARDS, day, fail, instant, integer, iso, week_start
from .models import GameEvent, GameFarm, GameLeaderboard, GameSettlement, GameSlot
from .random import FarmRandom, farm_seed

# Walking into a farm leaves one footprint per visitor per owner in this window.
VISIT_FOOTPRINT_MINUTES = 30
REACTIONS = ("❤️", "👍", "🌱", "🎉", "👋", "🌻")

POLICY = {
    "dailyHelp": 10,
    "helpCoins": 5,
    "dailySteal": 10,
    "stealCooldownHours": 4,
    "weeklyTargetLimit": 5,
    "stealFraction": 0.2,
    "ownerMinimumFraction": 0.6,
    "newbieDays": 7,
    "dogCatchChance": 0.25,
    "dogPrice": 500,
    "feedPrice": 50,
    "dogHours": 72,
    "insuranceFraction": 0.3,
    "returnGiftHours": 12,
    "minimumPlayers": 5,
}


def identity(db, farm, anonymous=False):
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == farm.user_id))
    if anonymous and farm.anonymous:
        return {"userId": None, "displayName": "匿名農夫", "avatarUrl": None}
    return {
        "userId": farm.user_id,
        "displayName": farm.state.get("nickname") or "尚未取暱稱的農友",
        "avatarUrl": f"{get_settings().api_prefix}/profile/avatar/{farm.user_id}"
        if profile and profile.avatar_file_id
        else None,
    }


def public_farm(service, farm, detail=False):
    result = {
        **identity(service.db, farm),
        "isHome": presence.is_home(farm.user_id),
        "newbieUntil": iso(farm.newbie_until),
        "title": farm.title,
        "guardDogUntil": farm.state.get("guardDogUntil"),
        "thiefUntil": iso(farm.thief_until)
        if farm.thief_until and instant(farm.thief_until) > service.now
        else None,
    }
    if detail:
        result.update(
            plots=[s.plot for s in service.slots(farm.user_id)],
            layout=farm.layout,
            serverTime=iso(service.now),
        )
    return result


def farm_list(service, uid, filter):
    farms = list(
        service.db.scalars(
            select(GameFarm)
            .join(User, User.id == GameFarm.user_id)
            .where(GameFarm.enabled.is_(True), User.is_enabled.is_(True), GameFarm.user_id != uid)
        )
    )
    rows = []
    for farm in farms:
        if filter == "active" and instant(farm.state["lastSeenAt"]) < service.now - timedelta(
            days=1
        ):
            continue
        # No advance on another user's unlocked rows; preview computes without mutation.
        if filter == "ready" and not any(
            rules.advance(s.plot, service.now, [], service.config)["growth"] >= 1
            and not rules.advance(s.plot, service.now, [], service.config)["withered"]
            for s in service.slots(farm.user_id)
        ):
            continue
        rows.append(public_farm(service, farm))
    return rows


def social_action(service, actor, owner, data):
    if actor.user_id == owner.user_id:
        fail("SELF_VISIT", "不能對自己執行拜訪動作", 400)
    service.advance(actor)
    service.advance(owner)
    action = data["action"]
    uid = actor.user_id
    target = owner.user_id
    if action == "returnGift":
        data = {**data, "returnGift": data.get("eventId")}
        action = "steal"
    events = list(
        service.db.scalars(
            select(GameEvent).where(
                GameEvent.actor_id == uid, GameEvent.created_at >= service.now - timedelta(days=8)
            )
        )
    )
    today = [e for e in events if day(e.created_at) == day(service.now)]
    idx = data.get("index")
    slot = None
    if action in ("help", "steal"):
        idx = integer(idx, "index", 0, 23)
        slot = service.db.get(GameSlot, (target, idx))
        if not slot:
            fail("INVALID_PLOT", "找不到田地", 404)
        if slot.plot["cropId"] is None or slot.plot["withered"]:
            fail("EMPTY_PLOT", "田地沒有活的作物")
    event_id = str(uuid4())
    result = {}
    event_data = {"index": idx}
    if action == "help":
        if sum(e.kind == "help" for e in today) >= POLICY["dailyHelp"]:
            fail("DAILY_LIMIT", "今天已幫忙 10 次")
        kind = data.get("kind")
        if kind not in ("water", "weed", "removePests"):
            fail("INVALID_ACTION", "無效幫忙種類", 400)
        p = slot.plot
        if (
            kind == "water"
            and p["moisture"] >= 100
            or kind == "weed"
            and not p["weeds"]
            or kind == "removePests"
            and not p["pests"]
        ):
            fail("NO_HELP_NEEDED", "這塊田目前不需要這項幫忙")
        slot.plot = rules.care(p, kind, service.config)
        service.wallet(uid, POLICY["helpCoins"], "help", event_id)
        service.wallet(target, POLICY["helpCoins"], "help_received", event_id)
        actor.conscience = min(100, actor.conscience + 1)
        event_data["kind"] = kind
        result = {"message": "已幫忙，雙方各獲得 5 好幣"}
    elif action == "steal":
        if instant(actor.newbie_until) > service.now or instant(owner.newbie_until) > service.now:
            fail("NEWBIE_PROTECTION", "新手保護期間不能偷菜或被偷")
        if actor.conscience < 20:
            fail("LOW_CONSCIENCE", "良心值低於 20，不能偷菜")
        gift = None
        if data.get("returnGift"):
            gift = service.db.get(GameEvent, data["returnGift"])
            if (
                not gift
                or gift.kind != "steal"
                or gift.owner_id != uid
                or gift.actor_id != target
                or gift.data.get("caught")
                or gift.data.get("returned")
                or instant(gift.created_at) + timedelta(hours=POLICY["returnGiftHours"])
                <= service.now
            ):
                fail("NO_RETURN_GIFT", "回禮機會不存在或已到期")
        same = [e for e in events if e.kind == "steal" and e.owner_id == target]
        if gift is None:
            if presence.is_home(target):
                fail("OWNER_HOME", "主人在家，下次再來")
            if sum(e.kind == "steal" for e in today) >= POLICY["dailySteal"]:
                fail("DAILY_LIMIT", "今天已偷菜 10 次")
            if any(
                instant(e.created_at) > service.now - timedelta(hours=POLICY["stealCooldownHours"])
                for e in same
            ):
                fail("COOLDOWN", "同一農場每 4 小時只能偷一次")
        if (
            sum(instant(e.created_at) >= week_start(service.now) for e in same)
            >= POLICY["weeklyTargetLimit"]
        ):
            fail("WEEKLY_LIMIT", "本週已拜訪這座農場偷菜 5 次")
        harvest = rules.harvest(slot.plot, service.now, service.config)
        total = harvest["quantity"]
        allowance = min(
            max(1, int(total * POLICY["stealFraction"])),
            total - math_ceil(total * POLICY["ownerMinimumFraction"]) - slot.stolen_qty,
        )
        if (
            rules.level(actor.state["xp"], service.config)
            > rules.level(owner.state["xp"], service.config) + 10
        ):
            allowance = min(allowance, max(1, int(total * POLICY["stealFraction"] / 2)))
        if allowance <= 0:
            fail("OWNER_RESERVE", "主人保留的收成不能再偷")
        guard = owner.state.get("guardDogUntil")
        price = service.market_prices()[harvest["cropId"]]
        if guard and instant(guard) > service.now and service.balance(uid) < price * 3:
            fail("INSUFFICIENT_COINS", "好幣不足以支付看門狗可能產生的罰金")
        # UUID generated on the server, so client cannot choose successful PRNG seeds.
        caught = bool(
            guard
            and instant(guard) > service.now
            and FarmRandom(farm_seed("dog|" + event_id)).next_double() < POLICY["dogCatchChance"]
        )
        if caught:
            fine = price * 3
            service.wallet(uid, -fine, "fine", event_id)
            service.wallet(target, fine, "fine_received", event_id)
            actor.conscience = max(0, actor.conscience - 10)
            actor.thief_until = service.now + timedelta(hours=24)
            result = {"message": "被看門狗抓到，罰金已付給主人", "quantity": 0, "caught": True}
            service.notify(
                owner,
                "guardian",
                category="farm",
                title="看門狗抓到小偷",
                body="罰金已存入農場錢包。",
                payload={"type": "farm_guardian", "userId": uid},
            )
        else:
            service.capacity(actor, allowance)
            service.add(uid, harvest["cropId"], allowance, harvest["expiresAt"])
            slot.stolen_qty += allowance
            actor.conscience = max(0, actor.conscience - 2)
            insurance = rules.dart_round(price * allowance * POLICY["insuranceFraction"])
            service.wallet(target, insurance, "insurance", event_id)
            result = {"message": f"偷到 {allowance} 個作物", "quantity": allowance, "caught": False}
            # Coalesce into one notification per two-hour window, retain events for return gifts.
            recent = service.db.scalar(
                select(Notification)
                .where(
                    Notification.user_id == target,
                    Notification.category == "farm",
                    Notification.created_at >= service.now - timedelta(hours=2),
                    Notification.title == "農場有人來偷菜",
                )
                .order_by(Notification.id.desc())
                .limit(1)
            )
            payload = {
                "type": "farm_stolen",
                "userId": uid,
                "eventId": event_id,
                "returnGiftUntil": iso(service.now + timedelta(hours=12)),
            }
            if not owner.state.get("notificationSettings", {}).get("stolen", True):
                pass
            elif recent:
                recent.body = "有人來偷菜；主人保底收成與保險補償仍保留。"
                recent.payload_json = payload
            else:
                create_notification(
                    service.db,
                    user_id=target,
                    category="farm",
                    title="農場有人來偷菜",
                    body="前往農場查看，12 小時內可回禮一次。",
                    payload=payload,
                )
        if gift:
            gift.data = {**gift.data, "returned": True}
        event_data.update(quantity=result["quantity"], caught=caught, cropId=harvest["cropId"])
    elif action == "sellTurnips":
        quantity = integer(data.get("quantity"), "quantity", 1)
        host_week = service.turnip(owner)
        price = rules.turnip_quote(host_week, service.now, service.config)
        visitor_week = service.turnip(actor)
        if price is None or visitor_week["quantity"] < quantity:
            fail("MARKET_CLOSED", "現在無法出售大頭菜")
        service.consume(uid, "turnip", quantity)
        service.wallet(
            uid,
            int(price * quantity * (0.9 if actor.conscience < 50 else 1)),
            "visit_turnip",
            event_id,
        )
        visitor_week["quantity"] -= quantity
        actor.turnips = visitor_week
        result = {"message": "已依這座農場當期價格出售大頭菜"}
    elif action == "visit":
        # Footprint only: no wallet, quota or notification. Entering the same farm
        # again inside the window records nothing, so the owner's list stays readable.
        if any(
            e.kind == "visit"
            and e.owner_id == target
            and instant(e.created_at) > service.now - timedelta(minutes=VISIT_FOOTPRINT_MINUTES)
            for e in events
        ):
            return {"message": "", "recorded": False}
        result = {"message": "", "recorded": True}
    elif action == "react":
        emoji = data.get("emoji")
        if emoji not in REACTIONS:
            fail("INVALID_EMOJI", "請選擇提供的貼圖", 400)
        if any(
            e.kind == "react"
            and e.owner_id == target
            and instant(e.created_at) > service.now - timedelta(minutes=5)
            for e in events
        ):
            fail("COOLDOWN", "請稍後再留貼圖")
        event_data["emoji"] = emoji
        result = {"message": "已留下貼圖"}
    else:
        fail("INVALID_ACTION", "無效拜訪動作", 400)
    service.db.add(
        GameEvent(
            id=event_id,
            actor_id=uid,
            owner_id=target,
            kind=action,
            data=event_data,
            created_at=service.now,
        )
    )
    service.db.flush()
    result["snapshot"] = service.snapshot(actor)
    return result


def math_ceil(v):
    import math

    return math.ceil(v)


def build_boards(service):
    """Closed weeks freeze once per board. Wallet receipts make reruns idempotent."""
    db = service.db
    current = week_start(service.now)
    latest = current - timedelta(days=7 if service.now >= current + timedelta(minutes=10) else 14)
    for offset in range(3, -1, -1):
        start = latest - timedelta(weeks=offset)
        key = day(start)
        end = start + timedelta(days=7)
        settlements = list(
            db.scalars(
                select(GameSettlement)
                .join(GameFarm, GameFarm.user_id == GameSettlement.user_id)
                .join(User, User.id == GameFarm.user_id)
                .where(
                    GameSettlement.week == key,
                    GameFarm.enabled.is_(True),
                    User.is_enabled.is_(True),
                )
            )
        )
        if not settlements:
            continue
        valid_count = sum(not s.anomaly_flags and s.data["score"] > 0 for s in settlements)
        for board in BOARDS:
            if db.scalar(
                select(GameLeaderboard.week)
                .where(GameLeaderboard.week == key, GameLeaderboard.board == board)
                .limit(1)
            ):
                continue
            values = []
            for s in settlements:
                farm = service.farm(s.user_id)
                events = (
                    list(
                        db.scalars(
                            select(GameEvent).where(
                                GameEvent.created_at >= start, GameEvent.created_at < end
                            )
                        )
                    )
                    if board != "bookkeeping"
                    else []
                )
                value = (
                    s.data["score"]
                    if board == "bookkeeping"
                    else sum(
                        e.data.get("value", 0)
                        for e in events
                        if e.kind == "harvest" and e.actor_id == farm.user_id
                    )
                    if board == "harvest"
                    else sum(
                        e.kind == "steal"
                        and e.actor_id == farm.user_id
                        and not e.data.get("caught")
                        for e in events
                    )
                    if board == "thief"
                    else sum(
                        e.kind == "steal"
                        and e.owner_id == farm.user_id
                        and e.data.get("caught", False)
                        for e in events
                    )
                )
                detail = json_load(s.data["detailsJson"])
                values.append(
                    (
                        farm,
                        s,
                        value,
                        detail.get("habit", 0),
                        instant(detail.get("achievedAt", s.data["createdAt"])).timestamp(),
                    )
                )
            values.sort(key=lambda x: (-x[2], -x[3], x[4], x[0].user_id))
            for rank, (farm, s, value, habit, _achieved_at) in enumerate(values, 1):
                eligible = (
                    not s.anomaly_flags and valid_count >= POLICY["minimumPlayers"] and value > 0
                )
                coins = 0
                title = None
                if board == "bookkeeping":
                    coins = 200 if value >= 60 else 0
                    if eligible:
                        coins = (
                            1500
                            if rank == 1
                            else 1000
                            if rank <= 3
                            else 500
                            if rank <= max(1, math_ceil(valid_count / 4))
                            else coins
                        )
                        if rank == 1:
                            title = "本週帳王"
                elif board == "harvest" and eligible:
                    coins = (
                        750
                        if rank == 1
                        else 500
                        if rank <= 3
                        else 250
                        if rank <= max(1, math_ceil(valid_count / 4))
                        else 100
                        if value > 0
                        else 0
                    )
                elif board in ("thief", "guardian") and eligible and rank == 1:
                    coins = 300
                    title = "本週神偷" if board == "thief" else "本週守護者"
                data = {
                    "rank": rank,
                    "value": value,
                    "rewardCoins": coins,
                    "title": title,
                    "habit": habit,
                    "anomalyFlags": s.anomaly_flags,
                    "playerCount": valid_count,
                    "validPlayer": not s.anomaly_flags and s.data["score"] > 0,
                }
                db.add(GameLeaderboard(week=key, board=board, user_id=farm.user_id, data=data))
                if coins:
                    service.wallet(farm.user_id, coins, "rank_reward", key + ":" + board)
                if title:
                    farm.title = title
                if eligible and board == "bookkeeping" and rank == 1:
                    from .time import season

                    decor_type = {
                        "spring": "tree_pink",
                        "summer": "tree",
                        "autumn": "tree_orange",
                        "winter": "tree_ice",
                    }[season(start)]
                    state = deepcopy(farm.state)
                    credits = dict(state.get("decorCredits", {}))
                    credits[decor_type] = credits.get(decor_type, 0) + 1
                    state["decorCredits"] = credits
                    farm.state = state
                if eligible and board == "bookkeeping" and rank <= 3:
                    reward_crop = max(
                        service.crops.values(),
                        key=lambda c: (c["minLevel"], c["seedPrice"], c["growthHours"]),
                    )["id"]
                    service.add(farm.user_id, "seed:" + reward_crop, 1)
                if (
                    eligible
                    and board == "bookkeeping"
                    and 3 < rank <= max(1, math_ceil(valid_count / 4))
                ):
                    service.add(farm.user_id, "item:fertilizer", 1)
                if board == "bookkeeping":
                    service.notify(
                        farm,
                        "ranking",
                        category="farm",
                        title="農場週排行榜已公布",
                        body="前往排行榜查看名次與獎勵。",
                        payload={"type": "farm_leaderboard", "week": key, "board": board},
                    )
        db.flush()


def json_load(value):
    import json

    return json.loads(value)


def leaderboard(service, uid, board, week=None):
    if board not in BOARDS:
        fail("INVALID_BOARD", "無效排行榜", 400)
    db = service.db
    weeks = list(
        db.scalars(select(GameLeaderboard.week).distinct().order_by(GameLeaderboard.week.desc()))
    )
    key = week or (weeks[0] if weeks else day(week_start(service.now) - timedelta(days=7)))
    # Weeks are date labels, never arbitrary timestamp/SQL fragments.
    import re

    if not isinstance(key, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        fail("INVALID_WEEK", "無效週次", 400)
    from datetime import date

    try:
        chosen = date.fromisoformat(key)
    except ValueError:
        fail("INVALID_WEEK", "無效週次", 400)
    from ..business_time import to_business_date

    if chosen.weekday() != 0 or chosen >= to_business_date(week_start(service.now)):
        fail("INVALID_WEEK", "請選擇已結束的週一週次", 400)
    rows = list(
        db.scalars(
            select(GameLeaderboard)
            .join(GameFarm, GameFarm.user_id == GameLeaderboard.user_id)
            .join(User, User.id == GameFarm.user_id)
            .where(
                GameLeaderboard.week == key,
                GameLeaderboard.board == board,
                GameFarm.enabled.is_(True),
                User.is_enabled.is_(True),
            )
        )
    )
    rows.sort(key=lambda r: r.data["rank"])
    count = sum(r.data.get("validPlayer", not r.data.get("anomalyFlags")) for r in rows)
    bk = list(
        db.scalars(
            select(GameLeaderboard)
            .join(GameFarm, GameFarm.user_id == GameLeaderboard.user_id)
            .join(User, User.id == GameFarm.user_id)
            .where(
                GameLeaderboard.week == key,
                GameLeaderboard.board == "bookkeeping",
                GameFarm.enabled.is_(True),
                User.is_enabled.is_(True),
            )
        )
    )
    average = sum(r.data["value"] for r in bk) / len(bk) if bk else 0
    items = [
        {
            "rank": r.data["rank"],
            **identity(db, service.farm(r.user_id), True),
            "value": r.data["value"],
            "rewardCoins": r.data["rewardCoins"],
            "title": r.data["title"],
            "isMe": r.user_id == uid,
            "thiefUntil": iso(farm.thief_until)
            if (farm := service.farm(r.user_id)).thief_until
            and instant(farm.thief_until) > service.now
            else None,
        }
        for r in rows
    ]
    return {
        "board": board,
        "week": key,
        "availableWeeks": weeks,
        "playerCount": count,
        "averageScore": average,
        "myBookkeepingScore": next((r.data["value"] for r in bk if r.user_id == uid), None),
        "myValue": next((r["value"] for r in items if r["isMe"]), None),
        "items": items,
        "serverTime": iso(service.now),
    }
