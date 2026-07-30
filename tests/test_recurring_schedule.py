from datetime import datetime, timezone

import pytest

from src.services.recurring_schedule import enumerate_occurrences


def test_simple_monthly_no_end_respects_max_count():
    start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=None, frequency="monthly", interval=1,
        advanced_rule=None, max_count=5,
    )
    assert len(occ) == 5
    assert occ[0] == start
    assert occ[1] == datetime(2026, 2, 15, tzinfo=timezone.utc)
    assert occ[4] == datetime(2026, 5, 15, tzinfo=timezone.utc)


def test_simple_with_end_stops_at_boundary():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 15, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=end, frequency="monthly", interval=1,
        advanced_rule=None, max_count=200,
    )
    # Jan 1, Feb 1, Mar 1 都 <= end;Apr 1 超过 end,截止
    assert occ == [
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 3, 1, tzinfo=timezone.utc),
    ]


def test_start_after_end_returns_empty():
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert enumerate_occurrences(
        start=start, end=end, frequency="monthly", interval=1,
        advanced_rule=None, max_count=10,
    ) == []


def test_weekly_days_sat_sun():
    # 2026-01-05 是週一(Monday=0)
    start = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=None, frequency="weekly", interval=1,
        advanced_rule={"type": "weekly_days", "days": [5, 6]},  # Sat, Sun
        max_count=4,
    )
    assert len(occ) == 4
    assert [d.weekday() for d in occ] == [5, 6, 5, 6]
    assert occ[0] == datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)  # 第一个週六
    assert occ[1] == datetime(2026, 1, 11, 9, 0, tzinfo=timezone.utc)  # 週日
    # 每个 occurrence 都保留 start 的 time-of-day
    assert all(d.hour == 9 for d in occ)


def test_weekly_days_invalid_days_raises():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        enumerate_occurrences(
            start=start, end=None, frequency="weekly", interval=1,
            advanced_rule={"type": "weekly_days", "days": []},
            max_count=4,
        )
    with pytest.raises(ValueError):
        enumerate_occurrences(
            start=start, end=None, frequency="weekly", interval=1,
            advanced_rule={"type": "weekly_days", "days": [7]},
            max_count=4,
        )


def test_monthly_day_basic():
    start = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=None, frequency="monthly", interval=1,
        advanced_rule={"type": "monthly_day", "day": 10},
        max_count=3,
    )
    assert occ == [
        datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 10, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
    ]


def test_monthly_day_start_after_target_day_skips_to_next_month():
    # start 在 15 号,目标是每月 10 号 → 第一次落在下个月
    start = datetime(2026, 1, 15, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=None, frequency="monthly", interval=1,
        advanced_rule={"type": "monthly_day", "day": 10},
        max_count=1,
    )
    assert occ == [datetime(2026, 2, 10, tzinfo=timezone.utc)]


def test_monthly_day_clamps_to_month_end():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    occ = enumerate_occurrences(
        start=start, end=None, frequency="monthly", interval=1,
        advanced_rule={"type": "monthly_day", "day": 31},
        max_count=3,
    )
    # Jan 31, Feb(28,非闰年) 夹断到 28, Mar 31
    assert occ[0] == datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert occ[1] == datetime(2026, 2, 28, tzinfo=timezone.utc)
    assert occ[2] == datetime(2026, 3, 31, tzinfo=timezone.utc)


def test_monthly_day_invalid_day_raises():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        enumerate_occurrences(
            start=start, end=None, frequency="monthly", interval=1,
            advanced_rule={"type": "monthly_day", "day": 32},
            max_count=1,
        )


def test_unsupported_advanced_rule_type_raises():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        enumerate_occurrences(
            start=start, end=None, frequency="monthly", interval=1,
            advanced_rule={"type": "yearly_on_date"},
            max_count=1,
        )


def test_max_count_zero_returns_empty():
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    assert enumerate_occurrences(
        start=start, end=None, frequency="daily", interval=1,
        advanced_rule=None, max_count=0,
    ) == []
