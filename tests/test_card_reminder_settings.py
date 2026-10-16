from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.services import business_time
from src.services.card_reminder_settings import effective_settings, reminder_kinds


def explicit(**kwargs):
    return {'cc_settings_version': 1, 'cc_billing_reminder_enabled': True,
            'cc_reminder_enabled': True, 'cc_reminder_days': 3,
            'cc_due_reminder_enabled': True, 'cc_due_reminder_maxdays': 3, **kwargs}


def test_legacy_and_override_and_reset():
    data = {'cc_reminder_enabled': False, 'cc_card_overrides': {'A': explicit(cc_reminder_days=5)}}
    assert effective_settings(data, 'B').legacy
    assert effective_settings(data, 'B').advance
    assert effective_settings(data, 'A').days == 5
    data.update(explicit(cc_reminder_days=1))
    assert effective_settings(data, 'A').days == 5
    assert effective_settings(data, 'B').days == 1
    del data['cc_card_overrides']['A']
    assert effective_settings(data, 'A').days == 1
    assert not effective_settings(data, 'A').legacy


@pytest.mark.parametrize('raw', ['bad json', '[]', None, {'cc_card_overrides': []}])
def test_malformed_settings_keep_legacy(raw):
    assert effective_settings(raw, 'A').legacy


def test_invalid_ranges_are_normalized():
    s = effective_settings(explicit(cc_reminder_hour=99, cc_reminder_days=True, cc_due_reminder_maxdays=99), 'A')
    assert (s.hour, s.days, s.maxdays) == (10, 3, 3)


@pytest.mark.parametrize(('hour', 'minute', 'expected'), [(0, 30, []), (7, 59, []), (9, 59, []), (10, 0, ['due_today']), (23, 59, ['due_today'])])
def test_business_time_boundary(monkeypatch, hour, minute, expected):
    monkeypatch.setattr(business_time, 'business_tz', lambda: ZoneInfo('Asia/Taipei'))
    now = datetime(2026, 10, 16, hour, minute, tzinfo=ZoneInfo('Asia/Taipei')).astimezone(timezone.utc)
    today = business_time.business_today(now)
    assert today == date(2026, 10, 16)
    assert reminder_kinds(effective_settings(explicit(), 'A'), today=today,
                          cycle_end=date(2026, 10, 5), due_date=today, now=now) == expected


def test_same_day_kinds_and_continuous_window():
    settings = effective_settings(explicit(), 'A')
    now = datetime(2026, 10, 6, 23, tzinfo=timezone.utc)
    assert reminder_kinds(settings, today=date(2026, 10, 6), cycle_end=date(2026, 10, 5),
                          due_date=date(2026, 10, 9), now=now) == ['statement_closed', 'due_soon']
    for day in [1, 2, 3]:
        kinds = reminder_kinds(settings, today=date(2026, 10, 9 + day), cycle_end=date(2026, 10, 5),
                               due_date=date(2026, 10, 9), now=datetime(2026, 10, 9 + day, 23, tzinfo=timezone.utc))
        assert kinds == (['overdue'] if day < 3 else [])
