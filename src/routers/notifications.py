"""通知中心 endpoints(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0）。

user-global，非 sync 实体，不进 `sync_changes`/projection —— 走普通 REST。
跨端各自 poll `GET /notifications`，或未来接 WS 推播时另外处理。

写入侧看 `services/notifications.create_notification()`，本文件只负责查询 /
已读标记。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import Notification, User
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter()

_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE)
_WRITE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE)


def _utc_iso(value: datetime | None) -> str | None:
    """跟 mcp_calls.py 同一套 UTC 标记化序列化 — SQLite 读回 naive,前端按本地解析会偏。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class NotificationItem(BaseModel):
    id: int
    category: str
    title: str
    body: str | None
    payload: dict | None
    read_at: datetime | None
    created_at: datetime

    @field_serializer("read_at", "created_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class NotificationListResponse(BaseModel):
    total: int
    unread_count: int
    items: list[NotificationItem]


def _to_item(row: Notification) -> NotificationItem:
    return NotificationItem(
        id=row.id,
        category=row.category,
        title=row.title,
        body=row.body,
        payload=row.payload_json,
        read_at=row.read_at,
        created_at=row.created_at,
    )


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationListResponse:
    """分页列出当前用户的通知(按 created_at 倒序)。unread_count 不受
    limit/offset/category/unread_only 影响,始终是该用户全部未读数,方便前端
    渲染角标。"""
    base = select(Notification).where(Notification.user_id == current_user.id)
    if category:
        base = base.where(Notification.category == category)
    if unread_only:
        base = base.where(Notification.read_at.is_(None))

    count_q = select(func.count()).select_from(base.subquery())
    total = int(db.scalar(count_q) or 0)

    unread_count = int(
        db.scalar(
            select(func.count()).select_from(
                select(Notification.id)
                .where(
                    Notification.user_id == current_user.id,
                    Notification.read_at.is_(None),
                )
                .subquery()
            )
        )
        or 0
    )

    rows = db.scalars(
        base.order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return NotificationListResponse(
        total=total,
        unread_count=unread_count,
        items=[_to_item(row) for row in rows],
    )


@router.post("/{notification_id}/read", response_model=NotificationItem)
def mark_notification_read(
    notification_id: int,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationItem:
    row = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.add(row)
        db.commit()
        db.refresh(row)
    return _to_item(row)


@router.post("/read-all", response_model=dict)
def mark_all_notifications_read(
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """批量已读 —— 不在原始设计文档里,但通知列表功能少了这个入口体验很差
    (逐条点太麻烦),Phase 0 顺手加上,风险低(纯 UPDATE,无跨表副作用)。"""
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(Notification).where(
            Notification.user_id == current_user.id,
            Notification.read_at.is_(None),
        )
    ).all()
    for row in rows:
        row.read_at = now
        db.add(row)
    db.commit()
    return {"updated": len(rows)}
