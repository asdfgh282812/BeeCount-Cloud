"""授權金鑰管理後台(docs/LICENSE_KEYS.md)。

掛在 `/api/v1/admin/licenses`,比照 `admin_app_version.py` 疊
`require_admin_user` + `require_scopes(SCOPE_OPS_WRITE)`。管理者本身免金鑰
(`services/license.py::is_user_licensed`),所以就算所有金鑰都過期了,
管理者還是進得來產生新的。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user, require_scopes
from ..models import AuditLog, LicenseKey, User
from ..schemas import (
    AdminLicenseKeyCreateRequest,
    AdminLicenseKeyListOut,
    AdminLicenseKeyOut,
)
from ..security import SCOPE_OPS_WRITE
from ..services import license as license_service

router = APIRouter()

_SCOPE_DEP = require_scopes(SCOPE_OPS_WRITE)

LicenseStatusFilter = Literal["all", "unused", "active", "expired", "revoked"]


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _status_of(row: LicenseKey, now: datetime) -> str:
    if row.revoked_at is not None:
        return "revoked"
    if row.redeemed_by_user_id is None:
        return "unused"
    expires = _as_utc(row.expires_at)
    if expires is not None and expires > now:
        return "active"
    return "expired"


def _audit(db: Session, *, user_id: str, action: str, metadata: dict[str, Any]) -> None:
    db.add(AuditLog(user_id=user_id, ledger_id=None, action=action, metadata_json=metadata))


def _to_out(row: LicenseKey, *, now: datetime, emails: dict[str, str]) -> AdminLicenseKeyOut:
    return AdminLicenseKeyOut(
        id=row.id,
        key=row.key,
        duration_days=row.duration_days,
        note=row.note,
        status=_status_of(row, now),
        created_at=row.created_at,
        created_by_email=emails.get(row.created_by_user_id or ""),
        redeemed_by_user_id=row.redeemed_by_user_id,
        redeemed_by_email=emails.get(row.redeemed_by_user_id or ""),
        redeemed_at=row.redeemed_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def _emails_for(db: Session, rows: list[LicenseKey]) -> dict[str, str]:
    ids = {uid for r in rows for uid in (r.created_by_user_id, r.redeemed_by_user_id) if uid}
    if not ids:
        return {}
    return {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(ids)))}


@router.get("", response_model=AdminLicenseKeyListOut)
def list_license_keys(
    status_filter: LicenseStatusFilter = Query(default="all", alias="status"),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=200, ge=1, le=1000),
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminLicenseKeyListOut:
    rows = list(db.scalars(select(LicenseKey).order_by(LicenseKey.created_at.desc())))
    now = datetime.now(timezone.utc)
    emails = _emails_for(db, rows)
    items = [_to_out(r, now=now, emails=emails) for r in rows]
    if status_filter != "all":
        items = [i for i in items if i.status == status_filter]
    if q and q.strip():
        needle = q.strip().lower()
        items = [
            i
            for i in items
            if needle in i.key.lower()
            or needle in (i.note or "").lower()
            or needle in (i.redeemed_by_email or "").lower()
        ]
    return AdminLicenseKeyListOut(items=items[:limit], total=len(items))


@router.post("", response_model=AdminLicenseKeyListOut)
def create_license_keys(
    req: AdminLicenseKeyCreateRequest,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminLicenseKeyListOut:
    note = (req.note or "").strip() or None
    existing = set(db.scalars(select(LicenseKey.key)))
    created: list[LicenseKey] = []
    while len(created) < req.count:
        key = license_service.generate_key()
        if key in existing:
            continue
        existing.add(key)
        row = LicenseKey(
            key=key,
            duration_days=req.duration_days,
            note=note,
            created_by_user_id=admin_user.id,
        )
        db.add(row)
        created.append(row)
    _audit(
        db,
        user_id=admin_user.id,
        action="license_keys_create",
        metadata={"count": req.count, "durationDays": req.duration_days, "note": note},
    )
    db.commit()
    for row in created:
        db.refresh(row)
    now = datetime.now(timezone.utc)
    emails = _emails_for(db, created)
    items = [_to_out(r, now=now, emails=emails) for r in created]
    return AdminLicenseKeyListOut(items=items, total=len(items))


@router.post("/{license_id}/revoke", response_model=AdminLicenseKeyOut)
def revoke_license_key(
    license_id: str,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminLicenseKeyOut:
    row = db.get(LicenseKey, license_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(timezone.utc)
        _audit(
            db,
            user_id=admin_user.id,
            action="license_key_revoke",
            metadata={"licenseKeyId": row.id, "redeemedBy": row.redeemed_by_user_id},
        )
        db.commit()
        db.refresh(row)
    now = datetime.now(timezone.utc)
    return _to_out(row, now=now, emails=_emails_for(db, [row]))


@router.delete("/{license_id}")
def delete_unused_license_key(
    license_id: str,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> dict:
    """只允許刪除「從未被啟用」的金鑰;已啟用的金鑰請用撤銷,保留紀錄。"""
    row = db.get(LicenseKey, license_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License key not found")
    if row.redeemed_by_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="License key already redeemed; revoke it instead",
        )
    db.delete(row)
    _audit(db, user_id=admin_user.id, action="license_key_delete", metadata={"licenseKeyId": license_id})
    db.commit()
    return {"deleted": True}


