"""Deterministic finite-window market quotes, identical to FarmMarket."""

import math

from .growth import crop, dart_round
from .random import FarmRandom, farm_seed
from .time import iso, market_slot, market_slot_start, season


def supply_factor(quantity: int, config: dict) -> float:
    if quantity < 0:
        raise ValueError("quantity must not be negative")
    o = config["market"]
    return min(
        o["maximumSupply"],
        max(
            o["minimumSupply"],
            1 - o["supplySlope"] * math.log(1 + quantity / o["referenceVolume"]) / math.log(2),
        ),
    )


def fluctuation(crop_id: str, slot: int, config: dict) -> float:
    value = 1.0
    o = config["market"]
    for i in range(slot - o["walkWindowSlots"] + 1, slot + 1):
        random = FarmRandom(farm_seed(f"market|{crop_id}|{i}|{config['version']}"))
        shock = (2 * random.next_double() - 1) * o["stepVolatility"]
        value += shock + (1 - value) * o["meanReversion"]
    return value


def market_price(
    crop_id: str, now, config: dict, supply_quantity: int = 0, event_multiplier: float = 1
) -> int:
    if not math.isfinite(event_multiplier) or event_multiplier <= 0:
        raise ValueError("event multiplier must be finite and positive")
    row = crop(config, crop_id)
    seasonal = 1.0 if season(now) in row["seasons"] else config["market"]["offSeasonMultiplier"]
    return max(
        1,
        dart_round(
            row["basePrice"]
            * seasonal
            * supply_factor(supply_quantity, config)
            * fluctuation(crop_id, market_slot(now), config)
            * event_multiplier
        ),
    )


def market_prices(now, config: dict) -> dict:
    return {row["id"]: market_price(row["id"], now, config) for row in config["crops"]}


def market_history(crop_id: str, now, config: dict) -> list[dict]:
    slot = market_slot(now)
    return [
        dict(at=iso(t := market_slot_start(i)), price=market_price(crop_id, t, config))
        for i in range(slot - config["market"]["historySlots"] + 1, slot + 1)
    ]
