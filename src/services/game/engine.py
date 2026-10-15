"""Atomic server-authoritative farm operations. No client balances or scores are trusted."""

from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from ...models import Ledger, LedgerMember, ReadTxProjection, SyncChange, User
from ...stats_amount import stats_amount
from ..business_time import business_date_start_utc, to_business_date
from ..notifications import create_notification
from . import rules
from .models import (
    GameAction,
    GameEvent,
    GameFarm,
    GameInventory,
    GameListing,
    GameMarketSnapshot,
    GameSettlement,
    GameSlot,
    GameWallet,
    GameWeather,
)

_sqlite_lock = RLock()
BOARDS = ("bookkeeping", "harvest", "thief", "guardian")


def now_utc():
    return datetime.now(timezone.utc)


def instant(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def iso(value):
    return instant(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def day(value):
    return to_business_date(instant(value)).isoformat()


def week_start(now):
    d = to_business_date(instant(now))
    return business_date_start_utc(d - timedelta(days=d.weekday()))


def fail(code, message, status=409):
    raise HTTPException(status, detail={"code": code, "message": message})


def integer(value, name, minimum=0, maximum=10000):
    if type(value) is not int or not minimum <= value <= maximum:
        fail("INVALID_ARGUMENT", f"無效的 {name}", 400)
    return value


@contextmanager
def transaction(db: Session, user_ids):
    """SQLite writer reservation and PostgreSQL ordered xact locks serialize all paths.

    Authentication already opened a read transaction; roll it back before reserving
    the writer. Caller captures the authenticated id first. No game mutations precede this.
    """
    dialect = db.get_bind().dialect.name
    with _sqlite_lock:
        db.rollback()
        try:
            if dialect == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            else:
                for uid in sorted(set(user_ids) | {"__market__"}):
                    db.execute(
                        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                        {"key": "farm:" + uid},
                    )
            yield
            db.commit()
        except Exception:
            db.rollback()
            raise


def validate_nickname(value):
    import unicodedata

    if not isinstance(value, str):
        fail("INVALID_NICKNAME", "請輸入 1～20 個字的農場暱稱", 400)
    value = value.strip()
    if not 1 <= len(value) <= 20 or any(unicodedata.category(c) == "Cc" for c in value):
        fail("INVALID_NICKNAME", "請輸入 1～20 個字的農場暱稱", 400)
    return value


class FarmService:
    def __init__(self, db, now=None):
        self.db = db
        self.now = instant(now or now_utc())
        self.config = rules.load_config()
        self.crops = {c["id"]: c for c in self.config["crops"]}
        self.items = {i["id"]: i for i in self.config["items"]}

    def farm(self, uid, enabled=True):
        farm = self.db.get(GameFarm, uid)
        if (
            farm is None
            or not (user := self.db.get(User, uid))
            or not user.is_enabled
            or (enabled and not farm.enabled)
        ):
            fail("FARM_NOT_FOUND", "農場尚未啟用", 404)
        return farm

    def slots(self, uid):
        return list(
            self.db.scalars(select(GameSlot).where(GameSlot.user_id == uid).order_by(GameSlot.idx))
        )

    def inventory(self, uid):
        return list(
            self.db.scalars(
                select(GameInventory)
                .where(GameInventory.user_id == uid)
                .order_by(GameInventory.expires_at, GameInventory.id)
            )
        )

    def balance(self, uid):
        self.db.flush()
        return int(
            self.db.scalar(
                select(func.coalesce(func.sum(GameWallet.delta), 0)).where(
                    GameWallet.user_id == uid
                )
            )
        )

    def wallet(self, uid, delta, reason, ref=None):
        self.db.flush()
        ref = ref or str(uuid4())
        prior = self.db.scalar(
            select(GameWallet).where(
                GameWallet.user_id == uid, GameWallet.reason == reason, GameWallet.ref_id == ref
            )
        )
        if prior:
            return
        if delta < 0 and self.balance(uid) < -delta:
            fail("INSUFFICIENT_COINS", "好幣不足")
        self.db.add(
            GameWallet(user_id=uid, delta=delta, reason=reason, ref_id=ref, created_at=self.now)
        )
        self.db.flush()

    def add(self, uid, item, quantity, expires=None):
        integer(quantity, "quantity", 1, 100000)
        self.db.add(
            GameInventory(
                user_id=uid,
                item_id=item,
                quantity=quantity,
                expires_at=instant(expires) if expires else None,
            )
        )
        self.db.flush()

    def consume(self, uid, item, quantity):
        integer(quantity, "quantity", 1)
        rows = [r for r in self.inventory(uid) if r.item_id == item]
        if sum(r.quantity for r in rows) < quantity:
            fail("INSUFFICIENT_STOCK", "庫存不足")
        for row in rows:
            take = min(quantity, row.quantity)
            quantity -= take
            if take == row.quantity:
                self.db.delete(row)
            else:
                row.quantity -= take
            if not quantity:
                break
        self.db.flush()

    def capacity(self, farm, added):
        used = sum(r.quantity for r in self.inventory(farm.user_id) if r.expires_at is not None)
        from .layout import storage_capacity

        if used + added > farm.state["capacity"] + storage_capacity(farm.layout):
            fail("STORAGE_FULL", "請先賣出或擴充倉庫")

    def _eligible_ledger(self, ledger, uid):
        if ledger is None or ledger.user_id != uid:
            return False
        # Every Cloud ledger has an owner membership, including private ledgers.
        # Only another user's membership makes it shared.
        if self.db.scalar(
            select(LedgerMember.user_id)
            .where(LedgerMember.ledger_id == ledger.id, LedgerMember.user_id != ledger.user_id)
            .limit(1)
        ):
            return False
        # Cloud keeps soft-deleted ledger rows for sync/audit; mirror the read
        # endpoint's latest ledger_snapshot tombstone contract without importing UI.
        action = self.db.scalar(
            select(SyncChange.action)
            .where(SyncChange.ledger_id == ledger.id, SyncChange.entity_type == "ledger_snapshot")
            .order_by(SyncChange.change_id.desc())
            .limit(1)
        )
        return action != "delete"

    def ledger(self, uid, external_id=None):
        if external_id is None:
            return None
        ledger = self.db.scalar(
            select(Ledger).where(Ledger.user_id == uid, Ledger.external_id == external_id)
        )
        if not self._eligible_ledger(ledger, uid):
            fail("INELIGIBLE_LEDGER", "請先同步自己的非共享帳本，或改選其他帳本", 400)
        return ledger.id

    def eligible(self, farm):
        ledger = self.db.get(Ledger, farm.scoring_ledger_id) if farm.scoring_ledger_id else None
        return self._eligible_ledger(ledger, farm.user_id)

    def entries(self, farm):
        if not self.eligible(farm):
            return []
        rows = list(
            self.db.scalars(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == farm.scoring_ledger_id,
                    or_(
                        ReadTxProjection.happened_at >= week_start(self.now) - timedelta(weeks=13),
                        ReadTxProjection.client_created_at
                        >= week_start(self.now) - timedelta(weeks=13),
                    ),
                )
            )
        )
        refunded = set(
            self.db.scalars(
                select(ReadTxProjection.refund_of_sync_id).where(
                    ReadTxProjection.ledger_id == farm.scoring_ledger_id,
                    ReadTxProjection.refund_of_sync_id.is_not(None),
                )
            )
        )
        result = []
        for tx in rows:
            # Keep the immutable client creation instant. Arrival eligibility is
            # evaluated per settlement week; an old import must not become a new day.
            created = instant(tx.client_created_at) if tx.client_created_at else None
            seen = instant(tx.first_seen_at) if tx.first_seen_at else None
            amount = max(0, stats_amount(tx.amount, tx.native_amount, tx.debt_split_amount))
            result.append(
                {
                    "happenedAt": iso(tx.happened_at),
                    "createdAt": iso(created) if created else None,
                    "firstSeenAt": iso(seen) if seen else None,
                    "type": tx.tx_type,
                    "amount": amount,
                    "budgetAmount": 0 if tx.exclude_from_budget else amount,
                    "excludeFromStats": tx.exclude_from_stats,
                    "isRefund": bool(tx.refund_of_sync_id) or tx.sync_id in refunded,
                }
            )
        return result

    def advance(self, farm):
        if not farm.enabled:
            return
        state = deepcopy(farm.state)
        if instant(state["lastSeenAt"]) > self.now:
            fail("INVALID_CLOCK", "農場時間異常")
        slots = self.slots(farm.user_id)
        hours = [
            r.data
            for r in self.db.scalars(
                select(GameWeather).where(
                    GameWeather.city_code == state["cityCode"],
                    GameWeather.hour <= self.now,
                    GameWeather.hour >= self.now - timedelta(days=30),
                )
            )
        ]
        if state.get("debugWeather"):
            hours = [
                {**state["debugWeather"], "at": iso(
                    self.now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=i)
                )}
                for i in range(721)
            ]
        for slot in slots:
            slot.plot = rules.advance(slot.plot, self.now, hours, self.config)
        self.db.execute(
            delete(GameInventory).where(
                GameInventory.user_id == farm.user_id, GameInventory.expires_at <= self.now
            )
        )
        for listing in self.db.scalars(
            select(GameListing).where(GameListing.user_id == farm.user_id)
        ):
            data = listing.data
            if (
                instant(data["sellsAt"]) <= self.now
                and instant(data["sellsAt"]) < instant(data["expiresAt"])
                and self.db.get(GameEvent, data["listingId"]) is None
            ):
                self.db.add(
                    GameEvent(
                        id=data["listingId"],
                        actor_id=farm.user_id,
                        owner_id=farm.user_id,
                        kind="market_sale",
                        data={"cropId": data["cropId"], "quantity": data["quantity"]},
                        created_at=instant(data["sellsAt"]),
                    )
                )
            if instant(data["expiresAt"]) <= self.now and instant(data["expiresAt"]) <= instant(
                data["sellsAt"]
            ):
                self.db.delete(listing)
        entries = self.entries(farm)
        eligible = self.eligible(farm)
        if (
            farm.scoring_ledger_id
            and not eligible
            and state.get("scoringUnavailableFor") != farm.scoring_ledger_id
        ):
            self.notify(
                farm,
                "settlement",
                category="farm",
                title="農場計分帳本需要重選",
                body="計分帳本已共享或刪除，請到 App 農場 → 設定改選自己的非共享帳本。",
                payload={"type": "farm_scoring_ledger"},
            )
            state["scoringUnavailableFor"] = farm.scoring_ledger_id
        elif eligible:
            state.pop("scoringUnavailableFor", None)
        today = day(self.now)
        new_day = today != state["waterDay"]
        recorded = any(
            e["createdAt"]
            and e["type"] in ("expense", "income")
            and not e["excludeFromStats"]
            and not e["isRefund"]
            and day(e["createdAt"]) == today
            and instant(e["createdAt"]) <= self.now
            for e in entries
        )
        claimed = state["waterBonus"] and not new_day
        state["waterRemaining"] = (
            self.config["care"]["dailyWater"] if new_day else state["waterRemaining"]
        ) + (self.config["care"]["bookkeepingWater"] if recorded and not claimed else 0)
        state["waterBonus"] = claimed or recorded
        # Conscience recovers once per elapsed civil day, never per read.
        elapsed = (to_business_date(self.now) - to_business_date(instant(state["lastSeenAt"]))).days
        farm.conscience = min(100, farm.conscience + max(0, elapsed) * 3)
        # Live turnip quantity follows actual unexpired inventory. Old Sunday batches
        # disappear at 05:00, not when the new purchase day begins at midnight.
        if farm.turnips:
            tw = deepcopy(farm.turnips)
            tw["quantity"] = sum(
                r.quantity for r in self.inventory(farm.user_id) if r.item_id == "turnip"
            )
            farm.turnips = tw
        self.reminders(farm, state, slots)
        state.update(waterDay=today, lastSeenAt=iso(self.now))
        farm.state = state
        self.settle(farm, entries)
        self.db.flush()

    def reminders(self, farm, state, slots):
        from .time import civil, next_season, season

        announced = state.get("readyAnnounced", {})
        next_announced = {}
        for slot in slots:
            p = slot.plot
            if rules.ready(p):
                key = f"{p['playerId']}:{p['plantedAt']}:{p['harvests']}"
                next_announced[str(slot.idx)] = key
                if announced.get(str(slot.idx)) != key:
                    self.notify(
                        farm,
                        "harvest",
                        category="farm",
                        title="農場有作物可以收成",
                        body="有成熟作物正在等你。",
                        payload={"type": "farm_harvest", "index": slot.idx},
                    )
        state["readyAnnounced"] = next_announced
        current = season(self.now)
        last = state.get("lastSeason", current)
        if last != current:
            self.notify(
                farm,
                "season",
                category="farm",
                title="農場已換季",
                body="請留意作物季節與保護道具。",
                payload={"type": "farm_season"},
            )
        state["lastSeason"] = current
        remaining = (to_business_date(next_season(self.now)) - to_business_date(self.now)).days
        key = f"{current}:{remaining}"
        if (
            remaining in (3, 1)
            and civil(self.now).hour >= 20
            and state.get("seasonReminder") != key
            and any(s.plot["cropId"] and not s.plot["withered"] for s in slots)
        ):
            self.notify(
                farm,
                "season",
                category="farm",
                title="農場即將換季",
                body=f"距離換季還有 {remaining} 天，請留意尚未收成的作物。",
                payload={"type": "farm_season", "days": remaining},
            )
            state["seasonReminder"] = key

    def settle(self, farm, entries=None):
        if not farm.enabled or not self.eligible(farm):
            return
        old = {
            r.week: r
            for r in self.db.scalars(
                select(GameSettlement).where(GameSettlement.user_id == farm.user_id)
            )
        }
        entries = entries if entries is not None else self.entries(farm)
        for week in rules.due_weeks(
            instant(farm.state["enabledAt"]), self.now, self.config, old.keys()
        ):
            key = day(week)
            previous = old.get(day(week - timedelta(days=7)))
            # Offline arrival has a 48-hour grace only until this result freezes.
            # A previously frozen week is never recomputed or paid again.
            deadline = week + timedelta(days=7, hours=48)
            scoring_entries = [
                {
                    **e,
                    "createdAt": e["createdAt"]
                    if e.get("firstSeenAt") and instant(e["firstSeenAt"]) <= deadline
                    else None,
                }
                for e in entries
            ]
            score = rules.score(
                week,
                scoring_entries,
                self.config,
                previous_streak=previous.data["streak"] if previous else 0,
            )
            details = {k: score.get(k) for k in ("habit", "control", "saving", "budget")}
            details.update(
                weights=self.config["scoring"]["weights"], configVersion=self.config["version"]
            )
            recorded = [
                instant(e["createdAt"])
                for e in scoring_entries
                if e["createdAt"]
                and week <= instant(e["createdAt"]) < week + timedelta(days=7)
                and e["type"] in ("expense", "income")
                and not e["excludeFromStats"]
                and not e["isRefund"]
            ]
            details["achievedAt"] = iso(max(recorded)) if recorded else iso(self.now)

            if hasattr(rules, "reward_breakdown"):
                details["reward"] = rules.reward_breakdown(
                    score["score"], score["streak"], self.config
                )
            data = {
                "week": key,
                "score": score["score"],
                "coins": score["coins"],
                "streak": score["streak"],
                "detailsJson": json.dumps(details),
                "createdAt": iso(self.now),
            }
            end = week + timedelta(days=7)
            flags = []
            if (
                sum(bool(e["createdAt"] and week <= instant(e["createdAt"]) < end) for e in entries)
                > 300
            ):
                flags.append("too_many_transactions")
            recent_income = sum(
                e["amount"]
                for e in entries
                if e["type"] == "income"
                and end - timedelta(days=28) <= instant(e["happenedAt"]) < end
            )
            prior_income = (
                sum(
                    e["amount"]
                    for e in entries
                    if e["type"] == "income"
                    and week - timedelta(weeks=8) <= instant(e["happenedAt"]) < week
                )
                / 2
            )
            if prior_income > 0 and recent_income > prior_income * 10:
                flags.append("income_spike")
            row = GameSettlement(user_id=farm.user_id, week=key, data=data, anomaly_flags=flags)
            self.db.add(row)
            old[key] = row
            self.notify(
                farm,
                "settlement",
                category="farm",
                title="農場週結算已完成",
                body="前往農場查看記帳表現並領取好幣。",
                payload={"type": "farm_settlement", "week": key},
            )

    def notify(self, farm, event, **kwargs):
        settings = farm.state.get("notificationSettings", {})
        if settings.get(event, event != "harvest"):
            return create_notification(self.db, user_id=farm.user_id, **kwargs)
        return None

    def create(self, uid, data):
        existing = self.db.get(GameFarm, uid)
        if existing:
            if data.get("importSave") is not None:
                fail("FARM_EXISTS", "雲端已有農場，單機農場不會合併")
            existing.enabled = True
            state = deepcopy(existing.state)
            state["enabled"] = True
            state["enabledAt"] = (
                iso(self.now) if not existing.state["enabled"] else state["enabledAt"]
            )
            existing.state = state
            self.advance(existing)
            return self.snapshot(existing)
        scoring_id = self.ledger(uid, data.get("scoringLedgerId"))
        state = {
            "id": 1,
            "mode": "cloud",
            "enabled": True,
            "enabledAt": iso(self.now),
            "lastSeenAt": iso(self.now),
            "playerId": uid,
            "cityCode": "taipei",
            "scoringLedgerId": None,
            "configVersion": self.config["version"],
            "xp": 0,
            "capacity": self.config["progression"]["initialCapacity"],
            "waterDay": day(self.now),
            "waterRemaining": self.config["care"]["dailyWater"],
            "waterBonus": False,
        }
        if "nickname" in data:
            state["nickname"] = validate_nickname(data["nickname"])
        farm = GameFarm(
            user_id=uid,
            enabled=True,
            state=state,
            scoring_ledger_id=scoring_id,
            anonymous=False,
            conscience=100,
            newbie_until=self.now + timedelta(days=7),
            created_at=self.now,
        )
        self.db.add(farm)
        self.db.flush()
        save = data.get("importSave")
        if save is None:
            slots = [rules.empty_plot(i, self.now) for i in range(2)]
            coins = self.config["progression"]["initialCoins"]
        else:
            slots, coins = self.import_save(farm, save)
        for p in slots:
            self.db.add(GameSlot(user_id=uid, idx=p["index"], plot=p, stolen_qty=0))
        self.wallet(uid, coins, "initial", "initial")
        self.db.flush()
        self.advance(farm)
        return self.snapshot(farm)

    def import_save(self, farm, save):
        from .layout import validate_layout

        if not isinstance(save, dict) or save.get("saveVersion") != 1:
            fail("INVALID_SAVE", "不支援的單機存檔", 400)
        state = save.get("state", {})
        if state.get("mode") != "local" or state.get("configVersion") != self.config["version"]:
            fail("INVALID_SAVE", "無效單機農場", 400)
        slots = []
        raw_slots = save.get("slots", [])
        if not isinstance(raw_slots, list) or not 1 <= len(raw_slots) <= 24:
            fail("INVALID_SAVE", "無效田地數量", 400)
        for raw in raw_slots:
            p = json.loads(raw["plotJson"])
            expected = rules.empty_plot(0, self.now)
            if not isinstance(p, dict) or set(expected) - set(p):
                fail("INVALID_SAVE", "田地欄位不完整", 400)
            if not isinstance(p["playerId"], str) or len(p["playerId"]) > 64:
                fail("INVALID_SAVE", "無效田地主人", 400)
            for flag in ("weeds", "pests", "windDamaged", "withered"):
                if type(p[flag]) is not bool:
                    fail("INVALID_SAVE", "無效田地旗標", 400)
            integer(p["harvests"], "harvests", 0, 3)
            integer(p["seasonPasses"], "seasonPasses", 0, 2)
            if not isinstance(p["effects"], list) or len(p["effects"]) > len(self.items):
                fail("INVALID_SAVE", "無效道具效果", 400)
            effect_ids = set()
            for effect in p["effects"]:
                if (
                    not isinstance(effect, dict)
                    or effect.get("id") in effect_ids
                    or type(effect.get("seasonUsed")) is not bool
                ):
                    fail("INVALID_SAVE", "無效道具效果", 400)
                effect_ids.add(effect.get("id"))
            if (p["cropId"] is None) != (p["plantedAt"] is None):
                fail("INVALID_SAVE", "作物與播種日期不一致", 400)
            integer(p["index"], "index", 0, 23)
            if p.get("saveVersion") != 1 or p.get("cropId") not in (None, *self.crops):
                fail("INVALID_SAVE", "無效作物", 400)
            if instant(p["lastAdvancedAt"]) > self.now + timedelta(minutes=5) or (
                p.get("plantedAt") and instant(p["plantedAt"]) > self.now
            ):
                fail("INVALID_SAVE", "單機農場的時間超前，請等待真實時間再搬遷", 400)
            for key in ("growth", "moisture", "dryHours"):
                val = p.get(key)
                if type(val) not in (int, float) or not math.isfinite(val) or val < 0:
                    fail("INVALID_SAVE", "無效作物狀態", 400)
            if p["growth"] > 1 or p["moisture"] > 100:
                fail("INVALID_SAVE", "無效作物狀態", 400)
            p["lastAdvancedAt"] = iso(min(self.now, instant(p["lastAdvancedAt"])))
            for effect in p.get("effects", []):
                if effect.get("id") not in self.items:
                    fail("INVALID_SAVE", "無效道具", 400)
                instant(effect["expiresAt"])
            slots.append(p)
        if sorted(p["index"] for p in slots) != list(range(len(slots))):
            fail("INVALID_SAVE", "無效田地索引", 400)
        layout = save.get("layout")
        if layout:
            layout = json.loads(layout) if isinstance(layout, str) else layout
            layout = rules.normalize_layout(layout)
            validate_layout(layout, [p["index"] for p in slots])
            farm.layout = layout
        new = deepcopy(farm.state)
        if isinstance(state.get("playerId"), str) and 1 <= len(state["playerId"]) <= 64:
            new["playerId"] = state["playerId"]
        new["xp"] = integer(state.get("xp", 0), "xp", 0, 100000)
        new["capacity"] = integer(state.get("capacity", 10), "capacity", 10, 10000)
        new["cityCode"] = state.get("cityCode", "taipei")
        from .weather import city_coordinates

        city_coordinates(new["cityCode"])
        farm.state = new
        for row in save.get("inventory", []):
            item = row.get("itemId")
            quantity = integer(row.get("quantity"), "quantity", 1)
            if (
                item not in self.crops
                and item != "turnip"
                and item not in ["seed:" + c for c in self.crops]
                and item not in ["item:" + i for i in self.items]
            ):
                fail("INVALID_SAVE", "無效庫存", 400)
            expiry = instant(row["expiresAt"]) if row.get("expiresAt") is not None else None
            if item in (*self.crops, "turnip") and expiry is None:
                fail("INVALID_SAVE", "收成缺少到期時間", 400)
            if expiry is None or expiry > self.now:
                self.add(farm.user_id, item, quantity, expiry)
        listings = save.get("listings", [])
        if not isinstance(listings, list) or len(listings) > 3:
            fail("INVALID_SAVE", "無效市場攤位", 400)
        seen_slots = set()
        for data in listings:
            slot = integer(data.get("slot"), "slot", 0, 2)
            if slot in seen_slots:
                fail("INVALID_SAVE", "重複攤位", 400)
            seen_slots.add(slot)
            crop = data.get("cropId")
            if crop not in self.crops and crop != "turnip":
                fail("INVALID_SAVE", "無效上架作物", 400)
            qty = integer(data.get("quantity"), "quantity", 1)
            price = integer(data.get("unitPrice"), "unitPrice", 1, 10000)
            listed = instant(data["listedAt"])
            sold = instant(data["sellsAt"])
            expires = instant(data["expiresAt"])
            if (
                listed > self.now
                or not 60 <= (sold - listed).total_seconds() <= 300
                or expires <= listed
            ):
                fail("INVALID_SAVE", "無效攤位時間", 400)
            if expires > self.now or sold < expires:
                self.db.add(
                    GameListing(
                        user_id=farm.user_id,
                        slot=slot,
                        data={
                            "slot": slot,
                            "listingId": str(uuid4()),
                            "cropId": crop,
                            "quantity": qty,
                            "unitPrice": price,
                            "listedAt": iso(listed),
                            "sellsAt": iso(sold),
                            "expiresAt": iso(expires),
                        },
                    )
                )
        imported_turnips = sum(
            r.quantity for r in self.inventory(farm.user_id) if r.item_id == "turnip"
        )
        active_rows = []
        for raw in save.get("turnips", []):
            starts = instant(raw["startsAt"])
            if starts + timedelta(days=7) > self.now:
                active_rows.append(raw)
        if imported_turnips or active_rows:
            # Keep the live batch's server-reconstructed seeded prices, never client future prices.
            current = rules.turnip_generate(farm.state["playerId"], self.now, self.config)
            raw = next(
                (r for r in active_rows if instant(r["startsAt"]) == instant(current["weekStart"])),
                None,
            )
            bought = integer(
                raw.get("bought", imported_turnips) if raw else imported_turnips,
                "bought",
                0,
                self.config["turnips"]["maximumQuantity"],
            )
            if imported_turnips > self.config["turnips"]["maximumQuantity"]:
                fail("INVALID_SAVE", "大頭菜超過每週上限", 400)
            farm.turnips = {
                **current,
                "quantity": imported_turnips,
                "bought": max(bought, imported_turnips),
            }
        self.capacity(farm, 0)
        total = sum(
            integer(abs(r.get("delta", 0)), "delta", 0, 100000000)
            * (1 if r.get("delta", 0) >= 0 else -1)
            for r in save.get("wallet", [])
        )
        if total < 0:
            fail("INVALID_SAVE", "無效錢包", 400)
        coins = min(total, self.config["progression"]["cloudMigrationCoinCap"])
        if total > coins:
            farm.title = "開墾先驅"
        return slots, coins

    def snapshot(self, farm):
        self.db.flush()
        hours = list(
            self.db.scalars(
                select(GameWeather).where(
                    GameWeather.city_code == farm.state["cityCode"],
                    GameWeather.hour == self.now.replace(minute=0, second=0, microsecond=0),
                )
            )
        )
        return {
            "ownerId": farm.user_id,
            "state": {**farm.state, "enabled": farm.enabled},
            "balance": self.balance(farm.user_id),
            "water": {
                "day": farm.state["waterDay"],
                "remaining": farm.state["waterRemaining"],
                "bonus": farm.state["waterBonus"],
            },
            "scoringAvailable": self.eligible(farm),
            "weather": farm.state.get("debugWeather") or (hours[0].data if hours else None),
            "layout": farm.layout,
            "plots": [s.plot for s in self.slots(farm.user_id)],
            "inventory": [
                {
                    "id": r.id,
                    "itemId": r.item_id,
                    "quantity": r.quantity,
                    "expiresAt": iso(r.expires_at) if r.expires_at else None,
                }
                for r in self.inventory(farm.user_id)
            ],
            "settlements": [
                s.data
                for s in self.db.scalars(
                    select(GameSettlement)
                    .where(GameSettlement.user_id == farm.user_id)
                    .order_by(GameSettlement.week.desc())
                )
            ],
            "claimedSettlementWeeks": list(
                self.db.scalars(
                    select(GameWallet.ref_id).where(
                        GameWallet.user_id == farm.user_id, GameWallet.reason == "settlement"
                    )
                )
            ),
            "listings": [
                r.data
                for r in self.db.scalars(
                    select(GameListing)
                    .where(GameListing.user_id == farm.user_id)
                    .order_by(GameListing.slot)
                )
            ],
            "scoringLedgerSyncId": (
                self.db.get(Ledger, farm.scoring_ledger_id).external_id
                if farm.scoring_ledger_id and self.db.get(Ledger, farm.scoring_ledger_id)
                else None
            ),
            "anonymousOnBoard": farm.anonymous,
            "social": {
                "nickname": farm.state.get("nickname"),
                "nicknameRequired": not bool(farm.state.get("nickname")),
                "canCheat": bool(self.db.get(User, farm.user_id).is_admin),
                "conscience": farm.conscience,
                "newbieUntil": iso(farm.newbie_until),
                "guardDogOwned": farm.state.get("guardDogOwned", False),
                "guardDogUntil": farm.state.get("guardDogUntil"),
                "thiefUntil": iso(farm.thief_until) if farm.thief_until else None,
                "title": farm.title,
                "decorCredits": farm.state.get("decorCredits", {}),
                "notificationSettings": farm.state.get(
                    "notificationSettings",
                    {
                        "settlement": True,
                        "ranking": True,
                        "stolen": True,
                        "guardian": True,
                        "harvest": False,
                        "season": True,
                    },
                ),
            },
            "serverTime": iso(self.now),
        }

    def patch(self, farm, data):
        # Settings are actions too: elapsed time belongs to the old city/ledger.
        # Apply it before changing settings so new weather cannot rewrite the past.
        if farm.enabled:
            self.advance(farm)
        state = deepcopy(farm.state)
        if "nickname" in data:
            state["nickname"] = validate_nickname(data["nickname"])
        if "enabled" in data:
            if type(data["enabled"]) is not bool:
                fail("INVALID_ARGUMENT", "enabled 必須為布林值", 400)
            if data["enabled"] and not farm.enabled:
                state["enabledAt"] = iso(self.now)
            farm.enabled = data["enabled"]
            state["enabled"] = farm.enabled
        if "cityCode" in data:
            from .weather import city_coordinates

            city_coordinates(data["cityCode"])
            state["cityCode"] = data["cityCode"]
        if "scoringLedgerId" in data:
            farm.scoring_ledger_id = self.ledger(farm.user_id, data["scoringLedgerId"])
        if "notificationSettings" in data:
            settings = data["notificationSettings"]
            keys = {"settlement", "ranking", "stolen", "guardian", "harvest", "season"}
            if (
                not isinstance(settings, dict)
                or not set(settings) <= keys
                or not all(type(v) is bool for v in settings.values())
            ):
                fail("INVALID_ARGUMENT", "無效通知設定", 400)
            state["notificationSettings"] = {**state.get("notificationSettings", {}), **settings}
        if "anonymousOnBoard" in data:
            if type(data["anonymousOnBoard"]) is not bool:
                fail("INVALID_ARGUMENT", "anonymousOnBoard 必須為布林值", 400)
            farm.anonymous = data["anonymousOnBoard"]
        farm.state = state
        self.advance(farm)
        return self.snapshot(farm)

    def action(self, farm, data):
        from .layout import (
            default_layout,
            first_free,
            layout_cost,
            storage_capacity,
            validate_layout,
        )

        uid = farm.user_id
        self.advance(farm)
        kind = data["action"]
        state = deepcopy(farm.state)
        q = data.get("quantity")
        if q is not None:
            integer(q, "quantity", 1)
        if kind in ("buySeeds", "buyItem"):
            key = data.get("cropId") if kind == "buySeeds" else data.get("itemId")
            obj = (self.crops if kind == "buySeeds" else self.items).get(key)
            if not obj:
                fail("INVALID_ITEM", "無效物品", 400)
            if (
                kind == "buySeeds"
                and obj["minLevel"] > rules.level(state["xp"], self.config)
            ):
                fail("LEVEL_REQUIRED", "等級尚未解鎖")
            integer(q, "quantity", 1)
            self.wallet(
                uid,
                -q * obj["seedPrice" if kind == "buySeeds" else "price"],
                "buy_seed" if kind == "buySeeds" else "buy_item",
            )
            self.add(uid, ("seed:" if kind == "buySeeds" else "item:") + key, q)
        elif kind in ("plant", "water", "weed", "removePests", "clear", "useItem", "harvest"):
            idx = integer(data.get("index"), "index", 0, 23)
            slot = self.db.get(GameSlot, (uid, idx))
            if not slot:
                fail("INVALID_PLOT", "找不到田地", 404)
            p = slot.plot
            if kind == "plant":
                crop = data.get("cropId")
                p = rules.plant(
                    p,
                    crop,
                    self.now,
                    state["playerId"],
                    self.config,
                    level=rules.level(state["xp"], self.config),
                )
                self.consume(uid, "seed:" + crop, 1)
                slot.stolen_qty = 0
            elif kind == "harvest":
                result = rules.harvest(p, self.now, self.config)
                qty = result["quantity"] - slot.stolen_qty
                if qty > 0:
                    self.capacity(farm, qty)
                    self.add(uid, result["cropId"], qty, result["expiresAt"])
                if result["seedReward"]:
                    self.add(uid, "seed:" + result["cropId"], result["seedReward"])
                state["xp"] += result["xp"]
                p = result["plot"]
                slot.stolen_qty = 0
                value = qty * self.market_prices()[result["cropId"]]
                self.db.add(
                    GameEvent(
                        id=str(uuid4()),
                        actor_id=uid,
                        owner_id=uid,
                        kind="harvest",
                        data={"value": value},
                        created_at=self.now,
                    )
                )
            elif kind == "useItem":
                item = data.get("itemId")
                spec = self.items.get(item)
                if not spec:
                    fail("INVALID_ITEM", "無效道具", 400)
                if not p["cropId"] or p["withered"]:
                    fail("EMPTY_PLOT", "田地沒有活的作物")
                self.consume(uid, "item:" + item, 1)
                layout = farm.layout or default_layout([s.idx for s in self.slots(uid)])
                positions = layout["plots"]
                center = positions[str(idx)]
                for target in self.slots(uid):
                    pos = positions[str(target.idx)]
                    if (
                        target.plot["cropId"]
                        and not target.plot["withered"]
                        and (
                            spec["area"] == 24
                            or target.idx == idx
                            or spec["area"] == 9
                            and abs(pos["x"] - center["x"]) <= 48
                            and abs(pos["y"] - center["y"]) <= 48
                        )
                    ):
                        target.plot = rules.use_item(target.plot, item, self.now, self.config)
                p = slot.plot
            else:
                if kind == "water":
                    if state["waterRemaining"] <= 0:
                        fail("NO_WATER", "今天的水壺已用完")
                    state["waterRemaining"] -= 1
                p = rules.care(p, kind, self.config)
                if kind == "clear":
                    slot.stolen_qty = 0
            slot.plot = p
        elif kind in ("sell", "listForSale"):
            crop = data.get("cropId")
            if crop not in self.crops and crop != "turnip":
                fail("INVALID_ITEM", "無效作物", 400)
            integer(q, "quantity", 1)
            batches = [r for r in self.inventory(uid) if r.item_id == crop]
            if sum(r.quantity for r in batches) < q:
                fail("INSUFFICIENT_STOCK", "庫存不足")
            price = self.market_prices().get(crop)
            if crop == "turnip":
                tw = self.turnip(farm)
                price = rules.turnip_quote(tw, self.now, self.config)
                if price is None or tw["quantity"] < q:
                    fail("MARKET_CLOSED", "現在不能賣大頭菜")
                tw["quantity"] -= q
                farm.turnips = tw
            price = math.floor(price * (0.9 if farm.conscience < 50 else 1))
            if kind == "sell":
                self.consume(uid, crop, q)
                self.wallet(uid, price * q, "sell")
                self.db.add(
                    GameEvent(
                        id=str(uuid4()),
                        actor_id=uid,
                        owner_id=uid,
                        kind="market_sale",
                        data={"cropId": crop, "quantity": q},
                        created_at=self.now,
                    )
                )
            else:
                slot = integer(data.get("slot"), "slot", 0, 2)
                if self.db.get(GameListing, (uid, slot)):
                    fail("STALL_BUSY", "攤位已上架")
                remaining = q
                expiry = None
                for b in batches:
                    if remaining <= 0:
                        break
                    if b.expires_at is None:
                        fail("INVALID_STOCK", "無效收成")
                    expiry = min(expiry, instant(b.expires_at)) if expiry else instant(b.expires_at)
                    remaining -= b.quantity
                self.consume(uid, crop, q)
                listing_id = str(uuid4())
                delay = 60 + int(hashlib.sha256(listing_id.encode()).hexdigest()[:8], 16) % 241
                self.db.add(
                    GameListing(
                        user_id=uid,
                        slot=slot,
                        data={
                            "slot": slot,
                            "listingId": listing_id,
                            "cropId": crop,
                            "quantity": q,
                            "unitPrice": price,
                            "listedAt": iso(self.now),
                            "sellsAt": iso(self.now + timedelta(seconds=delay)),
                            "expiresAt": iso(expiry),
                        },
                    )
                )
        elif kind == "collectListing":
            listing = self.db.get(GameListing, (uid, integer(data.get("slot"), "slot", 0, 2)))
            if (
                not listing
                or instant(listing.data["sellsAt"]) > self.now
                or instant(listing.data["sellsAt"]) >= instant(listing.data["expiresAt"])
            ):
                fail("NOT_SOLD", "攤位尚未成交")
            d = listing.data
            self.wallet(uid, d["unitPrice"] * d["quantity"], "market_sale", d["listingId"])
            self.db.delete(listing)
        elif kind == "claimSettlement":
            row = self.db.get(GameSettlement, (uid, data.get("week")))
            if not row:
                fail("NO_SETTLEMENT", "找不到週結算", 404)
            if row.data["coins"] > 0:
                self.wallet(uid, row.data["coins"], "settlement", row.week)
        elif kind in ("guardDog", "feedGuardDog"):
            from .social import POLICY

            if kind == "feedGuardDog" and not state.get("guardDogOwned"):
                fail("NO_GUARD_DOG", "請先購買看門狗")
            if kind == "guardDog" and state.get("guardDogOwned"):
                fail("GUARD_DOG_EXISTS", "已經有看門狗，請補狗糧")
            self.wallet(
                uid, -POLICY["dogPrice" if kind == "guardDog" else "feedPrice"], "guard_dog"
            )
            state["guardDogOwned"] = True
            state["guardDogUntil"] = iso(self.now + timedelta(hours=POLICY["dogHours"]))
        elif kind == "expandStorage":
            opts = self.config["progression"]
            self.wallet(uid, -opts["storageExpansionPrice"], "expand_storage")
            state["capacity"] += opts["storageExpansionQuantity"]
        elif kind == "expandPlots":
            opts = self.config["progression"]
            slots = self.slots(uid)
            n = len(slots)
            level = rules.level(state["xp"], self.config)
            if (
                n >= opts["maximumPlots"]
                or n >= opts["initialPlots"] + (level - 1) * opts["plotsPerLevel"]
            ):
                fail("LEVEL_REQUIRED", "等級不足或田地已達上限")
            price = rules.expansion_price(n, self.config)
            layout = deepcopy(farm.layout or default_layout([s.idx for s in slots]))
            pos = first_free(layout, 32, 32)
            if pos is None:
                fail("NO_SPACE", "請先騰出田地空間")
            layout["plots"][str(n)] = pos
            farm.layout = layout
            self.wallet(uid, -price, "expand_plot")
            self.db.add(
                GameSlot(user_id=uid, idx=n, plot=rules.empty_plot(n, self.now), stolen_qty=0)
            )
        elif kind == "saveLayout":
            layout = rules.normalize_layout(data.get("layout"))
            slots = self.slots(uid)
            validate_layout(layout, [s.idx for s in slots])
            old = farm.layout or default_layout([s.idx for s in slots])
            old_types = {d["id"]: d["type"] for d in old.get("decor", [])}
            for d in layout.get("decor", []):
                before = old_types.get(d["id"])
                if (
                    before
                    and before != d["type"]
                    and not (before.startswith("home") and d["type"].startswith("home"))
                ):
                    fail("INVALID_LAYOUT", "換物品類型請先移除再放置", 400)
            used = sum(r.quantity for r in self.inventory(uid) if r.expires_at)
            if used > state["capacity"] + storage_capacity(layout):
                fail("STORAGE_FULL", "移除板條箱後空間不足")
            cost = layout_cost(layout, old)
            from .layout import DECOR

            credits = dict(state.get("decorCredits", {}))
            existing_ids = {d["id"] for d in old.get("decor", [])}
            for d in layout.get("decor", []):
                kind = d["type"]
                if d["id"] not in existing_ids and credits.get(kind, 0) > 0:
                    cost = max(0, cost - DECOR[kind][2])
                    credits[kind] -= 1
            state["decorCredits"] = credits
            if cost:
                self.wallet(uid, -cost, "layout")
            farm.layout = deepcopy(layout)
        elif kind in ("buyTurnips", "sellTurnips"):
            integer(q, "quantity", 1)
            tw = self.turnip(farm)
            if kind == "buyTurnips":
                from .turnips import turnip_can_buy as can_buy

                if (
                    not can_buy(self.now, self.config)
                    or tw["bought"] + q > self.config["turnips"]["maximumQuantity"]
                ):
                    fail("MARKET_CLOSED", "現在不能買大頭菜或已達上限")
                self.capacity(farm, q)
                self.wallet(uid, -q * tw["buyPrice"], "buy_turnip")
                self.add(uid, "turnip", q, instant(tw["weekStart"]) + timedelta(days=7))
                tw["quantity"] += q
                tw["bought"] += q
            else:
                price = rules.turnip_quote(tw, self.now, self.config)
                if price is None or tw["quantity"] < q:
                    fail("MARKET_CLOSED", "現在不能賣大頭菜")
                self.consume(uid, "turnip", q)
                self.wallet(
                    uid, math.floor(price * q * (0.9 if farm.conscience < 50 else 1)), "sell_turnip"
                )
                tw["quantity"] -= q
            farm.turnips = tw
        else:
            fail("INVALID_ACTION", "無效農場操作", 400)
        farm.state = state
        self.db.flush()
        return self.snapshot(farm)

    def market_prices(self):
        from .time import market_slot, market_slot_start

        tick = market_slot(self.now)
        at = market_slot_start(tick)
        self.db.flush()
        rows = {
            r.crop_id: r
            for r in self.db.scalars(
                select(GameMarketSnapshot).where(GameMarketSnapshot.tick == tick)
            )
        }
        events = list(
            self.db.scalars(
                select(GameEvent).where(
                    GameEvent.kind == "market_sale",
                    GameEvent.created_at >= at - timedelta(hours=24),
                    GameEvent.created_at < at,
                )
            )
        )
        for crop_id in self.crops:
            if crop_id not in rows:
                supply = sum(
                    e.data.get("quantity", 0) for e in events if e.data.get("cropId") == crop_id
                )
                row = GameMarketSnapshot(
                    tick=tick,
                    crop_id=crop_id,
                    price=rules.market_price(crop_id, at, self.config, supply_quantity=supply),
                    supply_quantity=supply,
                )
                self.db.add(row)
                rows[crop_id] = row
        self.db.flush()
        return {k: r.price for k, r in rows.items()}

    def market_history(self, crop_id):
        if crop_id not in self.crops:
            fail("INVALID_CROP", "無效作物", 400)
        self.market_prices()
        from .time import market_slot, market_slot_start

        tick = market_slot(self.now)
        rows = {
            r.tick: r.price
            for r in self.db.scalars(
                select(GameMarketSnapshot).where(
                    GameMarketSnapshot.crop_id == crop_id,
                    GameMarketSnapshot.tick > tick - self.config["market"]["historySlots"],
                    GameMarketSnapshot.tick <= tick,
                )
            )
        }
        return [
            {
                "at": iso(market_slot_start(i)),
                "price": rows.get(
                    i, rules.market_price(crop_id, market_slot_start(i), self.config)
                ),
            }
            for i in range(tick - self.config["market"]["historySlots"] + 1, tick + 1)
        ]

    def turnip(self, farm):
        generated = rules.turnip_generate(
            farm.state["playerId"],
            self.now,
            self.config,
            previous_pattern=farm.turnips["pattern"] if farm.turnips else None,
        )
        if farm.turnips and farm.turnips["weekStart"] == generated["weekStart"]:
            tw = deepcopy(farm.turnips)
        else:
            tw = {**generated, "quantity": 0, "bought": 0}
        farm.turnips = tw
        return tw

    def turnip_snapshot(self, farm):
        from .turnips import turnip_can_buy as can_buy

        tw = self.turnip(farm)
        return {
            "buyPrice": tw["buyPrice"] if can_buy(self.now, self.config) else None,
            "sellPrice": rules.turnip_quote(tw, self.now, self.config),
            "quantity": tw["quantity"],
            "bought": tw["bought"],
            "expiresAt": iso(instant(tw["weekStart"]) + timedelta(days=7)),
        }

    def replay(self, uid, request_id, payload, execute):
        from uuid import UUID

        try:
            UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            fail("INVALID_REQUEST_ID", "requestId 必須為 UUID", 400)
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        row = self.db.get(GameAction, (uid, request_id))
        if row:
            if row.fingerprint != fingerprint:
                fail("REQUEST_ID_REUSED", "requestId 已用於其他操作")
            return row.response
        response = execute()
        self.db.add(
            GameAction(
                user_id=uid,
                request_id=request_id,
                fingerprint=fingerprint,
                response=response,
                created_at=self.now,
            )
        )
        self.db.flush()
        return response
