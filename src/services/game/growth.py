"""Pure growth/care rules. Dictionaries retain FarmSaveCodec saveVersion 1."""

import math
from copy import deepcopy
from datetime import timedelta

from .random import FarmRandom, farm_seed
from .time import HOUR, civil, hour_start, iso, milliseconds_since_epoch, next_season, season, utc

MICROS_HOUR = 3_600_000_000


def dart_round(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def crop(config: dict, crop_id: str) -> dict:
    return next((row for row in config["crops"] if row["id"] == crop_id), None) or _unknown(crop_id)


def _unknown(value):
    raise ValueError(f"Unknown farm id: {value}")


def empty_plot(index: int, now) -> dict:
    return dict(
        saveVersion=1,
        index=index,
        playerId="",
        cropId=None,
        plantedAt=None,
        lastAdvancedAt=iso(now),
        growth=0.0,
        moisture=0.0,
        dryHours=0.0,
        weeds=False,
        pests=False,
        windDamaged=False,
        withered=False,
        harvests=0,
        seasonPasses=0,
        yieldGrowingMicros=0,
        yieldAdverseMicros=0,
        readyYield=0,
        effects=[],
    )


def plant(plot: dict, crop_id: str, now, player_id: str, config: dict, level: int = 1) -> dict:
    now = utc(now)
    row = crop(config, crop_id)
    if plot["cropId"] is not None:
        raise ValueError("請先收成或清除枯草")
    if now < utc(plot["lastAdvancedAt"]):
        raise ValueError("時間倒退，暫停操作")
    if season(now) not in row["seasons"]:
        raise ValueError("非當季作物")
    if level < row["minLevel"]:
        raise ValueError("等級尚未解鎖")
    result = empty_plot(plot["index"], now)
    result.update(
        cropId=crop_id,
        playerId=player_id,
        plantedAt=iso(now),
        moisture=float(config["care"]["initialMoisture"]),
        seasonPasses=row["crossSeasons"],
    )
    return result


def ready(plot: dict) -> bool:
    return plot["cropId"] is not None and plot["growth"] >= 1 and not plot["withered"]


def care(plot: dict, action: str, config: dict) -> dict:
    p = deepcopy(plot)
    if action == "clear":
        if not p["withered"]:
            raise ValueError("只有枯草可以清除")
        return empty_plot(p["index"], p["lastAdvancedAt"])
    if p["cropId"] is None or p["withered"]:
        raise ValueError("田地沒有活的作物")
    if action == "water":
        p.update(moisture=min(100, p["moisture"] + config["care"]["waterAmount"]), dryHours=0.0)
    elif action == "weed":
        p["weeds"] = False
    elif action == "removePests":
        p["pests"] = False
    else:
        raise ValueError("Unknown care action")
    return p


def use_item(plot: dict, item_id: str, now, config: dict) -> dict:
    item = next((row for row in config["items"] if row["id"] == item_id), None) or _unknown(item_id)
    if plot["cropId"] is None or plot["withered"]:
        raise ValueError("田地沒有活的作物")
    now = utc(now)
    duration = item["durationHours"]
    expires = (
        now + timedelta(microseconds=dart_round(duration * MICROS_HOUR))
        if duration > 0
        else next_season(next_season(now))
        if item_id == "greenhouse"
        else next_season(now)
    )
    p = care(plot, "removePests", config) if item_id == "pesticide" else deepcopy(plot)
    p["effects"] = [e for e in p["effects"] if e["id"] != item_id] + [
        dict(id=item_id, expiresAt=iso(expires), seasonUsed=False)
    ]
    return p


def harvest_weights(plot: dict, config: dict) -> list[float]:
    total = plot.get("yieldGrowingMicros", 0)
    adverse = min(1.0, max(0.0, plot.get("yieldAdverseMicros", 0) / total)) if total else 0.0
    normal = config["care"]["normalYieldWeights"]
    bad = config["care"]["adverseYieldWeights"]
    return [normal[i] + (bad[i] - normal[i]) * adverse for i in range(5)]


def roll_yield(plot: dict, config: dict) -> int:
    key = f"{plot['playerId']}|{plot['index']}|{iso(plot['plantedAt'])}|harvest|{plot['harvests']}"
    roll = FarmRandom(farm_seed(key)).next_double() * 100
    cumulative = 0.0
    for quantity, weight in enumerate(harvest_weights(plot, config), 1):
        cumulative += weight
        if roll < cumulative:
            return quantity
    return 5


def advance(plot: dict, now, weather, config: dict) -> dict:
    return advance_result(plot, now, weather, config)["plot"]


def advance_result(initial: dict, now, weather, config: dict) -> dict:
    to = utc(now)
    p = deepcopy(initial)
    at = utc(p["lastAdvancedAt"])
    events = []
    if to <= at:
        return dict(plot=p, events=events)
    if p["cropId"] is None or p["withered"]:
        p["lastAdvancedAt"] = iso(to)
        return dict(plot=p, events=events)
    if p["plantedAt"] is None:
        raise ValueError("已播種田地缺少 plantedAt")
    if ready(p) and not p.get("readyYield", 0):
        p["readyYield"] = roll_yield(p, config)
    row = crop(config, p["cropId"])
    options = config["care"]
    planted = utc(p["plantedAt"])
    hours = {hour_start(w["at"]): w for w in weather or []}
    for w in hours.values():
        for key in ("temperature", "precipitation", "cloud", "gust", "humidity"):
            if not math.isfinite(
                w.get(key, {"temperature": 25, "cloud": 50, "humidity": 50}.get(key, 0))
            ):
                raise ValueError("無效的逐時天氣")
        if (
            w.get("precipitation", 0) < 0
            or w.get("gust", 0) < 0
            or not 0 <= w.get("cloud", 50) <= 100
            or not 0 <= w.get("humidity", 50) <= 100
        ):
            raise ValueError("無效的逐時天氣")

    def effect(kind, t):
        return any(e["id"] == kind and utc(e["expiresAt"]) > t for e in p["effects"])

    def chance(kind, t, probability):
        key = f"{p['playerId']}|{p['index']}|{iso(planted)}|{p['harvests']}|{kind}|{milliseconds_since_epoch(t)}"
        return FarmRandom(farm_seed(key)).next_double() < probability

    while at < to and not p["withered"]:
        hour = hour_start(at)
        season_at = next_season(at)
        elapsed = (at - planted) // timedelta(microseconds=1)
        weed_at = planted + timedelta(
            microseconds=(elapsed // (options["weedIntervalHours"] * MICROS_HOUR) + 1)
            * options["weedIntervalHours"]
            * MICROS_HOUR
        )
        pest_at = planted + timedelta(
            microseconds=(elapsed // (options["pestIntervalHours"] * MICROS_HOUR) + 1)
            * options["pestIntervalHours"]
            * MICROS_HOUR
        )
        end = min(
            [hour + HOUR, to, season_at, weed_at, pest_at]
            + [utc(e["expiresAt"]) for e in p["effects"] if utc(e["expiresAt"]) > at]
        )
        w = dict(
            temperature=25,
            precipitation=0,
            cloud=50,
            gust=0,
            humidity=50,
            isDay=6 <= civil(at).hour < 18,
        )
        if hour in hours:
            # A supplied weather hour defaults to isDay true, like FarmWeatherHour.
            w.update(isDay=True)
            w.update(hours[hour])
        greenhouse = effect("greenhouse", at)
        sunny = effect("sun_doll", at)
        rain = effect("rain_jar", at) or (
            not greenhouse and not sunny and w["precipitation"] >= options["rainThreshold"]
        )
        day = sunny or effect("grow_light", at) or w["isDay"]
        sprinkler = effect("sprinkler", at)
        moisture = 100.0 if rain else p["moisture"]
        if sprinkler:
            moisture = max(options["sprinklerMoisture"], moisture)
        loss = options["waterLoss"][row["waterNeed"]]
        if (
            not greenhouse
            and (sunny or w["cloud"] < options["sunnyCloudThreshold"])
            and w["temperature"] > options["hotTemperature"]
        ):
            loss *= options["hotLossMultiplier"]
        if rain or (sprinkler and moisture <= options["sprinklerMoisture"]):
            loss = 0
        dt = (end - at) / HOUR
        if loss > 0:
            for threshold in [options["thirstThreshold"], 0.0] + (
                [options["sprinklerMoisture"]] if sprinkler else []
            ):
                if moisture > threshold:
                    dt = min(dt, (moisture - threshold) / loss)
        if moisture <= 0:
            dt = min(dt, max(0, options["wiltAfterDryHours"] - p["dryHours"]))
        end = min(end, at + timedelta(microseconds=max(1, dart_round(dt * MICROS_HOUR))))
        dt = (end - at) / HOUR
        light_index = (
            3
            if not day
            else 2
            if rain
            else 0
            if sunny or w["cloud"] < options["sunnyCloudThreshold"]
            else 1
        )
        factor = float(options["lightFactors"][row["light"]][light_index])
        if (
            not greenhouse
            and rain
            and w["precipitation"] >= options["heavyRainThreshold"]
            and row["waterNeed"] == "low"
        ):
            factor *= options["heavyRainMultiplier"]
        if (
            not greenhouse
            and w["temperature"] < options["coldTemperature"]
            and "winter" not in row["seasons"]
        ):
            factor *= options["coldMultiplier"]
        if p["weeds"]:
            factor *= options["weedGrowthMultiplier"]
        if effect("fertilizer", at):
            factor *= options["fertilizerMultiplier"]
        growing = moisture >= options["thirstThreshold"] and (
            loss == 0 or moisture > options["thirstThreshold"]
        )
        if growing and p["growth"] < 1:
            mature_micros = math.ceil((1 - p["growth"]) * row["growthHours"] / factor * MICROS_HOUR)
            mature_at = at + timedelta(microseconds=max(1, mature_micros))
            if mature_at < end:
                end = mature_at
                dt = (end - at) / HOUR
        growth = (
            min(1.0, p["growth"] + dt / row["growthHours"] * factor) if growing else p["growth"]
        )
        dry_hours = p["dryHours"] + dt if moisture <= 0 else 0.0
        adverse = p["pests"] or (
            not greenhouse
            and (
                (w["gust"] >= options["windThreshold"] and not effect("wind_net", at))
                or (not sunny and w["precipitation"] >= options["heavyRainThreshold"])
                or (w["temperature"] < options["coldTemperature"] and "winter" not in row["seasons"])
                or (
                    w["temperature"] > options["hotTemperature"]
                    and (sunny or w["cloud"] < options["sunnyCloudThreshold"])
                    and not rain and not sprinkler
                )
            )
        )
        yield_micros = (end - at) // timedelta(microseconds=1) if p["growth"] < 1 else 0
        p["yieldGrowingMicros"] = p.get("yieldGrowingMicros", 0) + yield_micros
        p["yieldAdverseMicros"] = p.get("yieldAdverseMicros", 0) + (yield_micros if adverse else 0)
        next_moisture = max(0.0, moisture - loss * dt)
        if abs(next_moisture) < 1e-8:
            next_moisture = 0.0
        if abs(next_moisture - options["thirstThreshold"]) < 1e-8:
            next_moisture = float(options["thirstThreshold"])
        p.update(growth=growth, moisture=next_moisture, dryHours=dry_hours, lastAdvancedAt=iso(end))
        if ready(p) and not p.get("readyYield", 0):
            p["readyYield"] = roll_yield(p, config)

        def event(kind, when=end):
            events.append(dict(kind=kind, at=iso(when)))

        if growth >= 1 and initial["growth"] < 1 and not any(e["kind"] == "mature" for e in events):
            event("mature")
        if dry_hours + 1e-10 >= options["wiltAfterDryHours"]:
            p["withered"] = True
            event("drought")
        if not p["withered"] and end == season_at:
            if p["seasonPasses"] > 0:
                p["seasonPasses"] -= 1
            elif any(
                e["id"] == "greenhouse" and not e["seasonUsed"] and utc(e["expiresAt"]) > at
                for e in p["effects"]
            ):
                for e in p["effects"]:
                    if e["id"] == "greenhouse":
                        e["seasonUsed"] = True
            else:
                p["withered"] = True
                event("season_wither")
        if not p["withered"]:
            if end == weed_at and not p["weeds"] and chance("weed", end, options["weedChance"]):
                p["weeds"] = True
                event("weed")
            if end == pest_at and not p["pests"] and not effect("pesticide", at):
                humid = all(
                    (h := hours.get(hour - i * HOUR)) is not None
                    and h.get("temperature", 25) > options["hotTemperature"]
                    and h.get("humidity", 50) >= options["highHumidity"]
                    for i in range(48)
                )
                if chance("pest", end, options["humidPestChance" if humid else "pestChance"]):
                    p["pests"] = True
                    event("pest")
            if (
                end == hour + HOUR
                and not p["windDamaged"]
                and not greenhouse
                and not effect("wind_net", at)
                and w["gust"] >= options["windThreshold"]
                and chance("wind", end, options["windDamageChance"])
            ):
                p["windDamaged"] = True
                event("wind_damage")
        p["effects"] = [e for e in p["effects"] if utc(e["expiresAt"]) > end]
        at = end
    p["lastAdvancedAt"] = iso(to)
    return dict(plot=p, events=events)


def harvest(plot: dict, now, config: dict) -> dict:
    if not ready(plot):
        raise ValueError("作物尚未成熟或已枯死")
    now = utc(now)
    if now != utc(plot["lastAdvancedAt"]):
        raise ValueError("收成前必須先推進到操作時間")
    row = crop(config, plot["cropId"])
    quantity = plot.get("readyYield", 0) or roll_yield(plot, config)
    count = plot["harvests"] + 1
    p = deepcopy(plot) if count < row["harvests"] else empty_plot(plot["index"], now)
    if count < row["harvests"]:
        p.update(
            growth=0.0,
            harvests=count,
            windDamaged=False,
            yieldGrowingMicros=0,
            yieldAdverseMicros=0,
            readyYield=0,
            effects=[e for e in p["effects"] if e["id"] != "fertilizer"],
        )
    return dict(
        plot=p,
        cropId=row["id"],
        quantity=quantity,
        expiresAt=iso(now + timedelta(days=row["shelfLifeDays"])),
        xp=row["xp"],
        seedReward=row["seedReward"],
    )


def may_miss_season(crop_id: str, now, config: dict) -> bool:
    now = utc(now)
    return now + timedelta(
        microseconds=dart_round(crop(config, crop_id)["growthHours"] * MICROS_HOUR)
    ) > next_season(now)
