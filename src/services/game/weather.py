"""Public coarse cities only; missing hours retain neutral growth behavior."""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from .engine import iso, now_utc
from .models import GameFarm, GameWeather

CITIES = json.loads(Path(__file__).with_name("cities.json").read_text())


def city_coordinates(code):
    if isinstance(code, str) and code in CITIES:
        return CITIES[code]
    if isinstance(code, str) and code.startswith("geo:"):
        parts = code.split(":")
        if len(parts) == 3:
            lat, lon = map(float, parts[1:])
            if (
                math.isfinite(lat)
                and math.isfinite(lon)
                and abs(lat) <= 90
                and abs(lon) <= 180
                and f"geo:{lat:.1f}:{lon:.1f}" == code
            ):
                return lat, lon
    raise ValueError("無效城市")


def fetch(city):
    lat, lon = city_coordinates(city)
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,cloud_cover,wind_gusts_10m,relative_humidity_2m,is_day",
        "past_days": 2,
        "forecast_days": 2,
        "timezone": "GMT",
        "timeformat": "unixtime",
        "wind_speed_unit": "kmh",
    }
    response = httpx.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=8)
    response.raise_for_status()
    hourly = response.json()["hourly"]
    rows = []
    for i, t in enumerate(hourly["time"]):
        try:
            values = [
                hourly[k][i]
                for k in [
                    "temperature_2m",
                    "precipitation",
                    "cloud_cover",
                    "wind_gusts_10m",
                    "relative_humidity_2m",
                    "is_day",
                ]
            ]
            if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
                continue
            temp, precip, cloud, gust, humidity, is_day = values
            if precip < 0 or not 0 <= cloud <= 100 or gust < 0 or not 0 <= humidity <= 100:
                continue
            at = datetime.fromtimestamp(t, timezone.utc)
            rows.append(
                (
                    at,
                    {
                        "at": iso(at),
                        "temperature": temp,
                        "precipitation": precip,
                        "cloud": cloud,
                        "gust": gust,
                        "humidity": humidity,
                        "isDay": bool(is_day),
                    },
                )
            )
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return rows


def refresh_weather(db, only_user=None):
    now = now_utc()
    query = select(GameFarm.state).where(GameFarm.enabled.is_(True))
    if only_user:
        query = query.where(GameFarm.user_id == only_user)
    cities = list(db.scalars(query))
    codes = sorted(set(state["cityCode"] for state in cities))
    needed = []
    for code in codes:
        from .engine import instant

        last = db.scalar(
            select(GameWeather.fetched_at)
            .where(GameWeather.city_code == code)
            .order_by(GameWeather.fetched_at.desc())
            .limit(1)
        )
        if last is None or instant(last) <= now - timedelta(hours=1):
            needed.append(code)
    # End read transaction before external I/O; no pool connection is held for fetch.
    db.rollback()
    saved = 0
    failures = 0
    for code in needed:
        try:
            rows = fetch(code)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            failures += 1
            continue
        for at, data in rows:
            row = db.get(GameWeather, (code, at))
            if row:
                row.data = data
                row.fetched_at = now
            else:
                db.add(GameWeather(city_code=code, hour=at, data=data, fetched_at=now))
        db.commit()
        saved += len(rows)
    db.execute(delete(GameWeather).where(GameWeather.hour < now - timedelta(days=60)))
    db.commit()
    return {"cities": len(needed), "hours": saved, "failures": failures}
