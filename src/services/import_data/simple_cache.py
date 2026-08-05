"""分類 / 帳戶匯入的 token cache(2026-08 新增)。

刻意不重用 `cache.py`(交易匯入用的那張表)—— 那張表的 `_Entry` 資料形狀
(`ImportData`/`ImportFieldMapping`)是交易匯入專屬的,分類/帳戶匯入的資料
形狀完全不同(已經是解析完的 `ImportCategory`/`ImportAccount` 列表,沒有
欄位對應這層)。另開一張獨立的表可以避免上傳分類檔案時意外把使用者正在
處理的交易匯入 token 頂掉(反之亦然)——三種 entity type 的匯入互相獨立,
呼應使用者「並不需要一次匯入」的需求,不應該共用「單 user 同時只有一個
active token」這條限制。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from .schema import ImportAccount, ImportCategory, ImportError as ImpError

_TTL_SECONDS = 30 * 60

EntityType = Literal["categories", "accounts"]


@dataclass
class SimpleImportEntry:
    user_id: str
    entity_type: EntityType
    rows: list  # list[ImportCategory] | list[ImportAccount]
    errors: list[ImpError]
    target_ledger_id: str
    created_at: float


_lock = threading.Lock()
_TOKENS: dict[str, SimpleImportEntry] = {}


def _now() -> float:
    return time.monotonic()


def _purge_expired() -> None:
    cutoff = _now() - _TTL_SECONDS
    expired = [k for k, v in _TOKENS.items() if v.created_at < cutoff]
    for k in expired:
        _TOKENS.pop(k, None)


def save_simple_token(
    *,
    user_id: str,
    entity_type: EntityType,
    rows: list[ImportCategory] | list[ImportAccount],
    errors: list[ImpError],
    target_ledger_id: str,
) -> str:
    with _lock:
        _purge_expired()
        token = f"imps_{uuid4().hex[:24]}"
        _TOKENS[token] = SimpleImportEntry(
            user_id=user_id,
            entity_type=entity_type,
            rows=rows,
            errors=errors,
            target_ledger_id=target_ledger_id,
            created_at=_now(),
        )
        return token


def get_simple_token(*, token: str, user_id: str) -> SimpleImportEntry | None:
    with _lock:
        _purge_expired()
        entry = _TOKENS.get(token)
        if entry is None or entry.user_id != user_id:
            return None
        return entry


def consume_simple_token(*, token: str, user_id: str) -> SimpleImportEntry | None:
    with _lock:
        entry = _TOKENS.get(token)
        if entry is None or entry.user_id != user_id:
            return None
        _TOKENS.pop(token, None)
        return entry


def cancel_simple_token(*, token: str, user_id: str) -> bool:
    with _lock:
        entry = _TOKENS.get(token)
        if entry is None or entry.user_id != user_id:
            return False
        _TOKENS.pop(token, None)
        return True


def clear_all() -> None:
    """測試用 —— 清空所有 token。"""
    with _lock:
        _TOKENS.clear()
