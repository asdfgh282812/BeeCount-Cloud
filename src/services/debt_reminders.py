"""借還款追蹤到期提醒(MOZE_FEATURE_GAP_SD.md §2.5 Phase 3)。

`ReadDebtProjection` 故意不存 `remaining_amount`/`status`(见该类 docstring),
所以这里没有排程需要推进的字段,只做一件事:`due_at` 快到了(或已逾期)
且还没结清的欠款,写一条 §2.1 notification 提醒使用者。

去重不靠额外的 `reminder_sent_at` 列(那需要脱离正常写路径直接改
projection,违反"projection 只能从 sync_changes 回放"的一致性契约,见
SYNC_ARCHITECTURE.md)——改成查 `Notification` 表:同一笔债务只要已经有过
一条 category='reminder' 的通知,就不再重复提醒。使用者编辑 `due_at` 后
想重新提醒,需要产品面另外设计"重新提醒"入口,目前不支持。

调用入口:main.py 里独立的周期性 asyncio loop(`_start_debt_reminder_loop`,
启动时立即跑一次、之后每 15 分钟一次 —— 故意跟 recurring_materializer 的
24 小时 loop 分开,理由见 main.py 该 loop 上方注释),以及
`POST /internal/tasks/materialize-recurring`(admin scope,手动立即触发)。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, ReadDebtProjection, ReadTxProjection
from . import notifications as notification_service

logger = logging.getLogger(__name__)

# 到期前几天开始提醒(含已逾期 —— 没有上界,逾期越久越该提醒,反正每笔
# 债务只提醒一次)。
_REMINDER_WINDOW_DAYS = 3


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _already_reminded_debt_ids(db: Session, *, user_id: str) -> set[str]:
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "reminder",
        )
    ).all()
    out: set[str] = set()
    for payload in rows:
        if isinstance(payload, dict):
            debt_id = payload.get("debtId")
            if isinstance(debt_id, str) and debt_id:
                out.add(debt_id)
    return out


def send_due_debt_reminders(db: Session, *, now: datetime | None = None) -> int:
    """扫所有 `due_at` 在 [任意逾期, now + 3 天] 内、尚未结清、也还没提醒过
    的欠款,各写一条 notification。返回发出的提醒数。不 commit —— 调用方
    决定事务边界(跟 `refill_recurring_windows` 同款约定)。"""
    now = now or datetime.now(timezone.utc)
    threshold = now + timedelta(days=_REMINDER_WINDOW_DAYS)

    rows = db.scalars(
        select(ReadDebtProjection).where(
            ReadDebtProjection.due_at.is_not(None),
            ReadDebtProjection.due_at <= threshold,
        )
    ).all()
    if not rows:
        return 0

    reminded_by_user: dict[str, set[str]] = {}
    sent = 0
    for debt in rows:
        already = reminded_by_user.setdefault(
            debt.user_id, _already_reminded_debt_ids(db, user_id=debt.user_id)
        )
        if debt.sync_id in already:
            continue
        if debt.closed_at is not None:
            continue  # 已手動結案,不再提醒

        repaid_total = 0.0
        for amount in db.scalars(
            select(ReadTxProjection.amount).where(
                ReadTxProjection.ledger_id == debt.ledger_id,
                ReadTxProjection.debt_sync_id == debt.sync_id,
            )
        ).all():
            repaid_total += abs(float(amount or 0))
        if repaid_total >= float(debt.principal_amount or 0) - 0.01:
            continue  # 已结清,不提醒

        if debt.due_at is None:
            continue  # 兜底:上面的 SQL WHERE 已经排除了 due_at IS NULL
        due_at = _ensure_aware(debt.due_at)
        overdue = due_at < now
        direction_label = "该收款" if debt.direction == "receivable" else "该还款"
        notification_service.create_notification(
            db,
            user_id=debt.user_id,
            category="reminder",
            title=f"{'已逾期' if overdue else '即将到期'}：{debt.counterparty_name or direction_label}",
            body=(
                f"{debt.counterparty_name or direction_label} 的欠款"
                f"{'已逾期' if overdue else '即将到期'},剩余待{'收' if debt.direction == 'receivable' else '还'}"
                f" {max(float(debt.principal_amount or 0) - repaid_total, 0.0):.2f}"
            ),
            payload={
                "ledgerId": notification_service.resolve_ledger_external_id(db, debt.ledger_id),
                "debtId": debt.sync_id,
            },
        )
        already.add(debt.sync_id)
        sent += 1

    if sent:
        logger.info("debt_reminders: sent %d reminders", sent)
    return sent
