"""Execute every shared Dart golden row through the Python port (not hash-only)."""

import json
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.services import business_time
from src.services.game import rules, time
from src.services.game.random import FarmRandom, farm_seed

GOLDEN = json.loads(Path(__file__).with_name("golden").joinpath("rules_v1.json").read_text())


@pytest.fixture(autouse=True)
def taipei(monkeypatch):
    monkeypatch.setattr(business_time, "business_tz", lambda: ZoneInfo("Asia/Taipei"))


@pytest.fixture
def config():
    return rules.load_config()


def assert_fields(actual, expected):
    for key, value in expected.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert actual[key] == pytest.approx(value, abs=1e-10)
        elif key.endswith("At") or key == "weekStart":
            assert time.utc(actual[key]) == time.utc(value)
        else:
            assert actual[key] == value


@pytest.mark.parametrize("row", GOLDEN["random"])
def test_random_golden(row):
    seed = farm_seed(row["key"])
    assert f"{seed:x}" == row["seedHex"]
    random = FarmRandom(seed)
    assert [f"{random.next_uint64():x}" for _ in range(3)] == row["outputsHex"]


@pytest.mark.parametrize("row", GOLDEN["time"])
def test_time_golden(row):
    assert time.day_key(row["at"]) == row["day"]
    assert time.season(row["at"]) == row["season"]
    assert time.week_start(row["at"]) == time.utc(row["weekStart"])
    assert time.market_slot(row["at"]) == row["marketSlot"]


@pytest.mark.parametrize("row", GOLDEN["growth"], ids=lambda r: r["name"])
def test_growth_golden(row, config):
    p = rules.plant(rules.empty_plot(0, row["at"]), row["crop"], row["at"], "golden", config)
    p["growth"] = row.get("growth", 0)
    result = rules.advance(p, row["to"], [dict(at=row["at"], **row["weather"])], config)
    assert_fields(result, row["expected"])


@pytest.mark.parametrize("row", GOLDEN["market"])
def test_market_golden(row, config):
    assert rules.market_price(row["crop"], row["at"], config) == row["price"]


@pytest.mark.parametrize("row", GOLDEN["scores"], ids=lambda r: r["name"])
def test_scores_golden(row, config):
    week = time.utc("2026-10-11T16:00:00Z")
    entries = []
    # The shared generator records total expense once, then zero-amount records
    # for remaining habit days; createdAt is deliberately absent in row zero.
    kind = "expense" if row["expense"] else "income"
    entries.append(
        dict(
            happenedAt=week,
            createdAt=week if row["days"] else None,
            type=kind,
            amount=row["expense"] or row["income"],
        )
    )
    for day in range(1, row["days"]):
        at = week + timedelta(days=day)
        entries.append(dict(happenedAt=at, createdAt=at, type=kind, amount=0))
    result = rules.score(week, entries, config, previous_streak=row.get("previousStreak", 0))
    assert_fields(result, row["expected"])


@pytest.mark.parametrize("row", GOLDEN["turnips"])
def test_turnips_golden(row, config):
    result = rules.turnip_generate(row["player"], row["at"], config, row["previousPattern"])
    assert_fields(result, row["expected"])


def test_iso_matches_dart_seed_precision():
    assert time.iso("2026-10-13T02:00:00Z") == "2026-10-13T02:00:00.000Z"
    assert time.iso("2026-10-13T02:00:00.123456Z") == "2026-10-13T02:00:00.123456Z"


def test_partial_hours_and_observation_frequency(config):
    start = time.utc("2026-10-13T02:00:00.123456Z")
    initial = rules.plant(rules.empty_plot(0, start), "radish", start, "player", config)
    end = start + timedelta(hours=39)
    whole = rules.advance(initial, end, [], config)
    split = initial
    for i in range(1, 157):
        split = rules.advance(split, start + timedelta(minutes=i * 15), [], config)
    for field in ("growth", "moisture", "dryHours"):
        assert split[field] == pytest.approx(whole[field], abs=1e-8)
    for field in ("weeds", "pests", "withered", "windDamaged"):
        assert split[field] == whole[field]


