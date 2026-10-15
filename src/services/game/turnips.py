"""Only quote() is suitable for clients; generated future prices stay on server."""

from datetime import timedelta

from .growth import dart_round
from .random import FarmRandom, farm_seed
from .time import civil, day_key, iso, turnip_week_start, utc


def turnip_can_buy(now, config: dict) -> bool:
    d = civil(now)
    o = config["turnips"]
    return d.weekday() == 6 and o["buyStartHour"] <= d.hour < o["buyEndHour"]


def turnip_start(now, config: dict):
    hour = config["turnips"]["expiryHour"]
    start = turnip_week_start(now, hour)
    d = civil(now)
    return start + timedelta(days=7) if d.weekday() == 6 and d.hour < hour else start


def turnip_expired(week: dict, now) -> bool:
    return utc(now) >= utc(week["weekStart"]) + timedelta(days=7)


def validate_turnip_purchase(week: dict, now, quantity: int, already_bought: int, config: dict):
    if quantity <= 0 or already_bought < 0:
        raise ValueError("數量必須為正整數")
    if not turnip_can_buy(now, config) or turnip_start(now, config) != utc(week["weekStart"]):
        raise ValueError("菜販不在或已經換週")
    if already_bought + quantity > config["turnips"]["maximumQuantity"]:
        raise ValueError("本週購買量超過上限")


def turnip_quote(week: dict, now, config: dict) -> int | None:
    d = civil(now)
    if utc(now) < utc(week["weekStart"]) or turnip_expired(week, now) or d.weekday() == 6:
        return None
    return week["prices"][d.weekday() * 2 + (d.hour >= config["turnips"]["sellSplitHour"])]


def turnip_generate(player_id: str, now, config: dict, previous_pattern: str | None = None) -> dict:
    o = config["turnips"]
    start = turnip_start(now, config)
    random = FarmRandom(farm_seed(f"turnip|{player_id}|{day_key(start)}|{config['version']}"))
    patterns = o["patterns"]
    if previous_pattern is not None and previous_pattern not in patterns:
        raise ValueError("Unknown previous pattern")
    weights = (
        o["initialWeights"]
        if previous_pattern is None
        else o["transitionWeights"][patterns.index(previous_pattern)]
    )
    choice = random.next_double() * sum(weights)
    index = len(weights) - 1
    for i, weight in enumerate(weights):
        choice -= weight
        if choice < 0:
            index = i
            break
    pattern = patterns[index]
    buy = o["minimumBuyPrice"] + random.next_int(o["maximumBuyPrice"] - o["minimumBuyPrice"] + 1)

    def between(key):
        low, high = o[key]
        return low + random.next_double() * (high - low)

    falling = o["declineStart"]
    factors = []
    for _ in range(12):
        factors.append(falling)
        falling -= o["declineMin"] + random.next_double() * (o["declineMax"] - o["declineMin"])
    if pattern == "fluctuating":
        first = random.next_int(3)
        second = 6 + random.next_int(3)
        first_length = 2 + random.next_int(2)
        second_length = 2 + random.next_int(2)
        factors = [
            between(
                "fluctuatingHigh"
                if first <= i < first + first_length or second <= i < second + second_length
                else "fluctuatingLow"
            )
            for i in range(12)
        ]
    elif pattern in ("small_spike", "large_spike"):
        peak = between("smallPeak" if pattern == "small_spike" else "largePeak")
        at = 1 + random.next_int(7)
        for i in range(5):
            factors[at + i] = peak * o["spikeShoulders"][i]
    return dict(
        weekStart=iso(start),
        pattern=pattern,
        buyPrice=buy,
        prices=[max(1, dart_round(buy * value)) for value in factors],
    )
