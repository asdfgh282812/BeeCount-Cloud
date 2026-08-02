"""信用卡帳單週期計算(§2.9 信用卡管理整組功能,Phase 4,MOZE_FEATURE_GAP_SD.md)。

純函式,無 DB 依賴,方便直接寫單元測試鎖定邊界(月底夾斷/跨年)。
`billing_day`/`payment_due_day` 是帳戶既有欄位(1..31),超過當月天數時
夾斷到月底,沿用 `snapshot_mutator.add_months`/`services.recurring_schedule`
同款 `calendar.monthrange` 邏輯,這裡獨立實作一份(輸入輸出都是 `date`,
不是那两处用的 `datetime`),避免为了共用还要来回转型。

帳單週期定義(對齊台灣主要銀行常見設計):
- `billing_day`:結帳日,每月這天結一次帳單,涵蓋「上次結帳日(不含)~
  這次結帳日(含)」的消費。
- `payment_due_day`:繳款截止日 —— 從結帳日所在月份開始找第一個
  `>= 結帳日` 的 `payment_due_day`;數字上早於結帳日就代表繳款日落在
  下個月,否則跟結帳日同月。

**月底夾斷陷阱**:`billing_day=31` 這種值在二月會被夾斷成 28/29 號。往
前後平移一個月時,必須每次都拿「原始 `billing_day`」重新夾斷,不能拿
「已經夾斷過的那個 `date` 的 `.day`」去推算下個月 —— 否則 Feb 28(從 31
夾斷來的)往前推一個月会变成 Jan 28,而不是正确的 Jan 31。下面所有平移都
经过 `_shift_billing_day`,不直接用 `date.day`。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import TypedDict


def _clamp_day(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def _shift_billing_day(d: date, billing_day: int, months: int) -> date:
    """回傳 `d` 所在月份平移 `months` 個月之後、當月的 `billing_day` 那天
    (夾斷到月底)。用的是傳入的 `billing_day`,不是 `d.day`(`d` 可能是被
    夾斷過的值,比如 Feb 28,不能拿它的 28 反推別的月份)。"""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    return _clamp_day(year, month, billing_day)


def billing_cycle_containing(as_of: date, billing_day: int) -> tuple[date, date]:
    """回傳 `as_of` 所落入的帳單週期 `(cycle_start_exclusive, cycle_end_inclusive)`。

    `cycle_end` 是 `>= as_of` 最近的一個結帳日(`as_of` 剛好是結帳日時
    `cycle_end == as_of`,今天消費算進今天結的這期);`cycle_start` 是往前
    推一個月的結帳日(exclusive,該日之後的消費才算進本期)。"""
    this_month_end = _clamp_day(as_of.year, as_of.month, billing_day)
    cycle_end = this_month_end if as_of <= this_month_end else _shift_billing_day(this_month_end, billing_day, 1)
    cycle_start = _shift_billing_day(cycle_end, billing_day, -1)
    return cycle_start, cycle_end


def most_recently_closed_cycle(as_of: date, billing_day: int) -> tuple[date, date]:
    """回傳 `as_of` 當下「最近一次已經結束」的帳單週期(`as_of` 剛好是結帳日
    時視為當天已結束)。這是「目前應繳金額」對應的那一期;跟
    `billing_cycle_containing` 的差別是後者算的是「今天消費會落到哪一期」
    (通常是還沒結束的那一期,除非今天正好是結帳日,兩者才會重合)。"""
    this_month_end = _clamp_day(as_of.year, as_of.month, billing_day)
    cycle_end = this_month_end if as_of >= this_month_end else _shift_billing_day(this_month_end, billing_day, -1)
    cycle_start = _shift_billing_day(cycle_end, billing_day, -1)
    return cycle_start, cycle_end


def shift_cycle(cycle_start: date, cycle_end: date, billing_day: int, months: int) -> tuple[date, date]:
    """把一組 `(cycle_start, cycle_end)` 整體往前/後平移 `months` 個帳單週期
    (§2.9 帳單週期瀏覽,2026-08-02 補強)。`months` 可為負數(往回看歷史
    帳單)。每次平移都經過 `_shift_billing_day` 重新用原始 `billing_day`
    夾斷,不拿 `cycle_end.day` 反推(同檔案頭注釋的月底夾斷陷阱)。"""
    new_end = _shift_billing_day(cycle_end, billing_day, months)
    new_start = _shift_billing_day(new_end, billing_day, -1)
    return new_start, new_end


def due_date_for_cycle_end(cycle_end: date, payment_due_day: int) -> date:
    """給定一個結帳日,算出對應的繳款截止日:從結帳日所在月份開始找第一個
    `>= cycle_end` 的 `payment_due_day`;數字上早於 `cycle_end` 就落到下個月。"""
    candidate = _clamp_day(cycle_end.year, cycle_end.month, payment_due_day)
    if candidate < cycle_end:
        candidate = _shift_billing_day(candidate, payment_due_day, 1)
    return candidate


class InterestFreeSuggestion(TypedDict):
    as_of: date
    billing_day: int
    payment_due_day: int
    current_cycle_start: date
    current_cycle_end: date
    current_cycle_due_date: date
    next_cycle_start: date
    next_cycle_end: date
    next_cycle_due_date: date
    recommended_purchase_after: date
    min_interest_free_days: int
    max_interest_free_days: int


def interest_free_suggestion(
    as_of: date, billing_day: int, payment_due_day: int,
) -> InterestFreeSuggestion:
    """免息期推薦(§2.9 doc 原文「這個月哪天前買,下次繳款日最遠」)。

    今天消費會落入 `current_cycle`(通常還沒結束的那期),繳款日是
    `current_cycle_due_date`;等到 `current_cycle_end` 之後
    (即 `next_cycle_start`)才消費,會落入下一期,繳款日推到
    `next_cycle_due_date`,免息天數最長。同時回傳兩種情境供 UI 對照。
    """
    current_start, current_end = billing_cycle_containing(as_of, billing_day)
    current_due = due_date_for_cycle_end(current_end, payment_due_day)
    next_start = current_end + timedelta(days=1)
    next_end = _shift_billing_day(current_end, billing_day, 1)
    next_due = due_date_for_cycle_end(next_end, payment_due_day)
    return {
        "as_of": as_of,
        "billing_day": billing_day,
        "payment_due_day": payment_due_day,
        "current_cycle_start": current_start,
        "current_cycle_end": current_end,
        "current_cycle_due_date": current_due,
        "next_cycle_start": next_start,
        "next_cycle_end": next_end,
        "next_cycle_due_date": next_due,
        "recommended_purchase_after": current_end,
        "min_interest_free_days": (current_due - as_of).days,
        "max_interest_free_days": (next_due - next_start).days,
    }