def test_drought_season_and_greenhouse(config):
    start = time.utc("2026-11-30T15:00:00Z")
    p = rules.plant(rules.empty_plot(0, start), "radish", start, "p", config)
    covered = rules.use_item(p, "greenhouse", start, config)
    covered = rules.advance(covered, start + timedelta(hours=2), [], config)
    assert not covered["withered"]
    assert covered["effects"][0]["seasonUsed"]
    assert rules.advance(p, start + timedelta(hours=2), [], config)["withered"]
    start = time.utc("2026-10-13T02:00:00Z")
    p = rules.plant(rules.empty_plot(0, start), "radish", start, "p", config)
    assert rules.advance(p, start + timedelta(hours=33), [], config)["withered"]


def test_items_harvest_and_multi_harvest(config):
    start = time.utc("2026-05-13T02:00:00Z")
    p = rules.plant(rules.empty_plot(0, start), "tea", start, "p", config)
    p["pests"] = True
    p = rules.use_item(p, "pesticide", start, config)
    assert not p["pests"]
    p = rules.use_item(p, "fertilizer", start, config)
    p.update(growth=1, windDamaged=True)
    result = rules.harvest(p, start, config)
    assert result["quantity"] == 2
    assert result["plot"]["cropId"] == "tea"
    assert result["plot"]["harvests"] == 1
    assert result["plot"]["growth"] == 0
    assert not result["plot"]["windDamaged"]
    assert "fertilizer" not in {e["id"] for e in result["plot"]["effects"]}
    p["harvests"] = 2
    assert rules.harvest(p, start, config)["plot"]["cropId"] is None


def test_score_projection_filters_and_local_midnight(config):
    week = time.utc("2026-10-11T16:00:00Z")
    entries = [
        dict(happenedAt=week, createdAt=week, type="income", amount=7000),
        dict(
            happenedAt=week - timedelta(seconds=1),
            createdAt=week - timedelta(seconds=1),
            type="expense",
            amount=0,
        ),
    ]
    result = rules.score(week, entries, config)
    assert result["habit"] == pytest.approx(1 / 7)
    entries += [
        dict(happenedAt=week, createdAt=week, type="expense", amount=9000, excludeFromStats=True),
        dict(happenedAt=week, createdAt=week, type="expense", amount=9000, isRefund=True),
        dict(happenedAt=week, createdAt=week, type="transfer", amount=9000),
    ]
    assert rules.score(week, entries, config) == result
    # 23:59 of the last business day is included; following Monday 00:00 isn't.
    entries += [
        dict(
            happenedAt=week + timedelta(days=7, seconds=-1),
            createdAt=week + timedelta(days=7, seconds=-1),
            type="income",
            amount=0,
        ),
        dict(
            happenedAt=week + timedelta(days=7),
            createdAt=week + timedelta(days=7),
            type="income",
            amount=0,
        ),
    ]
    assert rules.score(week, entries, config)["habit"] == pytest.approx(2 / 7)


def test_budget_cross_month_and_due_boundary(config):
    week = time.utc("2026-08-30T16:00:00Z")
    assert rules.weekly_budget(week, {"2026-08": 3100, "2026-09": 6000}) == 1300
    now = time.utc("2026-10-11T16:04:59Z")
    due = rules.due_weeks(now - timedelta(days=70), now, config)
    assert due[-1] == time.utc("2026-09-27T16:00:00Z")
    due = rules.due_weeks(now - timedelta(days=70), now + timedelta(seconds=1), config)
    assert due[-1] == time.utc("2026-10-04T16:00:00Z")
    assert len(due) == 4
    assert (
        rules.due_weeks(
            now - timedelta(days=70),
            now + timedelta(seconds=1),
            config,
            [time.day_key(w) for w in due],
        )
        == []
    )


