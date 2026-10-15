"""Explicit admin-only farm test tools, separate from normal gameplay actions."""

from copy import deepcopy
from datetime import timedelta

from sqlalchemy import delete, or_

from . import rules
from .engine import fail, instant, integer, iso
from .models import (
    GameAction,
    GameEvent,
    GameInventory,
    GameLeaderboard,
    GameListing,
    GameSettlement,
    GameSlot,
    GameWallet,
)


def apply(service, farm, data):
    service.advance(farm)
    uid, now = farm.user_id, service.now
    state = deepcopy(farm.state)
    command = data.get("command")
    slots = service.slots(uid)
    if command == "addCoins":
        service.wallet(uid, integer(data.get("value"), "value", 1, 100000), "debug_cheat")
    elif command == "setLevel":
        target = min(
            integer(data.get("value"), "value", 1, 99),
            rules.level(rules.MAX_EXACT_XP, service.config),
        )
        state["xp"] = rules.xp_for_level(target, service.config)
    elif command == "refillWater":
        state["waterRemaining"] = 99
    elif command in ("giveAllSeeds", "giveAllItems", "giveAllCrops"):
        qty = integer(data.get("value"), "value", 1, 100)
        for key, crop in (service.items if command == "giveAllItems" else service.crops).items():
            prefix = (
                "item:"
                if command == "giveAllItems"
                else "seed:"
                if command == "giveAllSeeds"
                else ""
            )
            expires = (
                now + timedelta(days=crop["shelfLifeDays"]) if command == "giveAllCrops" else None
            )
            service.add(uid, prefix + key, qty, expires)
    elif command == "addCapacity":
        state["capacity"] += integer(data.get("value"), "value", 1, 1000)
    elif command == "clearInventory":
        service.db.execute(delete(GameInventory).where(GameInventory.user_id == uid))
    elif command == "unlockPlots":
        count = integer(
            data.get("value"), "value", 0, service.config["progression"]["maximumPlots"]
        )
        for idx in range(len(slots), count):
            service.db.add(
                GameSlot(user_id=uid, idx=idx, plot=rules.empty_plot(idx, now), stolen_qty=0)
            )
    elif command in ("plantEmpty", "ripenAll", "careAll", "clearAllPlots"):
        crop = service.crops.get(data.get("cropId")) if command == "plantEmpty" else None
        if command == "plantEmpty" and crop is None:
            fail("INVALID_ARGUMENT", "找不到作物", 400)
        for slot in slots:
            p = deepcopy(slot.plot)
            if command == "clearAllPlots":
                p = rules.empty_plot(slot.idx, now)
            elif command == "plantEmpty" and p["cropId"] is None:
                p = rules.empty_plot(slot.idx, now)
                p.update(
                    cropId=crop["id"],
                    playerId=state["playerId"],
                    plantedAt=iso(now),
                    moisture=service.config["care"]["initialMoisture"],
                    seasonPasses=crop["crossSeasons"],
                )
            elif command == "ripenAll" and p["cropId"] and not p["withered"]:
                p["growth"] = 1
            elif command == "careAll" and p["cropId"]:
                p.update(
                    moisture=100,
                    dryHours=0,
                    weeds=False,
                    pests=False,
                    windDamaged=False,
                    withered=False,
                )
            slot.plot = p
            slot.stolen_qty = 0
    elif command == "fastForward":
        by = timedelta(seconds=integer(data.get("value"), "value", 1, 604800))
        for slot in slots:
            p = deepcopy(slot.plot)
            for key in ("plantedAt", "lastAdvancedAt"):
                if p.get(key):
                    p[key] = iso(instant(p[key]) - by)
            for effect in p["effects"]:
                effect["expiresAt"] = iso(instant(effect["expiresAt"]) - by)
            slot.plot = p
        for listing in service.db.query(GameListing).filter_by(user_id=uid):
            listing.data = {
                **listing.data,
                **{
                    key: iso(instant(listing.data[key]) - by)
                    for key in ("listedAt", "sellsAt", "expiresAt")
                },
            }
        for row in service.inventory(uid):
            if row.expires_at:
                row.expires_at = instant(row.expires_at) - by
        state["lastSeenAt"] = iso(instant(state["lastSeenAt"]) - by)
    elif command in ("setWeather", "setDaylight"):
        weather = deepcopy(
            state.get("debugWeather")
            or dict(temperature=25, precipitation=0, cloud=80, gust=0, humidity=60, isDay=True)
        )
        if command == "setDaylight":
            if type(data.get("value")) is not bool:
                fail("INVALID_ARGUMENT", "無效日夜設定", 400)
            weather["isDay"] = data["value"]
        else:
            kind = data.get("kind")
            if kind not in ("sunny", "cloudy", "rain", "storm", "snow", "windy"):
                fail("INVALID_ARGUMENT", "無效天氣", 400)
            weather.update(
                temperature=-2
                if kind == "snow"
                else 28
                if kind == "sunny"
                else 21
                if kind in ("rain", "storm")
                else 24,
                precipitation={"rain": 3, "storm": 14, "snow": 2}.get(kind, 0),
                cloud=10 if kind == "sunny" else 90,
                gust={"windy": 45, "storm": 60}.get(kind, 5),
            )
        weather["at"] = iso(now.replace(minute=0, second=0, microsecond=0))
        state["debugWeather"] = weather
    elif command == "resetFarm":
        nickname = state.get("nickname")
        for model in (
            GameAction,
            GameInventory,
            GameLeaderboard,
            GameListing,
            GameSettlement,
            GameSlot,
            GameWallet,
        ):
            service.db.execute(delete(model).where(model.user_id == uid))
        service.db.execute(
            delete(GameEvent).where(or_(GameEvent.actor_id == uid, GameEvent.owner_id == uid))
        )
        service.db.delete(farm)
        service.db.flush()
        return service.create(uid, {"nickname": nickname} if nickname else {})
    else:
        fail("INVALID_ARGUMENT", "無效作弊器操作", 400)
    farm.state = state
    service.db.flush()
    service.advance(farm)
    return service.snapshot(farm)
