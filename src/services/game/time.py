"""Farm instants are UTC; civil days use the existing business-time boundary."""

from datetime import date, datetime, timedelta, timezone

from .. import business_time

HOUR = timedelta(hours=1)


def utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(
        timezone.utc
    )


def iso(value: datetime | str) -> str:
    """Dart toIso8601String always emits milliseconds; microseconds only if nonzero."""
    value = utc(value)
    return value.isoformat(
        timespec="microseconds" if value.microsecond % 1000 else "milliseconds"
    ).replace("+00:00", "Z")


def civil(value: datetime | str) -> datetime:
    return utc(value).astimezone(business_time.business_tz())


def day_key(value: datetime | str) -> str:
    return business_time.to_business_date(utc(value)).isoformat()


def day_start(value: datetime | str) -> datetime:
    return business_time.business_date_start_utc(business_time.to_business_date(utc(value)))


def week_start(value: datetime | str) -> datetime:
    day = business_time.to_business_date(utc(value))
    return business_time.business_date_start_utc(day - timedelta(days=day.weekday()))


def season(value: datetime | str) -> str:
    month = business_time.to_business_date(utc(value)).month
    return (
        "spring"
        if 3 <= month <= 5
        else "summer"
        if 6 <= month <= 8
        else "autumn"
        if 9 <= month <= 11
        else "winter"
    )


def next_season(value: datetime | str) -> datetime:
    day = business_time.to_business_date(utc(value))
    month = (
        3
        if day.month <= 2
        else 6
        if day.month <= 5
        else 9
        if day.month <= 8
        else 12
        if day.month <= 11
        else 3
    )
    return business_time.business_date_start_utc(date(day.year + (day.month == 12), month, 1))


def hour_start(value: datetime | str) -> datetime:
    return utc(value).replace(minute=0, second=0, microsecond=0)


def market_slot(value: datetime | str) -> int:
    return int(utc(value).timestamp() // 43200)


def market_slot_start(slot: int) -> datetime:
    return datetime.fromtimestamp(slot * 43200, timezone.utc)


def turnip_week_start(value: datetime | str, hour: int = 5) -> datetime:
    shifted = utc(value) - timedelta(hours=hour)
    day = business_time.to_business_date(shifted)
    sunday = day - timedelta(days=(day.weekday() + 1) % 7)
    return business_time.business_date_start_utc(sunday) + timedelta(hours=hour)


def milliseconds_since_epoch(value: datetime | str) -> int:
    return (utc(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
