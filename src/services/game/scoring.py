"""Farm scoring only accepts already-projected bookkeeping amounts."""

import calendar
import math
import statistics
from datetime import timedelta

from .growth import dart_round
from .time import civil, day_key, iso, utc, week_start


def reward_breakdown(points: float, streak: int, config: dict) -> dict:
    o = config["scoring"]
    base = 0.0 if points == 0 else float(o["baseCoins"])
    per_point = 0.0 if points == 0 else o["coinsPerPoint"] * points
    capped = min(o["maximumCoins"], base + per_point)
    bonus = o["streakBonus"] * min(streak, o["maximumStreak"])
    return dict(
        base=base,
        points=per_point,
        perPoint=o["coinsPerPoint"],
        cap=o["maximumCoins"],
        capped=capped,
        bonusRate=bonus,
        coins=dart_round(capped * (1 + bonus)),
    )


def weekly_budget(week, monthly_budgets: dict) -> float | None:
    week = week_start(week)
    total = 0.0
    for i in range(7):
        d = civil(week + timedelta(days=i))
        budget = monthly_budgets.get(f"{d.year}-{d.month:02}")
        if budget is None or budget <= 0:
            return None
        if not math.isfinite(budget):
            raise ValueError("預算必須是有限金額")
        total += budget / calendar.monthrange(d.year, d.month)[1]
    return total


def score(
    week, entries, config: dict, monthly_budgets: dict | None = None, previous_streak: int = 0
) -> dict:
    if previous_streak < 0:
        raise ValueError("previous streak must not be negative")
    week = week_start(week)
    end = week + timedelta(days=7)
    o = config["scoring"]
    rows = []
    for entry in entries:
        e = dict(entry)
        for key in ("amount", "budgetAmount"):
            value = e.get(key, e["amount"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("金額必須是有限非負數")
        if (
            not e.get("excludeFromStats", False)
            and not e.get("isRefund", False)
            and e["type"] in ("expense", "income")
        ):
            e["happenedAt"] = utc(e["happenedAt"])
            e["createdAt"] = utc(e["createdAt"]) if e.get("createdAt") is not None else None
            rows.append(e)

    def within(at, start, stop):
        return start <= at < stop

    def total(values, kind, budget=False):
        return sum(
            e.get("budgetAmount", e["amount"]) if budget else e["amount"]
            for e in values
            if e["type"] == kind
        )

    days = len(
        {
            day_key(e["createdAt"])
            for e in rows
            if e["createdAt"] is not None and within(e["createdAt"], week, end)
        }
    )
    habit = days / 7
    current = [e for e in rows if within(e["happenedAt"], week, end)]
    expense = total(current, "expense")
    baseline = []
    for i in range(1, o["baselineWeeks"] + 1):
        start = week - timedelta(days=7 * i)
        prior = [e for e in rows if within(e["happenedAt"], start, start + timedelta(days=7))]
        if prior:
            baseline.append(total(prior, "expense"))
    control = None
    suspicious = False
    if expense > 0 or any(e > 0 for e in baseline):
        if len(baseline) < o["minimumBaselineWeeks"]:
            control = o["newcomerControl"]
        else:
            median = statistics.median(baseline)
            ratio = expense / median if median > 0 else math.inf if expense > 0 else 0.0
            a, b, c = o["controlBreaks"]
            control = (
                1
                if ratio <= a
                else 1 - (ratio - a)
                if ratio <= b
                else 1 - (b - a) - (ratio - b) * o["controlHighSlope"]
                if ratio <= c
                else 0
            )
            if expense < median * o["suspiciousRatio"] and days < o["suspiciousDays"]:
                suspicious = True
        control = min(1, max(0, control)) * min(1, days / o["minimumControlDays"])
        if suspicious:
            control = min(control, o["suspiciousCap"])
    if expense == 0 and total(current, "income") > 0 and days > 0:
        control = 1.0
    rolling = [
        e for e in rows if within(e["happenedAt"], end - timedelta(days=o["savingDays"]), end)
    ]
    income = total(rolling, "income")
    saving = (
        min(1.0, max(0.0, (income - total(rolling, "expense")) / income / o["savingTarget"]))
        if income > 0
        else None
    )
    week_budget = weekly_budget(week, monthly_budgets or {})
    budget = None
    if week_budget is not None:
        usage = total(current, "expense", budget=True) / week_budget
        budget = min(
            1,
            max(
                0,
                1 - (usage - o["budgetFullRatio"]) / (o["budgetZeroRatio"] - o["budgetFullRatio"]),
            ),
        )
    values = dict(habit=habit, control=control, saving=saving, budget=budget)
    numerator = denominator = 0.0
    for key, value in values.items():
        if value is not None:
            weight = o["weights"][key]
            numerator += weight * value
            denominator += weight
    points = 0.0 if days == 0 else min(100.0, max(0.0, 100 * numerator / denominator))
    streak = previous_streak + 1 if points >= o["streakThreshold"] else 0
    return dict(
        weekStart=iso(week),
        **values,
        score=points,
        coins=reward_breakdown(points, streak, config)["coins"],
        streak=streak,
    )


def due_weeks(enabled_at, now, config: dict, settled_weeks=()) -> list:
    current = week_start(now)
    latest = current - timedelta(days=14 if utc(now) < current + timedelta(minutes=5) else 7)
    first = week_start(enabled_at)
    return [
        week
        for i in range(config["scoring"]["catchUpWeeks"] - 1, -1, -1)
        if (week := latest - timedelta(days=i * 7)) >= first and day_key(week) not in settled_weeks
    ]
