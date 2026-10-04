"""節日資料讀端點(docs/HOLIDAYS_SD.md)。

全域市場資料,不分 user;仍走 `get_current_user`(授權/最低版本檢查一致,
`tests/test_license_route_audit.py` 不用開例外)。客戶端帶 `known_version`,
跟 server 版本相同時回 `unchanged=true` 且不帶 entries,省流量。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user
from ...models import HolidayEntryRow, User
from ...routers.pats import _utc_iso  # SQLite naive-datetime 坑,同 pats.py:75-87
from ...services.holidays import dataset
from ...services.holidays.catalog import SUPPORTED_COUNTRIES
from ._shared import _READ_SCOPE_DEP, router


class HolidayEntryOut(BaseModel):
    date: str
    country: str
    key: str
    kind: str
    name_zh_tw: str
    name_en: str
    name_local: str
    emoji: str
    color: str
    priority: int


class HolidaysOut(BaseModel):
    version: int
    updated_at: str
    unchanged: bool
    years: list[int]
    countries: list[str]
    entries: list[HolidayEntryOut]


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.get("/holidays", response_model=HolidaysOut)
def get_holidays(
    countries: str | None = Query(default=None, max_length=64, description="逗號分隔,例如 TW,JP;省略=全部"),
    years: str | None = Query(default=None, max_length=64, description="逗號分隔,例如 2026,2027;省略=全部"),
    known_version: int | None = Query(default=None, ge=0),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HolidaysOut:
    country_list = [c.upper() for c in _parse_csv(countries)]
    unknown = [c for c in country_list if c not in SUPPORTED_COUNTRIES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unsupported countries: {','.join(unknown)}")
    try:
        year_list = [int(y) for y in _parse_csv(years)]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="years must be integers") from exc
    if any(y < 1900 or y > 2200 for y in year_list):
        raise HTTPException(status_code=422, detail="year out of range")

    meta = dataset.ensure_dataset(db)
    available_years = sorted(db.scalars(select(distinct(HolidayEntryRow.year))).all())
    out = HolidaysOut(
        version=meta.version,
        updated_at=_utc_iso(meta.updated_at) or "",
        unchanged=True,
        years=list(available_years),
        countries=list(SUPPORTED_COUNTRIES),
        entries=[],
    )
    if known_version is not None and known_version == meta.version:
        return out

    rows = dataset.list_entries(db, countries=country_list or None, years=year_list or None)
    return out.model_copy(update=dict(
        unchanged=False,
        entries=[
            HolidayEntryOut(
                date=r.day.isoformat(),
                country=r.country,
                key=r.key,
                kind=r.kind,
                name_zh_tw=r.name_zh_tw,
                name_en=r.name_en,
                name_local=r.name_local,
                emoji=r.emoji,
                color=r.color,
                priority=r.priority,
            )
            for r in rows
        ],
    ))
