"""授權金鑰 + App 最低可同步版本門檻(docs/LICENSE_KEYS.md)。

兩道門檻都在「鑑權」這一層集中檢查,不散落在各個 router:
- `deps.get_current_user`(所有一般 REST 端點的唯一 user 來源)
- `deps._resolve_pat` / `mcp/auth.py::_resolve_pat_sync`(MCP / PAT)
- `routers/ws.py`(WebSocket)

`tests/test_license_route_audit.py` 會掃過 app 上所有路由,確保每一個需要
登入的端點都經過上面其中一個入口 —— 新增端點時忘了掛 `get_current_user`
(例如只掛 `require_scopes`)會直接讓那支測試失敗,而不是悄悄變成一個
不用授權就能打的後門。
"""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import HTTPException, Request, status
from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AppVersionCheckConfig, LicenseKey, User

DEFAULT_DURATION_DAYS = 365
# App 端本地授權的離線寬限期 —— App 每次成功跟 server 確認授權有效後,
# 本地可用期限延長到「現在 + 7 天」(不超過金鑰本身的到期日)。server 只負責
# 把這個數字告訴 App,實際倒數在 App 端。
OFFLINE_GRACE_DAYS = 7

APP_VERSION_HEADER = "X-App-Version"

LICENSE_REQUIRED_MESSAGE = "License required"
APP_VERSION_TOO_OLD_PREFIX = "App version too old"

# 金鑰字元集:去掉易混淆的 0/O/1/I/L,32 個字元 → 每字元 5 bits,
# 20 個字元 = 100 bits,暴力猜測不可行(另外 activate 端點還有速率限制)。
_KEY_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_KEY_GROUPS = 4
_KEY_GROUP_LEN = 5
_KEY_PREFIX = "BC"


# --------------------------------------------------------------------------- #
# 開關
# --------------------------------------------------------------------------- #


def is_enforcement_enabled() -> bool:
    """授權檢查預設**永遠開啟**。

    `LICENSE_ENFORCEMENT=false` 只在 APP_ENV 為 development/test 時才生效
    (給既有測試套件跟本地開發用);其它環境一律忽略這個環境變數 —— 避免
    部署時誤設一個 env 就讓整台 server 變成免授權。每次呼叫都重讀 env
    (不走 lru_cache),測試才能用 monkeypatch 切換。
    """
    raw = os.environ.get("LICENSE_ENFORCEMENT", "").strip().lower()
    if raw not in {"0", "false", "no", "off"}:
        return True
    return get_settings().app_env not in {"development", "test"}


# --------------------------------------------------------------------------- #
# 金鑰格式
# --------------------------------------------------------------------------- #


def generate_key() -> str:
    groups = [
        "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_GROUP_LEN))
        for _ in range(_KEY_GROUPS)
    ]
    return "-".join([_KEY_PREFIX, *groups])


def normalize_key(raw: str) -> str | None:
    """容忍使用者輸入小寫、空白、少打/多打連字號;不合格式回 None。"""
    compact = re.sub(r"[^0-9A-Za-z]", "", raw or "").upper()
    body_len = _KEY_GROUPS * _KEY_GROUP_LEN
    if compact.startswith(_KEY_PREFIX) and len(compact) == len(_KEY_PREFIX) + body_len:
        compact = compact[len(_KEY_PREFIX):]
    if len(compact) != body_len or any(ch not in _KEY_ALPHABET for ch in compact):
        return None
    groups = [compact[i:i + _KEY_GROUP_LEN] for i in range(0, body_len, _KEY_GROUP_LEN)]
    return "-".join([_KEY_PREFIX, *groups])


# --------------------------------------------------------------------------- #
# 授權狀態
# --------------------------------------------------------------------------- #


def _as_utc(dt: datetime | None) -> datetime | None:
    # SQLite 讀回來是 naive,Postgres 是 aware —— 統一當 UTC(同 deps._resolve_pat)。
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def get_user_license_expiry(db: Session, user_id: str) -> datetime | None:
    """使用者名下所有「已啟用、未撤銷」金鑰中最晚的到期日(可能已過期)。"""
    value = db.scalar(
        select(func.max(LicenseKey.expires_at)).where(
            LicenseKey.redeemed_by_user_id == user_id,
            LicenseKey.revoked_at.is_(None),
        )
    )
    return _as_utc(value)


def is_user_licensed(db: Session, user: User, *, now: datetime | None = None) -> bool:
    if user.is_admin or not is_enforcement_enabled():
        return True
    expiry = get_user_license_expiry(db, user.id)
    return expiry is not None and expiry > (now or datetime.now(timezone.utc))


def license_required_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail={"message": LICENSE_REQUIRED_MESSAGE, "error_code": "LICENSE_REQUIRED"},
    )


def assert_user_licensed(db: Session, user: User) -> None:
    if not is_user_licensed(db, user):
        raise license_required_exception()


# --------------------------------------------------------------------------- #
# 啟用金鑰
# --------------------------------------------------------------------------- #

_ACTIVATE_LOCK = Lock()
_ACTIVATE_BUCKETS: dict[str, list[float]] = {}
_ACTIVATE_WINDOW_SECONDS = 15 * 60
_ACTIVATE_MAX_ATTEMPTS = 10


def check_activate_rate_limit(user_id: str) -> None:
    """每個帳號 15 分鐘內最多嘗試 10 次(成功失敗都算),防暴力猜金鑰。"""
    now_ts = time.time()
    with _ACTIVATE_LOCK:
        bucket = [ts for ts in _ACTIVATE_BUCKETS.get(user_id, []) if now_ts - ts < _ACTIVATE_WINDOW_SECONDS]
        if len(bucket) >= _ACTIVATE_MAX_ATTEMPTS:
            _ACTIVATE_BUCKETS[user_id] = bucket
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")
        bucket.append(now_ts)
        _ACTIVATE_BUCKETS[user_id] = bucket


