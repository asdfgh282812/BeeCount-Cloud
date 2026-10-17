"""即時回饋的「待重算」標記(2026-10-16,見 `services/card_reward_live.py`)。

`projection.upsert_tx` / `delete_tx` / `upsert_card_reward_rule` / `delete_card_reward_rule`
是所有交易與回饋規則寫入的必經之路(App push、Web 寫入、排程產生的交易都走這裡),
在這裡把「寫入前的狀態」記到 `Session.info`,commit 前由 `card_reward_live` 的
`before_commit` listener 一次重算。這個模組刻意不 import 任何 service,避免
projection ↔ services 循環 import。

- 交易:同一個 commit 內同一筆交易被寫多次時,只保留**第一次**寫入前的狀態
  (重算需要的是「這次 commit 前」vs「現在」)。
- `suppress(db)`:即時機制自己寫回饋交易、或批次重建投影時關掉標記。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

_TX_KEY = "card_reward_live_tx"
_RULE_KEY = "card_reward_live_rules"
_SUPPRESS_KEY = "card_reward_live_suppress"


def is_suppressed(db: Session) -> bool:
    return bool(db.info.get(_SUPPRESS_KEY))


@contextmanager
def suppress(db: Session) -> Iterator[None]:
    depth = db.info.get(_SUPPRESS_KEY, 0)
    db.info[_SUPPRESS_KEY] = depth + 1
    try:
        yield
    finally:
        db.info[_SUPPRESS_KEY] = depth


def tx_snapshot(row: Any) -> dict[str, Any] | None:
    """`ReadTxProjection` 列 → 重算需要的欄位。`row` 為 None(新交易)回 None。"""
    if row is None:
        return None
    return {
        "user_id": row.user_id,
        "tx_type": row.tx_type,
        "account_sync_id": row.account_sync_id,
        "happened_at": row.happened_at,
        "deferred_posting_at": row.deferred_posting_at,
        "reward_rule_sync_ids_json": row.reward_rule_sync_ids_json,
        "refund_of_sync_id": row.refund_of_sync_id,
        "reward_payout_rule_sync_id": row.reward_payout_rule_sync_id,
        "reward_payout_key": row.reward_payout_key,
    }


def mark_tx(db: Session, *, ledger_id: str, sync_id: str, before: dict[str, Any] | None) -> None:
    if is_suppressed(db):
        return
    touched: dict[tuple[str, str], dict[str, Any] | None] = db.info.setdefault(_TX_KEY, {})
    key = (ledger_id, sync_id)
    if key not in touched:
        touched[key] = before


def mark_rule(db: Session, *, user_id: str, rule_sync_id: str) -> None:
    if is_suppressed(db):
        return
    rules: set[tuple[str, str]] = db.info.setdefault(_RULE_KEY, set())
    rules.add((user_id, rule_sync_id))


def pop_all(db: Session) -> tuple[dict[tuple[str, str], dict[str, Any] | None], set[tuple[str, str]]]:
    return db.info.pop(_TX_KEY, {}), db.info.pop(_RULE_KEY, set())


def has_pending(db: Session) -> bool:
    return bool(db.info.get(_TX_KEY)) or bool(db.info.get(_RULE_KEY))


def clear(db: Session) -> None:
    db.info.pop(_TX_KEY, None)
    db.info.pop(_RULE_KEY, None)