def test_turnip_sunday_new_batch_and_expiry(config):
    sunday_midnight = time.utc("2026-10-10T16:00:00Z")
    week = rules.turnip_generate("p", sunday_midnight, config)
    assert rules.turnip_can_buy(sunday_midnight, config)
    assert time.utc(week["weekStart"]) == sunday_midnight + timedelta(hours=5)
    assert rules.turnip_quote(week, sunday_midnight, config) is None
    rules.validate_turnip_purchase(week, sunday_midnight, 400, 0, config)
    with pytest.raises(ValueError):
        rules.validate_turnip_purchase(week, sunday_midnight, 401, 0, config)
    monday = sunday_midnight + timedelta(days=1)
    assert rules.turnip_quote(week, monday, config) == week["prices"][0]
    assert rules.turnip_quote(week, monday + timedelta(hours=12), config) == week["prices"][1]
    assert rules.turnip_expired(week, sunday_midnight + timedelta(days=7, hours=5))


def test_layout_cost_capacity_and_bounds():
    old = rules.default_layout(range(2))
    rules.validate_layout(old, range(2))
    assert rules.layout_cost(old, old) == 0
    new = json.loads(json.dumps(old))
    p = rules.first_free(new, 16, 16)
    new["decor"].append(dict(id="crate", type="crate_slat", position=p))
    rules.validate_layout(new, range(2))
    assert rules.layout_cost(new, old) == 30
    assert rules.storage_capacity(new) == 10
    new["plots"]["0"] = new["plots"]["1"]
    with pytest.raises(ValueError):
        rules.validate_layout(new, range(2))


def test_negative_amount_and_invalid_care(config):
    with pytest.raises(ValueError):
        rules.score(
            "2026-10-12T02:00:00Z",
            [dict(happenedAt="2026-10-12T02:00:00Z", type="income", amount=float("nan"))],
            config,
        )
    with pytest.raises(ValueError):
        rules.care(rules.empty_plot(0, "2026-10-12T02:00:00Z"), "water", config)


def simulate_economy(profile, config, expand=True):
    """Literal FarmSimulation harness: rain-free visits and immediate FIFO sales."""
    start = time.utc("2026-10-04T16:00:00Z")
    coins = config["progression"]["initialCoins"]
    xp = streak = 0
    water = dict(day="", remaining=0, bonus=False)
    plots = [rules.empty_plot(i, start) for i in range(config["progression"]["initialPlots"])]
    entries = []
    result = []
    for week in range(12):
        week_start = start + timedelta(days=7 * week)
        before = coins
        expansion_cost = harvested = withered = 0
        recorded_days = set()
        for day in range(7):
            if profile == "casual" and day not in (1, 4):
                continue
            for hour in [8, 20] if profile == "diligent" else [8]:
                now = week_start + timedelta(days=day, hours=hour)
                key = time.day_key(now)
                if key not in recorded_days:
                    recorded_days.add(key)
                    entries.append(dict(happenedAt=now, createdAt=now, type="expense", amount=100))
                    if len(recorded_days) == 1:
                        entries.append(
                            dict(happenedAt=now, createdAt=now, type="income", amount=2000)
                        )
                water = rules.water_refresh(water, now, True, config)
                rotation = (week * 7 + day) % len(plots)
                for n in range(len(plots)):
                    i = (n + rotation) % len(plots)
                    p = rules.advance(plots[i], now, [], config)
                    if p["withered"]:
                        withered += 1
                        p = rules.care(p, "clear", config)
                    if rules.ready(p):
                        h = rules.harvest(p, now, config)
                        # All produce is immediately sold; inventory never remains
                        # across visits. Capacity still rejects oversized harvests.
                        if h["quantity"] <= config["progression"]["initialCapacity"]:
                            coins += h["quantity"] * rules.market_price(h["cropId"], now, config)
                            xp += h["xp"]
                            harvested += h["quantity"]
                            p = h["plot"]
                    if p["cropId"] is None:
                        season = time.season(now)
                        crop_id = (
                            "bok_choy"
                            if season == "spring"
                            else "water_spinach"
                            if season == "summer"
                            else "radish"
                            if season == "autumn" and profile == "diligent"
                            else "sweet_potato"
                            if season == "autumn"
                            else "chrysanthemum"
                        )
                        seed_price = rules.crop(config, crop_id)["seedPrice"]
                        if coins >= seed_price:
                            p = rules.plant(
                                p, crop_id, now, profile, config, rules.level(xp, config)
                            )
                            coins -= seed_price
                    if p["cropId"] is not None:
                        if p["weeds"]:
                            p = rules.care(p, "weed", config)
                        pesticide = next(row for row in config["items"] if row["id"] == "pesticide")
                        if p["pests"] and coins >= pesticide["price"]:
                            coins -= pesticide["price"]
                            p = rules.care(p, "removePests", config)
                            # Simulation appends protection rather than replacing.
                            p["effects"].append(
                                dict(
                                    id="pesticide",
                                    expiresAt=time.iso(
                                        now + timedelta(hours=pesticide["durationHours"])
                                    ),
                                    seasonUsed=False,
                                )
                            )
                        while p["moisture"] < 80 and water["remaining"] > 0:
                            p = rules.care(p, "water", config)
                            water = rules.water_use(water)
                    plots[i] = p
        settlement = rules.score(week_start, entries, config, previous_streak=streak)
        streak = settlement["streak"]
        coins += settlement["coins"]
        operating_net = coins - before
        if expand and rules.can_expand(len(plots), xp, config):
            price = rules.expansion_price(len(plots), config)
            reserve = len(plots) * rules.crop(config, "sweet_potato")["seedPrice"]
            if coins >= price + reserve:
                coins -= price
                expansion_cost = price
                plots.append(rules.empty_plot(len(plots), week_start + timedelta(days=7)))
        result.append(
            dict(
                week=week + 1,
                operatingNet=operating_net,
                expansionCost=expansion_cost,
                coins=coins,
                plots=len(plots),
                harvested=harvested,
                withered=withered,
                score=round(settlement["score"], 6),
            )
        )
    return result


