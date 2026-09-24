"""使用者自己的授權狀態查詢 + 輸入金鑰啟用(docs/LICENSE_KEYS.md)。

掛在 `/api/v1/license`。這兩支端點本身在 `services/license.py::
is_license_exempt_path` 的豁免清單裡 —— 沒有授權的使用者也要能查狀態、
能輸入金鑰,否則永遠啟用不了。但仍然要求登入(`get_current_user`),
而且 App 版本門檻照樣生效(版本過舊的 App 先被 426 擋下,強制更新)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import AuditLog, User
from ..schemas import LicenseActivateRequest, LicenseStatusOut
from ..services import license as license_service

router = APIRouter()


def build_status(db: Session, user: User) -> LicenseStatusOut:
    now = datetime.now(timezone.utc)
    return LicenseStatusOut(
        user_id=user.id,
        email=user.email,
        is_admin=bool(user.is_admin),
        licensed=license_service.is_user_licensed(db, user, now=now),
        exempt=bool(user.is_admin),
        expires_at=license_service.get_user_license_expiry(db, user.id),
        server_time=now,
        offline_grace_days=license_service.OFFLINE_GRACE_DAYS,
    )


def _audit(db: Session, *, user_id: str, action: str, metadata: dict[str, Any]) -> None:
    db.add(AuditLog(user_id=user_id, ledger_id=None, action=action, metadata_json=metadata))


@router.get("/status", response_model=LicenseStatusOut)
def get_license_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LicenseStatusOut:
    return build_status(db, current_user)


@router.post("/activate", response_model=LicenseStatusOut)
def activate_license(
    req: LicenseActivateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LicenseStatusOut:
    license_service.check_activate_rate_limit(current_user.id)
    row = license_service.redeem_key(db, current_user, req.key)
    _audit(
        db,
        user_id=current_user.id,
        action="license_activate",
        metadata={"licenseKeyId": row.id, "expiresAt": row.expires_at.isoformat() if row.expires_at else None},
    )
    db.commit()
    return build_status(db, current_user)
