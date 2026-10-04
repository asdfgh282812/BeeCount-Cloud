"""節日資料集落地 + 版本管理。

`refresh_dataset` 給 `holiday_dataset_refresh` 排程(每 24 小時)呼叫:
- 目標年份 = 去年(年度報告要用)+ 今年 + 「11 月起」明年——這就是「年底預先
  產生明年」。去年以前的資料保留不動、不再重算。
- 每年產生完算 sha256,跟 `HolidayDatasetMeta.year_hashes` 比,有差才整年替換;
  任一年有變就 version +1(一次 refresh 最多 +1)。`holidays` 套件升級帶進的
  補假修正因此會自動讓客戶端重抓。

讀端點在資料集還沒產生過時(新部署、排程還沒輪到)會先同步呼叫一次
`ensure_dataset`,不讓第一個請求拿到空資料。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import HolidayDatasetMeta, HolidayEntryRow
from .generator import HolidayEntry, generate_year

logger = logging.getLogger(__name__)

_META_ID = 1
# 11 月起預先產生明年。
_NEXT_YEAR_FROM_MONTH = 11


def target_years(today: date) -> list[int]:
    years = [today.year - 1, today.year]
    if today.month >= _NEXT_YEAR_FROM_MONTH:
        years.append(today.year + 1)
    return years


def _hash_entries(entries: list[HolidayEntry]) -> str:
    payload = [{**e.to_wire(), "country": e.country} for e in entries]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_row(e: HolidayEntry) -> HolidayEntryRow:
    return HolidayEntryRow(
        year=e.year,
        country=e.country,
        day=e.date,
        key=e.key,
        kind=e.kind,
        name_zh_tw=e.name_zh_tw,
        name_en=e.name_en,
        name_local=e.name_local,
        emoji=e.emoji,
        color=e.color,
        priority=e.priority,
    )


def get_meta(db: Session) -> HolidayDatasetMeta | None:
    return db.get(HolidayDatasetMeta, _META_ID)


def refresh_dataset(db: Session, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    meta = get_meta(db)
    if meta is None:
        meta = HolidayDatasetMeta(id=_META_ID, version=0, year_hashes={})
        db.add(meta)

    hashes = dict(meta.year_hashes or {})
    changed: list[int] = []
    total = 0
    for year in target_years(today):
        entries = generate_year(year)
        total += len(entries)
        digest = _hash_entries(entries)
        if hashes.get(str(year)) == digest:
            continue
        db.execute(delete(HolidayEntryRow).where(HolidayEntryRow.year == year))
        db.add_all(_to_row(e) for e in entries)
        hashes[str(year)] = digest
        changed.append(year)

    if changed:
        meta.version = (meta.version or 0) + 1
        # JSON 欄位要整個換新 dict,SQLAlchemy 才偵測得到變更。
        meta.year_hashes = hashes
        meta.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "version": meta.version,
        "changed_years": ",".join(str(y) for y in changed) or "-",
        "entries": total,
    }


def ensure_dataset(db: Session) -> HolidayDatasetMeta:
    meta = get_meta(db)
    if meta is not None:
        return meta
    try:
        refresh_dataset(db)
    except IntegrityError:
        # 排程跟第一個讀請求同時建 meta 列;對方已建好就直接用。
        db.rollback()
        logger.info("holiday dataset concurrently initialised; reusing existing")
    meta = get_meta(db)
    if meta is None:  # pragma: no cover — refresh 成功後一定有
        raise RuntimeError("holiday dataset unavailable")
    return meta


def list_entries(
    db: Session,
    *,
    countries: list[str] | None = None,
    years: list[int] | None = None,
) -> list[HolidayEntryRow]:
    stmt = select(HolidayEntryRow)
    if countries:
        stmt = stmt.where(HolidayEntryRow.country.in_(countries))
    if years:
        stmt = stmt.where(HolidayEntryRow.year.in_(years))
    stmt = stmt.order_by(
        HolidayEntryRow.day, HolidayEntryRow.country, HolidayEntryRow.priority, HolidayEntryRow.key
    )
    return list(db.scalars(stmt).all())
