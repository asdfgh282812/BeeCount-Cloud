"""訂金尾款追蹤到期提醒(doc.moze.app/record/deposit)。

做法同 `debt_reminders`:`due_at` 快到(3 天內)或已逾期、還沒補上
(`status='pending'`)的尾款,各寫一條 notification,payload 帶
`depositBalanceId` 讓前端/App 點通知能跳到待補尾款清單。去重不加額外欄位,
查 `Notification` 表:同一筆尾款已有過一條提醒就不再重複。

日期未定(`due_at IS NULL`)的尾款不提醒——MOZE 文件:這類尾款「會一直留在
通知中心(待完成交易)」,直到補上日期/金額為止,那是 App/Web 清單本身的
職責,不是推播。

App 端另外在到期日當天用本機排程推一則(見 App `DepositBalanceReminderService`),
兩者時間點不同(這裡是「快到期/逾期」,本機是「到期當天」),不是重複通知。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, ReadDepositBalanceProjection, ReadTxProjection
from . import notifications as notification_service

logger = logging.getLogger(__name__)

_REMINDER_WINDOW_DAYS = 3


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _already_reminded_ids(db: Session, *, user_id: str) -> set[str]:
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "reminder",
        )
    ).all()
    out: set[str] = set()
    for payload in rows:
        if isinstance(payload, dict):
            balance_id = payload.get("depositBalanceId")
            if isinstance(balance_id, str) and balance_id:
                out.add(balance_id)
    return out


def send_due_deposit_balance_reminders(db: Session, *, now: datetime | None = None) -> int:
    """掃所有 `due_at` 在 [任意逾期, now + 3 天] 內、尚未補上、也還沒提醒過的
    尾款,各寫一條 notification。返回發出的提醒數。不 commit —— 調用方決定
    事務邊界(同 `send_due_debt_reminders`)。"""
    now = now or datetime.now(timezone.utc)
    threshold = now + timedelta(days=_REMINDER_WINDOW_DAYS)

    rows = db.scalars(
        select(ReadDepositBalanceProjection).where(
            ReadDepositBalanceProjection.status == "pending",
            ReadDepositBalanceProjection.due_at.is_not(None),
            ReadDepositBalanceProjection.due_at <= threshold,
        )
    ).all()
    if not rows:
        return 0

    reminded_by_user: dict[str, set[str]] = {}
    sent = 0
    for balance in rows:
        already = reminded_by_user.setdefault(
            balance.user_id, _already_reminded_ids(db, user_id=balance.user_id)
        )
        if balance.sync_id in already or balance.due_at is None:
            continue
        source = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == balance.ledger_id,
                ReadTxProjection.sync_id == balance.source_tx_sync_id,
            ).limit(1)
        )
        if source is None:
            continue  # 訂金交易已不在(孤兒),不提醒
        overdue = _ensure_aware(balance.due_at) < now
        label = (source.merchant or "").strip() or (source.category_name or "").strip() or "訂金"
        amount_text = (
            f"尾款 {float(balance.amount):.2f}" if balance.amount is not None else "尾款金額未定"
        )
        notification_service.create_notification(
            db,
            user_id=balance.user_id,
            category="reminder",
            title=f"{'尾款已逾期' if overdue else '尾款即將到期'}：{label}",
            body=f"{label} 的{amount_text},{'已逾期' if overdue else '即將到期'},請補上尾款。",
            payload={
                "ledgerId": notification_service.resolve_ledger_external_id(db, balance.ledger_id),
                "depositBalanceId": balance.sync_id,
            },
            priority=1,
        )
        already.add(balance.sync_id)
        sent += 1

    if sent:
        logger.info("deposit_balance_reminders: sent %d reminders", sent)
    return sent
