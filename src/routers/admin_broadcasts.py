"""管理者系統公告(`SystemBroadcast` docstring 有完整說明)。

掛在 `/api/v1/admin/broadcasts`,比照 `admin_licenses.py` 疊
`require_admin_user` + `require_scopes(SCOPE_OPS_WRITE)`。

發送 = 對所有 `is_enabled` 使用者各寫一筆 `category='system'`、
`priority=3` 的通知;不另外做推播通道,App/web 都走既有的
`GET /notifications` 輪詢(App 輪詢到新未讀會跳本機系統通知)。發送/撤回後
額外對在線使用者推一則 `notification_changed` WS 訊息,讓 web 鈴鐺立即刷新,
不用等 60 秒輪詢;舊版 App 會忽略不認得的 WS type,不受影響。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user, require_scopes
from ..models import AuditLog, Notification, SystemBroadcast, User
from ..schemas import (
    AdminBroadcastCreateRequest,
    AdminBroadcastListOut,
    AdminBroadcastOut,
    AdminBroadcastRecipientCountOut,
)
from ..security import SCOPE_OPS_WRITE

router = APIRouter()
logger = logging.getLogger(__name__)

_SCOPE_DEP = require_scopes(SCOPE_OPS_WRITE)

# 見 models.Notification.priority 的約定:3 排在欠款(2)/信用卡(1)之前。
BROADCAST_PRIORITY = 3


def _audit(db: Session, *, user_id: str, action: str, metadata: dict[str, Any]) -> None:
    db.add(AuditLog(user_id=user_id, ledger_id=None, action=action, metadata_json=metadata))


def _enabled_user_ids(db: Session) -> list[str]:
    return list(db.scalars(select(User.id).where(User.is_enabled.is_(True))))


def _read_counts(db: Session, broadcast_ids: list[str]) -> dict[str, int]:
    if not broadcast_ids:
        return {}
    rows = db.execute(
        select(Notification.broadcast_id, func.count())
        .where(
            Notification.broadcast_id.in_(broadcast_ids),
            Notification.read_at.is_not(None),
        )
        .group_by(Notification.broadcast_id)
    ).all()
    return {bid: int(n) for bid, n in rows}


def _to_out(
    row: SystemBroadcast, *, emails: dict[str, str], read_counts: dict[str, int]
) -> AdminBroadcastOut:
    return AdminBroadcastOut(
        id=row.id,
        title=row.title,
        body=row.body,
        created_at=row.created_at,
        created_by_email=emails.get(row.created_by_user_id or ""),
        recipient_count=row.recipient_count,
        read_count=read_counts.get(row.id, 0),
        retracted_at=row.retracted_at,
    )


def _single_out(db: Session, row: SystemBroadcast) -> AdminBroadcastOut:
    emails: dict[str, str] = {}
    if row.created_by_user_id:
        email = db.scalar(select(User.email).where(User.id == row.created_by_user_id))
        if email:
            emails[row.created_by_user_id] = email
    return _to_out(row, emails=emails, read_counts=_read_counts(db, [row.id]))


async def _notify_online(request: Request, user_ids: list[str], broadcast_id: str) -> None:
    """通知在線使用者重新拉通知列表。失敗不影響請求本身(資料已落庫,
    客戶端下一次輪詢一樣拿得到)。"""
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager is None:
        return
    targets = set(user_ids) & set(ws_manager.online_user_ids())
    payload = {"type": "notification_changed", "broadcastId": broadcast_id}
    for uid in targets:
        try:
            await ws_manager.broadcast_to_user(uid, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("admin_broadcast: ws notify failed user=%s err=%s", uid, exc)


@router.get("", response_model=AdminBroadcastListOut)
def list_broadcasts(
    limit: int = Query(default=100, ge=1, le=500),
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminBroadcastListOut:
    total = int(db.scalar(select(func.count()).select_from(SystemBroadcast)) or 0)
    rows = list(
        db.scalars(
            select(SystemBroadcast).order_by(SystemBroadcast.created_at.desc()).limit(limit)
        )
    )
    creator_ids = {r.created_by_user_id for r in rows if r.created_by_user_id}
    emails = (
        {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(creator_ids)))}
        if creator_ids
        else {}
    )
    read_counts = _read_counts(db, [r.id for r in rows])
    return AdminBroadcastListOut(
        items=[_to_out(r, emails=emails, read_counts=read_counts) for r in rows],
        total=total,
    )


@router.get("/recipient-count", response_model=AdminBroadcastRecipientCountOut)
def broadcast_recipient_count(
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminBroadcastRecipientCountOut:
    count = int(
        db.scalar(select(func.count()).select_from(User).where(User.is_enabled.is_(True))) or 0
    )
    return AdminBroadcastRecipientCountOut(count=count)


@router.post("", response_model=AdminBroadcastOut, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    req: AdminBroadcastCreateRequest,
    request: Request,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminBroadcastOut:
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Title is required")
    body = (req.body or "").strip() or None

    user_ids = _enabled_user_ids(db)
    broadcast = SystemBroadcast(
        title=title,
        body=body,
        created_by_user_id=admin_user.id,
        recipient_count=len(user_ids),
    )
    db.add(broadcast)
    db.flush()
    db.add_all(
        [
            Notification(
                user_id=uid,
                category="system",
                title=title,
                body=body,
                payload_json={"broadcastId": broadcast.id},
                priority=BROADCAST_PRIORITY,
                broadcast_id=broadcast.id,
            )
            for uid in user_ids
        ]
    )
    _audit(
        db,
        user_id=admin_user.id,
        action="admin_broadcast_create",
        metadata={"broadcastId": broadcast.id, "title": title, "recipients": len(user_ids)},
    )
    db.commit()
    db.refresh(broadcast)
    logger.info(
        "admin_broadcast.create id=%s recipients=%d by=%s",
        broadcast.id, len(user_ids), admin_user.id,
    )

    await _notify_online(request, user_ids, broadcast.id)
    return _single_out(db, broadcast)


@router.post("/{broadcast_id}/retract", response_model=AdminBroadcastOut)
async def retract_broadcast(
    broadcast_id: str,
    request: Request,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> AdminBroadcastOut:
    broadcast = db.get(SystemBroadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")
    if broadcast.retracted_at is not None:
        return _single_out(db, broadcast)

    affected_user_ids = list(
        db.scalars(
            select(Notification.user_id).where(Notification.broadcast_id == broadcast.id)
        )
    )
    db.execute(delete(Notification).where(Notification.broadcast_id == broadcast.id))
    broadcast.retracted_at = datetime.now(timezone.utc)
    _audit(
        db,
        user_id=admin_user.id,
        action="admin_broadcast_retract",
        metadata={"broadcastId": broadcast.id, "deleted": len(affected_user_ids)},
    )
    db.commit()
    db.refresh(broadcast)
    logger.info(
        "admin_broadcast.retract id=%s deleted=%d by=%s",
        broadcast.id, len(affected_user_ids), admin_user.id,
    )

    await _notify_online(request, affected_user_ids, broadcast.id)
    return _single_out(db, broadcast)
