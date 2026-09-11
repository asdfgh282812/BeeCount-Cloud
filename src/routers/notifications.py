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
from ..services import debt_status

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
    priority: int
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
        priority=row.priority,
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
    """分页列出当前用户的通知,依序按(1)未读优先(2)"未结清"优先度——但
    若该笔欠款/对象「现在」已结清/已结案则不再享有较高优先度,即时重新
    判断,不是当年建通知时写死的 priority 字段(3)建立时间倒序。
    unread_count 不受 limit/offset/category/unread_only 影响,始终是该
    用户全部未读数,方便前端渲染角标。"""
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

    # 排序键(即时判断"未结清"是否仍然成立)依赖每笔欠款/对象目前的还款
    # 状态,SQL 层的 ORDER BY 做不到,所以先把该用户全部符合筛选条件的
    # 记录取出来,在 Python 里排序完再切页。个人记账 app 单一用户的通知
    # 笔数有限,这里不做游标式增量优化。
    all_rows = db.scalars(base).all()

    reminder_pairs: set[tuple[str, str]] = set()
    counterparty_pairs: set[tuple[str, str]] = set()
    for row in all_rows:
        if row.priority != 2:
            continue
        payload = row.payload_json or {}
        ledger_ext = payload.get("ledgerId")
        if not isinstance(ledger_ext, str):
            continue
        if row.category == "reminder":
            debt_id = payload.get("debtId")
            if isinstance(debt_id, str):
                reminder_pairs.add((ledger_ext, debt_id))
        elif row.category == "debt_unsettled":
            counterparty = payload.get("counterpartyName")
            if isinstance(counterparty, str):
                counterparty_pairs.add((ledger_ext, counterparty))

    still_unsettled_reminders = debt_status.unsettled_debt_reminder_keys(
        db, user_id=current_user.id, pairs=reminder_pairs
    )
    still_unsettled_groups = debt_status.unsettled_counterparty_group_keys(
        db, user_id=current_user.id, pairs=counterparty_pairs
    )

    def _effective_priority(row: Notification) -> int:
        if row.priority != 2:
            return row.priority
        payload = row.payload_json or {}
        ledger_ext = payload.get("ledgerId")
        if row.category == "reminder":
            key = (ledger_ext, payload.get("debtId"))
            return row.priority if key in still_unsettled_reminders else 0
        if row.category == "debt_unsettled":
            key = (ledger_ext, payload.get("counterpartyName"))
            return row.priority if key in still_unsettled_groups else 0
        return row.priority

    effective_priority_by_id = {row.id: _effective_priority(row) for row in all_rows}

    # 用稳定排序由最不重要的键排到最重要的键:未读优先于一切,同读/未读
    # 状态内再比"(即时判断后的)未结清"优先度,最后比建立时间。
    sorted_rows = sorted(all_rows, key=lambda r: r.id, reverse=True)
    sorted_rows.sort(key=lambda r: r.created_at, reverse=True)
    sorted_rows.sort(key=lambda r: effective_priority_by_id[r.id], reverse=True)
    sorted_rows.sort(key=lambda r: r.read_at is None, reverse=True)

    page_rows = sorted_rows[offset : offset + limit]

    return NotificationListResponse(
        total=total,
        unread_count=unread_count,
        items=[_to_item(row) for row in page_rows],
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