@pytest.mark.parametrize("profile", ["diligent", "ordinary", "casual"])
@pytest.mark.parametrize("expand", [True, False])
def test_all_twelve_week_economy_golden(profile, expand, config):
    golden = json.loads(Path(__file__).with_name("golden").joinpath("economy_v1.json").read_text())
    expected = (
        golden[profile]
        if expand
        else next(r["weeks"] for r in golden["fixedSixPlots"] if r["profile"] == profile)
    )
    assert simulate_economy(profile, config, expand) == expected


def test_water_daily_bonus_cannot_be_reclaimed(config):
    now = time.utc("2026-10-11T16:00:00Z")
    water = rules.water_refresh(dict(day="", remaining=0, bonus=False), now, False, config)
    assert water["remaining"] == 8
    water = rules.water_refresh(water, now, True, config)
    assert water["remaining"] == 16
    water = rules.water_use(water)
    assert rules.water_refresh(water, now, True, config) == water
    assert rules.water_refresh(water, now - timedelta(days=1), True, config) == water
    assert rules.water_refresh(water, now + timedelta(days=1), False, config)["remaining"] == 8


def test_millisecond_seed_is_integer_and_floors_before_epoch():
    assert time.milliseconds_since_epoch("1969-12-31T23:59:59.999999Z") == -1
    assert time.milliseconds_since_epoch("2026-10-13T02:00:00.123456Z") == 1791856800123


def test_missing_layout_fields_fail_as_validation_errors():
    for layout in (
        None,
        {},
        {"version": 2, "plots": {"0": {}}},
        {"version": 2, "plots": {}, "decor": [{}]},
    ):
        with pytest.raises(ValueError):
            rules.validate_layout(layout, range(1))


def test_plant_rejects_wrong_season_level_and_time(config):
    now = time.utc("2026-10-13T02:00:00Z")
    empty = rules.empty_plot(0, now)
    for crop_id, at, level in [
        ("tea", now, 1),
        ("pomelo", now, 1),
        ("radish", now - timedelta(hours=1), 1),
    ]:
        with pytest.raises(ValueError):
            rules.plant(empty, crop_id, at, "p", config, level)
    assert rules.may_miss_season("radish", "2026-11-30T15:00:00Z", config)
    assert not rules.may_miss_season("radish", now, config)
