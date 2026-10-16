"""PH02 profile.notification_settings 契約，對齊 App CardReminderSettings。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .business_time import business_date_start_utc


@dataclass(frozen=True)
class CardReminderSettings:
    hour: int = 10
    minute: int = 0
    advance: bool = False
    days: int = 3
    billing: bool = False
    due: bool = False
    maxdays: int = 3
    legacy: bool = False


def effective_settings(raw: str | dict | None, account_id: str) -> CardReminderSettings:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        data = None
    data = data if isinstance(data, dict) else {}
    overrides = data.get('cc_card_overrides')
    override = overrides.get(account_id) if isinstance(overrides, dict) else None
    custom = isinstance(override, dict)
    if custom:
        data = override
    version = data.get('cc_settings_version')
    legacy = not custom and (type(version) is not int or version != 1)

    def number(key: str, fallback: int, low: int, high: int) -> int:
        value = data.get(key)
        return value if type(value) is int and low <= value <= high else fallback

    return CardReminderSettings(
        hour=number('cc_reminder_hour', 10, 0, 23),
        minute=number('cc_reminder_minute', 0, 0, 59),
        advance=legacy or data.get('cc_reminder_enabled') is True,
        days=7 if legacy else number('cc_reminder_days', 3, 1, 7),
        billing=legacy or data.get('cc_billing_reminder_enabled') is True,
        due=legacy or data.get('cc_due_reminder_enabled') is True,
        maxdays=2 if legacy else number('cc_due_reminder_maxdays', 3, 1, 10),
        legacy=legacy,
    )


def reminder_kinds(settings: CardReminderSettings, *, today: date,
                   cycle_end: date, due_date: date, now: datetime) -> list[str]:
    # 既有 Cloud 沒有時間 gate。只有明確採用新版設定才開始依時間發送。
    if not settings.legacy:
        at = business_date_start_utc(today) + timedelta(hours=settings.hour, minutes=settings.minute)
        if now < at:
            return []
    kinds = []
    if settings.billing and today == cycle_end + timedelta(days=1):
        kinds.append('statement_closed')
    if settings.advance and today == due_date - timedelta(days=settings.days) and today > cycle_end:
        kinds.append('due_soon')
    offset = (today - due_date).days
    if settings.due:
        if offset == 0:
            kinds.append('due_today')
        elif offset > 0 and (settings.legacy or offset < settings.maxdays):
            kinds.append('overdue')
    # 舊排程同一天原本只取第一個優先種類。
    return kinds[:1] if settings.legacy else kinds
