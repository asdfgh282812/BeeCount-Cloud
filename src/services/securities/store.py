"""securities / security_quotes 的批次 upsert。上游一次回傳上千檔(證交所
全市場收盤),逐筆 ON CONFLICT 太慢,這裡分塊做多列 VALUES upsert。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ...models import Security, SecurityQuote
from . import markets
from .providers.base import QuoteData, SecurityInfo

_CHUNK = 150


def _insert_fn(db: Session):
    name = db.get_bind().dialect.name
    if name == "postgresql":
        return pg_insert
    if name == "sqlite":
        return sqlite_insert
    raise RuntimeError(f"unsupported dialect for securities upsert: {name}")


def _bulk_upsert(db: Session, model, rows: list[dict], conflict_cols: list[str]) -> None:
    if not rows:
        return
    insert_fn = _insert_fn(db)
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        stmt = insert_fn(model).values(chunk)
        update_cols = {
            k: stmt.excluded[k] for k in chunk[0].keys() if k not in conflict_cols
        }
        stmt = stmt.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols)
        db.execute(stmt)


def upsert_securities(db: Session, infos: Iterable[SecurityInfo], *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    seen: dict[tuple[str, str], dict] = {}
    for info in infos:
        seen[(info.market, info.symbol)] = {
            "market": info.market,
            "symbol": info.symbol,
            "name": info.name,
            "currency": info.currency,
            "kind": info.kind,
            "is_active": True,
            "updated_at": now,
        }
    _bulk_upsert(db, Security, list(seen.values()), ["market", "symbol"])


def ensure_security(db: Session, *, market: str, symbol: str, name: str | None, currency: str) -> int:
    row = db.scalar(select(Security).where(Security.market == market, Security.symbol == symbol))
    if row is not None:
        if name and not row.name:
            row.name = name
        return row.id
    row = Security(market=market, symbol=symbol, name=name or "", currency=currency, kind="stock")
    db.add(row)
    db.flush()
    return row.id


def security_ids(db: Session, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], int]:
    keys = list(set(keys))
    out: dict[tuple[str, str], int] = {}
    if not keys:
        return out
    by_market: dict[str, list[str]] = {}
    for market, symbol in keys:
        by_market.setdefault(market, []).append(symbol)
    for market, symbols in by_market.items():
        for i in range(0, len(symbols), 500):
            for sid, sym in db.execute(
                select(Security.id, Security.symbol).where(
                    Security.market == market, Security.symbol.in_(symbols[i : i + 500])
                )
            ).all():
                out[(market, sym)] = sid
    return out


def _same_session_day(q: QuoteData, old: SecurityQuote) -> bool:
    if q.quote_time is None or old.quote_time is None:
        return False
    m = markets.get_market(q.market)
    if m is None:
        return False
    old_time = old.quote_time if old.quote_time.tzinfo else old.quote_time.replace(tzinfo=timezone.utc)
    return markets.local_now(m, q.quote_time).date() == markets.local_now(m, old_time).date()


def upsert_quotes(db: Session, quotes: Iterable[QuoteData], *, session: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    quotes = list(quotes)
    ids = security_ids(db, [(q.market, q.symbol) for q in quotes])
    existing = {
        row.security_id: row
        for row in db.scalars(
            select(SecurityQuote).where(SecurityQuote.security_id.in_(list(ids.values())))
        ).all()
    } if ids else {}
    rows: list[dict] = []
    for q in quotes:
        sid = ids.get((q.market, q.symbol))
        if sid is None:
            continue
        open_price = q.open_price
        old = existing.get(sid)
        if open_price is None and old is not None and old.open_price is not None:
            # 新報價沒帶開盤價(來源不同/暫時缺值),但跟舊報價是同一個交易日:沿用,
            # 不要把已經拿到的當天開盤價洗掉(定期定額靠它成交)。
            if _same_session_day(q, old):
                open_price = old.open_price
        rows.append(
            {
                "security_id": sid,
                "price": q.price,
                "prev_close": q.prev_close,
                "quote_time": q.quote_time,
                "open_price": open_price,
                "session": session,
                "source": q.source,
                "fetched_at": now,
            }
        )
    _bulk_upsert(db, SecurityQuote, rows, ["security_id"])
    return len(rows)
