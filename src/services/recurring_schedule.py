"""週期性收支 occurrence 枚举(§2.12.2 Phase 1.5,MOZE_FEATURE_GAP_SD.md)。

纯函式,无 DB 依赖。取代旧版"每次只推进一步"的
`snapshot_mutator.next_run_from` 单步调用方式 —— 建规则/视窗续产生都需要
"给一个时间范围,一次列出范围内全部发生时间"的批次版本,这里统一收口。

`advanced_rule` 只支持文件明确举例的两种、不做过度泛化:
- `{"type": "weekly_days", "days": [0, 6]}`:每週指定星期几各收一次。
  **`days` 用 Python `datetime.weekday()` 慣例,Monday=0 … Sunday=6**——
  跟 JS(`Date.getDay()`,Sunday=0)/Dart 的慣例不同,前端组装这个 JSON 时
  要注意换算,不要直接照抄前端自己的星期基准。
- `{"type": "monthly_day", "day": 10}`:每隔 `interval` 个月的第 `day` 天收
  一次(超过当月天数会夹断到月底,复用 `snapshot_mutator.add_months` 同款
  逻辑)。
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from ..snapshot_mutator import add_months, next_run_from

ADVANCED_RULE_TYPES = frozenset({"weekly_days", "monthly_day"})

# weekly_days 用逐日扫描实现;end 为 None(长期规则)时用这个硬上限兜底,
# 避免规则组合导致扫描失控(实际会先被 max_count 截住,这只是最后防线)。
_WEEKLY_SCAN_HARD_STOP_DAYS = 366 * 20

# 视窗生成预设策略(§2.12.2 Phase 1.5,文件本身没写死具体数字,这里明确
# 选定,write endpoint / recurring_materializer 都从这里读,不要各自定义
# 一份免得两处数字漂移):
# - 没设 end_at(长期规则)→ 建立当下先生成 12 个月或 200 笔,取先到者;
#   之后由 recurring_materializer.refill_recurring_windows 低频续产生。
DEFAULT_WINDOW_MONTHS = 12
DEFAULT_WINDOW_MAX_OCCURRENCES = 200
# 即使设了 end_at,单次生成也设安全上限,避免
# `frequency=daily, end_at=+50年` 这类极端规则在一次 HTTP 请求里炸出数万笔
# SyncChange;超过上限的尾段改成视窗续产生,对使用者体感无影响。
MAX_OCCURRENCES_PER_GENERATION = 2000
# 视窗续产生:generated_until_at 距今 < 此天数时触发续产生。
REFILL_THRESHOLD_DAYS = 30
REFILL_WINDOW_MONTHS = 12


def enumerate_occurrences(
    *,
    start: datetime,
    end: datetime | None,
    frequency: str,
    interval: int,
    advanced_rule: dict | None,
    max_count: int,
) -> list[datetime]:
    """枚举 `[start, end]`(`end=None` 视为不设上限)范围内的发生时间,最多
    `max_count` 笔。`start` 本身一定是第一笔结果(呼叫方保证 `start` 就是
    "下一个该发生的时间点",不是"上一次已发生的时间点")。"""
    if max_count <= 0:
        return []
    if end is not None and start > end:
        return []
    if advanced_rule is None:
        return _enumerate_simple(start, end, frequency, interval, max_count)
    rule_type = advanced_rule.get("type")
    if rule_type == "weekly_days":
        return _enumerate_weekly_days(start, end, advanced_rule, max_count)
    if rule_type == "monthly_day":
        return _enumerate_monthly_day(start, end, advanced_rule, interval, max_count)
    raise ValueError(f"unsupported advanced_rule type: {rule_type}")


def plan_initial_generation(
    *,
    start: datetime,
    end: datetime | None,
    frequency: str,
    interval: int,
    advanced_rule: dict | None,
) -> tuple[list[datetime], datetime, bool]:
    """建規則(或建交易當下順便設週期)时,决定"这次要一口气生成哪些
    occurrence"。返回 `(occurrences, generated_until_at, fully_generated)`:

    - 有 `end_at`:枚举 `[start, end]` 全部(受 `MAX_OCCURRENCES_PER_GENERATION`
      硬上限保护),`fully_generated=True` 代表确实枚举到了 `end` 本身(没被
      硬上限截断),规则可以标记 `enabled=False`;截断时视窗续产生 loop 会
      在下次 refill 补齐剩下的部分。
    - 没有 `end_at`(长期规则):只生成一个默认视窗
      (`DEFAULT_WINDOW_MONTHS` 个月或 `DEFAULT_WINDOW_MAX_OCCURRENCES` 笔,
      取先到者),`fully_generated` 恒为 `False`(长期规则永远需要续产生)。
    """
    if end is not None:
        occurrences = enumerate_occurrences(
            start=start, end=end, frequency=frequency, interval=interval,
            advanced_rule=advanced_rule, max_count=MAX_OCCURRENCES_PER_GENERATION,
        )
        fully_generated = len(occurrences) < MAX_OCCURRENCES_PER_GENERATION
        generated_until_at = occurrences[-1] if occurrences else start
        return occurrences, generated_until_at, fully_generated

    window_end = add_months(start, DEFAULT_WINDOW_MONTHS)
    occurrences = enumerate_occurrences(
        start=start, end=window_end, frequency=frequency, interval=interval,
        advanced_rule=advanced_rule, max_count=DEFAULT_WINDOW_MAX_OCCURRENCES,
    )
    generated_until_at = occurrences[-1] if occurrences else start
    return occurrences, generated_until_at, False


def _enumerate_simple(
    start: datetime, end: datetime | None, frequency: str, interval: int, max_count: int,
) -> list[datetime]:
    occurrences = [start]
    current = start
    while len(occurrences) < max_count:
        current = next_run_from(current, frequency, interval)
        if end is not None and current > end:
            break
        occurrences.append(current)
    return occurrences


def _enumerate_weekly_days(
    start: datetime, end: datetime | None, advanced_rule: dict, max_count: int,
) -> list[datetime]:
    days = advanced_rule.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("weekly_days rule requires non-empty 'days'")
    days_set = {int(d) for d in days}
    if not days_set.issubset(range(7)):
        raise ValueError("weekly_days 'days' must be within 0..6 (Monday=0 .. Sunday=6)")

    hard_stop = start + timedelta(days=_WEEKLY_SCAN_HARD_STOP_DAYS)
    occurrences: list[datetime] = []
    cursor = start
    while len(occurrences) < max_count and cursor <= hard_stop:
        if end is not None and cursor > end:
            break
        if cursor.weekday() in days_set:
            occurrences.append(cursor)
        cursor = cursor + timedelta(days=1)
    return occurrences


def _on_month_day(dt: datetime, day: int) -> datetime:
    clamped = min(day, calendar.monthrange(dt.year, dt.month)[1])
    return dt.replace(day=clamped)


def _enumerate_monthly_day(
    start: datetime, end: datetime | None, advanced_rule: dict, interval: int, max_count: int,
) -> list[datetime]:
    day = advanced_rule.get("day")
    if not isinstance(day, int) or not (1 <= day <= 31):
        raise ValueError("monthly_day rule requires 'day' in 1..31")
    interval = max(1, int(interval or 1))

    first = _on_month_day(start, day)
    if first < start:
        first = _on_month_day(add_months(first, interval), day)
    if end is not None and first > end:
        return []

    occurrences = [first]
    cursor = first
    while len(occurrences) < max_count:
        cursor = _on_month_day(add_months(cursor, interval), day)
        if end is not None and cursor > end:
            break
        occurrences.append(cursor)
    return occurrences
