"""彈性結帳日 + 繳款期限(MOZE 對標,alembic 0070)純函式測試。

對應 App `test/utils/billing_schedule_test.dart`——兩邊的算法必須一致,這裡用同一組 2026 年
日期(10/31 週六、11/1 週日、11/2 週一、10/9 週五、10/10 週六、10/11 週日、10/12 週一)。
另外用「舊算法的原樣複製」對拍:沒有任何調整時,新實作必須跟舊實作逐日完全相同。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from src.services import billing_schedule as bs
from src.services import credit_card
from src.services.billing_schedule import BillingSchedule, NonWorkingCalendar


def sched(day, *, rule=None, holidays=(), overrides=None, due=None, mode=None):
    return BillingSchedule(
        billing_day=day,
        holiday_rule=rule,
        region="TW" if rule else None,
        overrides=overrides or {},
        payment_due_day=due,
        due_mode=mode,
        calendar=NonWorkingCalendar(frozenset(holidays)),
    )


# --------------------------------------------------------------------------- #
# 舊算法原樣複製(2026-10-12 之前的 credit_card.py),當對拍基準                #
# --------------------------------------------------------------------------- #


def _old_clamp(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _old_shift(d, billing_day, months):
    total = d.month - 1 + months
    return _old_clamp(d.year + total // 12, total % 12 + 1, billing_day)


def _old_containing(as_of, billing_day):
    this_month_end = _old_clamp(as_of.year, as_of.month, billing_day)
    end = this_month_end if as_of <= this_month_end else _old_shift(this_month_end, billing_day, 1)
    return _old_shift(end, billing_day, -1), end


def _old_closed(as_of, billing_day):
    this_month_end = _old_clamp(as_of.year, as_of.month, billing_day)
    end = this_month_end if as_of >= this_month_end else _old_shift(this_month_end, billing_day, -1)
    return _old_shift(end, billing_day, -1), end


def test_no_adjustment_matches_old_algorithm_every_day_every_billing_day():
    start = date(2025, 11, 1)
    for billing_day in range(1, 32):
        d = start
        for _ in range(0, 450, 3):
            assert credit_card.billing_cycle_containing(d, billing_day) == _old_containing(d, billing_day)
            assert credit_card.most_recently_closed_cycle(d, billing_day) == _old_closed(d, billing_day)
            d += timedelta(days=3)


def test_shift_cycle_matches_old_algorithm():
    for billing_day in (1, 15, 28, 29, 30, 31):
        base_start, base_end = _old_closed(date(2026, 3, 20), billing_day)
        for months in range(-14, 3):
            expected_end = _old_shift(base_end, billing_day, months)
            expected_start = _old_shift(expected_end, billing_day, -1)
            got = credit_card.shift_cycle(base_start, base_end, billing_day, months)
            assert got == (expected_start, expected_end)


# --------------------------------------------------------------------------- #
# 結帳日遇非工作日                                                             #
# --------------------------------------------------------------------------- #


def test_postpone_saturday_closing_to_monday():
    s = sched(31, rule="postpone")
    assert s.closing_date(2026, 10) == date(2026, 11, 2)
    start, end = credit_card.billing_cycle_containing(date(2026, 10, 12), s)
    assert (start, end) == (date(2026, 9, 30), date(2026, 11, 2))


def test_advance_saturday_closing_to_friday():
    assert sched(31, rule="advance").closing_date(2026, 10) == date(2026, 10, 30)


def test_holiday_counts_as_non_working_day():
    s = sched(9, rule="postpone", holidays=[date(2026, 10, 9)])
    assert s.closing_date(2026, 10) == date(2026, 10, 12)


def test_consecutive_holidays_skipped():
    s = sched(8, rule="postpone", holidays=[date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)])
    assert s.closing_date(2026, 10) == date(2026, 10, 13)
    a = sched(12, rule="advance", holidays=[date(2026, 10, 12), date(2026, 10, 9)])
    assert a.closing_date(2026, 10) == date(2026, 10, 8)


def test_no_rule_ignores_holidays():
    s = sched(31, holidays=[date(2026, 10, 30)])
    assert s.closing_date(2026, 10) == date(2026, 10, 31)


def test_nominal_closing_passed_but_actual_not_yet_still_current_cycle():
    s = sched(9, rule="postpone", holidays=[date(2026, 10, 9)])
    assert credit_card.billing_cycle_containing(date(2026, 10, 10), s)[1] == date(2026, 10, 12)
    assert credit_card.billing_cycle_containing(date(2026, 10, 12), s)[1] == date(2026, 10, 12)
    assert credit_card.billing_cycle_containing(date(2026, 10, 13), s)[1] == date(2026, 11, 9)


def test_most_recently_closed_uses_actual_closing_date():
    s = sched(31, rule="postpone")
    # 10/31 順延到 11/2:11/1 時最近已結帳的是 9 月那期(9/30),11/2 當天才算 10 月那期已結。
    assert credit_card.most_recently_closed_cycle(date(2026, 11, 1), s)[1] == date(2026, 9, 30)
    assert credit_card.most_recently_closed_cycle(date(2026, 11, 2), s)[1] == date(2026, 11, 2)


def test_adjacent_cycles_are_contiguous():
    s = sched(31, rule="postpone")
    base_start, base_end = credit_card.most_recently_closed_cycle(date(2026, 10, 12), s)
    prev_end = base_end
    for months in range(1, 8):
        start, end = credit_card.shift_cycle(base_start, base_end, s, months)
        assert start == prev_end  # exclusive 起點 = 上一期實際結帳日
        prev_end = end


def test_shift_cycle_handles_override_that_moves_into_next_month():
    s = sched(31, overrides={"2026-10": "2026-11-02"})
    # 10 月那期結帳日被調到 11/2(跨月),shift 起點的 idx 要找對。
    _start, end = credit_card.shift_cycle(date(2026, 9, 30), date(2026, 11, 2), s, 1)
    assert end == date(2026, 11, 30)
    start2, end2 = credit_card.shift_cycle(date(2026, 9, 30), date(2026, 11, 2), s, 0)
    assert (start2, end2) == (date(2026, 9, 30), date(2026, 11, 2))


# --------------------------------------------------------------------------- #
# 單期覆寫                                                                    #
# --------------------------------------------------------------------------- #


def test_override_wins_and_affects_only_that_cycle():
    s = sched(9, rule="postpone", overrides={"2026-10": "2026-10-14"})
    cur = credit_card.billing_cycle_containing(date(2026, 10, 1), s)
    nxt_start, nxt_end = credit_card.shift_cycle(cur[0], cur[1], s, 1)
    assert cur[1] == date(2026, 10, 14)
    assert nxt_start == date(2026, 10, 14)
    assert nxt_end == date(2026, 11, 9)


def test_cycle_info_reports_default_adjusted_and_next_end():
    s = sched(31, rule="postpone", overrides={"2026-10": "2026-11-03"})
    info = bs.cycle_info(s, date(2026, 11, 3))
    assert info["month_key"] == "2026-10"
    assert info["default_end"] == date(2026, 11, 2)  # 預設 = 套假日規則、不含覆寫
    assert info["adjusted"] is True
    assert info["next_end"] == date(2026, 11, 30)
    plain = bs.cycle_info(sched(31, rule="postpone"), date(2026, 11, 2))
    assert plain["adjusted"] is False


def test_normalize_overrides_filters_bad_entries():
    assert bs.normalize_overrides({"2026-10": "2026-10-14", "bad": "2026-10-14", "2026-11": "oops"}) == {
        "2026-10": "2026-10-14"
    }
    assert bs.normalize_overrides('{"2026-11":"2026-11-03T00:00:00Z"}') == {"2026-11": "2026-11-03"}
    assert bs.normalize_overrides("not json") == {}
    assert bs.normalize_overrides(None) == {}
    assert bs.normalize_overrides({"2026-13": "2026-10-14"}) == {}


def test_encode_overrides_sorted_and_none_when_empty():
    assert bs.encode_overrides({}) is None
    assert bs.encode_overrides({"2026-11": "2026-11-03", "2026-10": "2026-10-14"}) == (
        '{"2026-10": "2026-10-14", "2026-11": "2026-11-03"}'
    )


# --------------------------------------------------------------------------- #
# 繳款期限                                                                    #
# --------------------------------------------------------------------------- #


def test_due_fixed_day_moze_example():
    # 週期 6/16–7/15、期限每月 3 號 → 8/3
    assert sched(15, due=3).due_date_for_cycle_end(date(2026, 7, 15)) == date(2026, 8, 3)
    assert sched(15, due=20).due_date_for_cycle_end(date(2026, 7, 15)) == date(2026, 7, 20)


def test_due_fixed_day_equal_to_closing_rolls_to_next_month():
    # 跟 App 一致:繳款日必須嚴格晚於結帳日,數字相同就落到下個月(2026-10-12 對齊)
    assert sched(15, due=15).due_date_for_cycle_end(date(2026, 7, 15)) == date(2026, 8, 15)
    assert sched(15, due=15).due_date_for_cycle_end(date(2026, 12, 15)) == date(2027, 1, 15)
    # 結帳日被假日順延後(實際 7/17),期限 15 號 → 下個月
    assert sched(15, due=15).due_date_for_cycle_end(date(2026, 7, 17)) == date(2026, 8, 15)


def test_due_days_after_closing_moze_example():
    s = sched(15, due=7, mode="days_after_closing")
    assert s.due_date_for_cycle_end(date(2026, 7, 15)) == date(2026, 7, 22)
    assert s.due_date_for_cycle_end(date(2026, 12, 28)) == date(2027, 1, 4)


def test_due_follows_actual_closing_date():
    s = sched(31, rule="postpone", due=7, mode="days_after_closing")
    _start, end = credit_card.billing_cycle_containing(date(2026, 10, 12), s)
    assert credit_card.due_date_for_cycle_end(end, s) == date(2026, 11, 9)


def test_due_date_for_cycle_end_int_still_works_and_schedule_requires_due_day():
    assert credit_card.due_date_for_cycle_end(date(2026, 3, 15), 10) == date(2026, 4, 10)
    try:
        credit_card.due_date_for_cycle_end(date(2026, 3, 15), sched(15))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_interest_free_suggestion_accepts_schedule():
    s = sched(15, due=7, mode="days_after_closing")
    r = credit_card.interest_free_suggestion(date(2026, 7, 1), s)
    assert r["current_cycle_end"] == date(2026, 7, 15)
    assert r["current_cycle_due_date"] == date(2026, 7, 22)
    assert r["next_cycle_end"] == date(2026, 8, 15)
    assert r["next_cycle_due_date"] == date(2026, 8, 22)
    # 舊簽名(整數)不變
    old = credit_card.interest_free_suggestion(date(2026, 7, 1), 15, 10)
    assert old["current_cycle_due_date"] == date(2026, 8, 10)


# --------------------------------------------------------------------------- #
# schedule_from_account:群組退回                                              #
# --------------------------------------------------------------------------- #


class _Acc:
    def __init__(self, **kw):
        self.billing_day = kw.get("billing_day")
        self.payment_due_day = kw.get("payment_due_day")
        self.billing_holiday_rule = kw.get("billing_holiday_rule")
        self.billing_holiday_region = kw.get("billing_holiday_region")
        self.payment_due_mode = kw.get("payment_due_mode")
        self.billing_cycle_overrides_json = kw.get("billing_cycle_overrides_json")


def test_schedule_from_account_child_falls_back_to_group_per_bundle():
    group = _Acc(
        billing_day=31, payment_due_day=20, billing_holiday_rule="advance", billing_holiday_region="JP",
        billing_cycle_overrides_json='{"2026-10":"2026-10-29"}',
    )
    child = _Acc(payment_due_day=7, payment_due_mode="days_after_closing")
    s = bs.schedule_from_account(child, group, calendar_override=bs.WEEKENDS_ONLY)
    assert s is not None
    assert (s.billing_day, s.holiday_rule, s.region) == (31, "advance", "JP")
    assert dict(s.overrides) == {"2026-10": "2026-10-29"}
    assert (s.payment_due_day, s.due_is_days_after_closing) == (7, True)


def test_schedule_from_account_none_without_billing_day_and_ignores_bad_rule():
    assert bs.schedule_from_account(_Acc(payment_due_day=3)) is None
    s = bs.schedule_from_account(_Acc(billing_day=5, billing_holiday_rule="weird"))
    assert s is not None and s.holiday_rule is None and s.region is None
