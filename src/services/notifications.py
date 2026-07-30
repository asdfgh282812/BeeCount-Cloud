"""通知中心写入 helper(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0 地基）。

后续 recurring 到期提醒 / budget 超支提醒 / 信用卡繳款日提醒等功能，各自在
自己的业务逻辑里调用 `create_notification()` 落一行记录即可，故意不做成
集中调度的 job，避免功能之间产生耦合。
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from ..models import Notification

NotificationCategory = Literal["reminder", "budget_alert", "card_due", "system"]


def create_notification(
    db: Session,
    *,
    user_id: str,
    category: NotificationCategory,
    title: str,
    body: str | None = None,
    payload: dict | None = None,
) -> Notification:
    """插入一条通知记录。不 commit —— 调用方通常在自己的事务里跟业务写入一起提交。"""
    notification = Notification(
        user_id=user_id,
        category=category,
        title=title,
        body=body,
        payload_json=payload,
    )
    db.add(notification)
    return notification
