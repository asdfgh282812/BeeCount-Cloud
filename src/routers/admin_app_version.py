"""App 端新版本提醒 —— 管理者設定後台(§ docs/superpowers/specs/
2026-09-08-app-update-reminder-design.md §2)。

掛在 `/api/v1/admin/app-version-config`,比照 `admin_scheduled_jobs.py` 疊
`require_admin_user` + `require_scopes(SCOPE_OPS_WRITE)` ——這是運維層級操作
(NAS WebDAV 連線設定 + 目前最新版本號),不是使用者自己擁有的資源。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user, require_scopes
from ..models import AuditLog, User
from ..schemas import (
    AppVersionCheckConfigOut,
    AppVersionCheckConfigUpdateRequest,
    AppVersionCheckNowOut,
)
from ..security import SCOPE_OPS_WRITE
from ..services import app_version_check

router = APIRouter()

_SCOPE_DEP = require_scopes(SCOPE_OPS_WRITE)


def _audit(db: Session, *, user_id: str, action: str, metadata: dict[str, Any]) -> None:
    db.add(AuditLog(user_id=user_id, ledger_id=None, action=action, metadata_json=metadata))


def _build_out(config) -> AppVersionCheckConfigOut:  # noqa: ANN001
    return AppVersionCheckConfigOut(
        latest_version=config.latest_version,
        nas_webdav_url=config.nas_webdav_url,
        nas_webdav_user=config.nas_webdav_user,
        nas_webdav_password_set=bool(config.nas_webdav_password),
        last_checked_at=config.last_checked_at,
        last_check_error=config.last_check_error,
    )


@router.get("", response_model=AppVersionCheckConfigOut)
def get_app_version_config(
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AppVersionCheckConfigOut:
    config = app_version_check.get_or_create_config(db)
    return _build_out(config)


@router.put("", response_model=AppVersionCheckConfigOut)
def update_app_version_config(
    req: AppVersionCheckConfigUpdateRequest,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AppVersionCheckConfigOut:
    config = app_version_check.get_or_create_config(db)
    if req.latest_version is not None:
        config.latest_version = req.latest_version.strip() or None
    if req.nas_webdav_url is not None:
        config.nas_webdav_url = req.nas_webdav_url.strip() or None
    if req.nas_webdav_user is not None:
        config.nas_webdav_user = req.nas_webdav_user.strip() or None
    # 只有明確帶非空字串才覆蓋——前端不會把已設定的密碼明文帶回來,留空/
    # 不帶這個欄位都視為「不變更」,唯一合理的預設行為(否則管理者只想改
    # URL、沒動密碼欄位時就會把密碼清空)。
    if req.nas_webdav_password:
        config.nas_webdav_password = req.nas_webdav_password

    _audit(
        db,
        user_id=admin_user.id,
        action="app_version_config_update",
        metadata={
            "latestVersion": req.latest_version,
            "nasWebdavUrl": req.nas_webdav_url,
            "nasWebdavUser": req.nas_webdav_user,
            "nasWebdavPasswordChanged": bool(req.nas_webdav_password),
        },
    )
    db.commit()
    db.refresh(config)
    return _build_out(config)


@router.post("/check-now", response_model=AppVersionCheckNowOut)
def check_app_version_now(
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AppVersionCheckNowOut:
    """立即執行一次 NAS WebDAV 偵測,同步回傳結果,不用等排程。"""
    result = app_version_check.check_latest_app_version(db)
    config = app_version_check.get_or_create_config(db)
    _audit(
        db,
        user_id=admin_user.id,
        action="app_version_config_check_now",
        metadata={"status": result["status"]},
    )
    db.commit()
    return AppVersionCheckNowOut(
        status=result["status"],
        latest_version=config.latest_version,
        last_checked_at=config.last_checked_at,
        last_check_error=config.last_check_error,
    )
