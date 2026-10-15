"""Stable dictionary interface for the authoritative farm engine.

Pure functions never read database/user state and never grant currency. All writes,
capacities, inventory consumption and rewards must be atomic in the caller.
"""

import json
from pathlib import Path

from .growth import (
    advance,
    advance_result,
    care,
    crop,
    dart_round,
    empty_plot,
    harvest,
    may_miss_season,
    plant,
    ready,
    use_item,
)
from .layout import (
    default_layout,
    default_position,
    first_free,
    layout_cost,
    normalize_layout,
    storage_capacity,
    validate_layout,
)
from .market import market_history, market_price, market_prices, supply_factor
from .scoring import due_weeks, reward_breakdown, score, weekly_budget
from .turnips import (
    turnip_can_buy,
    turnip_expired,
    turnip_generate,
    turnip_quote,
    turnip_start,
    validate_turnip_purchase,
)


def load_config() -> dict:
    # A fresh copy protects cached/shared config from mutation by a caller.
    return json.loads(Path(__file__).with_name("balance_v1.json").read_text())


MAX_EXACT_XP = 2**53 - 1


def xp_progress(xp: int, config: dict) -> tuple[int, int, int]:
    """Level, earned XP in this level, and its integer upgrade cost."""
    options = config["progression"]
    cost = int(options["xpPerLevel"])
    growth = int(options.get("xpGrowthPercent", 0))
    remaining = max(0, xp)
    if growth == 0:
        return 1 + remaining // cost, remaining % cost, cost
    current_level = 1
    while remaining >= cost:
        remaining -= cost
        cost = cost * (100 + growth) // 100
        current_level += 1
    return current_level, remaining, cost


def level(xp: int, config: dict) -> int:
    return xp_progress(xp, config)[0]


def xp_for_level(target_level: int, config: dict) -> int:
    """Total XP to reach a level; each successive cost truncates fractions."""
    if target_level < 1:
        raise ValueError("等級至少為 1")
    options = config["progression"]
    cost = int(options["xpPerLevel"])
    growth = int(options.get("xpGrowthPercent", 0))
    total = 0
    for _ in range(1, target_level):
        total += cost
        if total > MAX_EXACT_XP:
            raise ValueError("等級所需 XP 超出安全整數範圍")
        cost = cost * (100 + growth) // 100
    return total


def can_expand(current_plots: int, xp: int, config: dict) -> bool:
    o = config["progression"]
    return (
        current_plots < o["maximumPlots"]
        and current_plots < o["initialPlots"] + (level(xp, config) - 1) * o["plotsPerLevel"]
    )


def expansion_price(current_plots: int, config: dict) -> int:
    o = config["progression"]
    if current_plots < 0 or current_plots >= o["maximumPlots"]:
        raise ValueError("田地數不在可擴充範圍")
    return dart_round(
        o["expansionBasePrice"] * o["expansionMultiplier"] ** (current_plots - o["initialPlots"])
    )


def water_refresh(water: dict, now, recorded_today: bool, config: dict) -> dict:
    from .time import day_key

    today = day_key(now)
    if today < water["day"]:
        return dict(water)
    new_day = today != water["day"]
    base = config["care"]["dailyWater"] if new_day else water["remaining"]
    claimed = not new_day and water["bonus"]
    return dict(
        day=today,
        remaining=base
        + (config["care"]["bookkeepingWater"] if recorded_today and not claimed else 0),
        bonus=claimed or recorded_today,
    )


def water_use(water: dict) -> dict:
    if water["remaining"] <= 0:
        raise ValueError("今天的水壺已用完")
    return dict(water, remaining=water["remaining"] - 1)


__all__ = [
    "crop",
    "dart_round",
    "empty_plot",
    "plant",
    "ready",
    "care",
    "use_item",
    "advance",
    "advance_result",
    "harvest",
    "may_miss_season",
    "market_price",
    "market_prices",
    "market_history",
    "supply_factor",
    "turnip_generate",
    "turnip_quote",
    "turnip_start",
    "turnip_can_buy",
    "turnip_expired",
    "validate_turnip_purchase",
    "score",
    "due_weeks",
    "weekly_budget",
    "reward_breakdown",
    "validate_layout",
    "default_layout",
    "layout_cost",
    "storage_capacity",
    "first_free",
    "default_position",
    "normalize_layout",
    "load_config",
    "level",
    "xp_progress",
    "xp_for_level",
    "MAX_EXACT_XP",
    "can_expand",
    "expansion_price",
    "water_refresh",
    "water_use",
]