def reset_activate_rate_limit() -> None:
    """測試用。"""
    with _ACTIVATE_LOCK:
        _ACTIVATE_BUCKETS.clear()


def redeem_key(db: Session, user: User, raw_key: str) -> LicenseKey:
    """把金鑰綁到 `user`,回傳更新後的 row(呼叫方負責 commit)。

    用條件式 UPDATE(`WHERE redeemed_by_user_id IS NULL AND revoked_at IS NULL`)
    + 檢查 rowcount 做原子搶佔,兩個帳號同時送同一把金鑰只有一個會成功,
    SQLite / Postgres 行為一致,不依賴 SELECT ... FOR UPDATE。
    """
    normalized = normalize_key(raw_key)
    if normalized is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="License key format invalid")
    row = db.scalar(select(LicenseKey).where(LicenseKey.key == normalized))
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License key not found")
    if row.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="License key revoked")
    if row.redeemed_by_user_id is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="License key already redeemed")

    now = datetime.now(timezone.utc)
    current = get_user_license_expiry(db, user.id)
    start = current if current is not None and current > now else now
    expires_at = start + timedelta(days=int(row.duration_days or DEFAULT_DURATION_DAYS))

    result: CursorResult = db.execute(  # type: ignore[assignment]
        update(LicenseKey)
        .where(
            LicenseKey.id == row.id,
            LicenseKey.redeemed_by_user_id.is_(None),
            LicenseKey.revoked_at.is_(None),
        )
        .values(redeemed_by_user_id=user.id, redeemed_at=now, expires_at=expires_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="License key already redeemed")
    db.flush()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# App 最低可同步版本
# --------------------------------------------------------------------------- #


def parse_version(raw: str | None) -> tuple[int, ...] | None:
    """`3.5.7` / `3.5.7+1` / `3.5.7 1`(舊 report-version 格式)都取 `3.5.7`。"""
    if not raw:
        return None
    head = re.split(r"[+\s-]", raw.strip(), maxsplit=1)[0]
    if not re.fullmatch(r"\d+(\.\d+){0,3}", head):
        return None
    return tuple(int(p) for p in head.split("."))


def is_version_below(current: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    width = max(len(current), len(minimum))
    return current + (0,) * (width - len(current)) < minimum + (0,) * (width - len(minimum))


def get_min_sync_version(db: Session) -> str | None:
    config = db.get(AppVersionCheckConfig, 1)
    value = (config.min_sync_version or "").strip() if config is not None else ""
    return value or None


def app_version_too_old_message(min_version: str) -> str:
    # 舊版 App 會把 `detail` 原樣顯示在同步頁的錯誤訊息裡 —— 後半段直接寫
    # 中文,讓沒有新版錯誤處理的舊 App 使用者也看得懂該做什麼。
    return f"{APP_VERSION_TOO_OLD_PREFIX}: 請將 App 更新到 {min_version} 以上版本才能同步"


def app_version_rejection(db: Session, raw_app_version: str | None) -> str | None:
    """App 版本不符合門檻時回傳 min_version 字串,否則回 None。

    沒帶版本(舊版 App 從來不送 `X-App-Version`)一律視為過舊 —— 這正是
    「舊版 App 只會同步失敗」的機制。
    """
    min_raw = get_min_sync_version(db)
    minimum = parse_version(min_raw)
    if minimum is None:
        return None
    current = parse_version(raw_app_version)
    if current is None or is_version_below(current, minimum):
        return min_raw
    return None


def assert_app_version_allowed(db: Session, raw_app_version: str | None) -> None:
    min_version = app_version_rejection(db, raw_app_version)
    if min_version is not None:
        raise HTTPException(
            status_code=status.HTTP_426_UPGRADE_REQUIRED,
            detail={
                "message": app_version_too_old_message(min_version),
                "error_code": "APP_VERSION_TOO_OLD",
                "min_version": min_version,
            },
        )


# --------------------------------------------------------------------------- #
# 路徑豁免
# --------------------------------------------------------------------------- #


def _relative_path(request: Request) -> str:
    prefix = get_settings().api_prefix.rstrip("/")
    path = request.url.path
    return path[len(prefix):] if prefix and path.startswith(prefix) else path


_REPORT_VERSION_RE = re.compile(r"^/devices/[^/]+/report-version/?$")


def is_license_exempt_path(request: Request) -> bool:
    """沒有授權時仍可呼叫的端點:登入相關、授權查詢/啟用本身、回報版本。"""
    path = _relative_path(request)
    return (
        path.startswith("/auth/")
        or path.startswith("/license/")
        or bool(_REPORT_VERSION_RE.match(path))
    )


def is_version_exempt_path(request: Request) -> bool:
    """版本過舊時仍可呼叫的端點:登入相關、回報版本(讓管理者看得到誰還在用舊版)。"""
    path = _relative_path(request)
    return path.startswith("/auth/") or bool(_REPORT_VERSION_RE.match(path))


def enforce_request_gates(
    request: Request, db: Session, user: User, *, client_type: str | None
) -> None:
    """`get_current_user` 專用:先擋版本(只針對 App),再擋授權。"""
    if client_type == "app" and not is_version_exempt_path(request):
        assert_app_version_allowed(db, request.headers.get(APP_VERSION_HEADER))
    if not is_license_exempt_path(request):
        assert_user_licensed(db, user)
