"""欠款分期排程(App v68,MOZE 化)—— 金額拆分與每期日期。

跟 App `lib/data/repositories/debt_repository.dart` 的 `splitInstallmentAmounts`/
`installmentDateAt` 同一套規則,兩端算出來的每期金額和日期必須一致:

- 金額:平均拆成 N 期。整數金額取整到元,有小數的取到分;尾差放在最後一期。
- 日期:第一期往後每月同一天,超過當月天數時取月底(1/31 → 2/28),保留時分。
  App 用手機本地時區算,這裡用業務時區 `LEDGER_TIMEZONE`(台灣使用者兩者一致),
  所以 UTC+8 凌晨建立的分期日期在兩端相同。
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .business_time import business_tz


def split_installment_amounts(total: float, count: int) -> list[float]:
    if count <= 0:
        raise ValueError("count must be > 0")
    cents = round(total * 100)
    unit = 100 if cents % 100 == 0 else 1
    units = cents // unit
    per = units // count
    remainder = units - per * count
    return [
        ((per + (remainder if i == count - 1 else 0)) * unit) / 100
        for i in range(count)
    ]


def installment_date_at(first: datetime, index: int, *, tz: ZoneInfo | None = None) -> datetime:
    """第 `index` 期(0 起算)的時間(UTC aware)。"""
    zone = tz or business_tz()
    aware = first if first.tzinfo is not None else first.replace(tzinfo=timezone.utc)
    local = aware.astimezone(zone)
    m = local.month - 1 + index
    year = local.year + m // 12
    month = m % 12 + 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    shifted = datetime(year, month, day, local.hour, local.minute, tzinfo=zone)
    return shifted.astimezone(timezone.utc)
