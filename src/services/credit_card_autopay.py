"""自動扣繳(MOZE_FEATURE_GAP_SD.md §2.9,2026-08-04 改版)到期物化。

**改版說明**:2026-08-02 的第一版自動扣繳借用了「週期性收支」(`tx_type==
"transfer"` 的 `recurring_rule`)整套機制,使用者反饋這不對——自動扣繳
應該是掛在信用卡(群組,或沒有掛靠任何群組的獨立信用卡)自己身上的一個
開關 + 一個來源帳戶(`UserAccountProjection.auto_pay_enabled` /
`auto_pay_from_account_id`,見 `alembic/versions/0028_account_auto_pay.py`),
不是一條完整的、有金額/頻率概念的週期性收支規則——信用卡帳單本來就是
`credit_card_billing.compute_group_billing` 算出來的,不需要使用者自己
再設一次金額。

三個判斷都對齊使用者需求(2026-08-04 确认):
- 只在 `is_billing_root` 的帳戶(account_group,或沒有掛靠任何群組的獨立
  信用卡)上生效,且必須已設定 `billing_day`/`payment_due_day`(否則算不出
  應繳金額/截止日)。
- 到了繳款截止日(`today >= due_date`)才開始嘗試,不是提前算好。
- 嘗試當下查 `auto_pay_from_account_id` 的記帳餘額(跟
  `recurring_materializer.compute_account_balance` 同一個公式)—— 不夠就
  跳過 + 通知(去重,同一期只通知一次),之後每 15 分鐘 loop 重試同一期,
  直到餘額足夠或使用者手動繳清。夠的話就照實際應繳金額(`remaining_due`)
  自動繳清,分攤規則複用 `credit_card_billing.compute_card_payment_
  allocations`(跟使用者手動繳款同一份邏輯,金額不是使用者輸入而是系統
  算出的應繳金額,恰好付清不會產生溢繳)。

去重用 `Notification` 表(跟 `credit_card_reminders`/
`recurring_materializer` 同款約定,不額外加追蹤欄位):
`category="card_due"`, `kind="auto_pay_executed"` 標記「這一期已經自動繳
過了」,防止同一期因為截止日後又有新消費導致 remaining_due 回正、被誤判
成又要繳一次;`kind="auto_pay_skipped_insufficient"` 標記「這一期已經通知
過餘額不足」,避免每 15 分鐘重試都重複發通知(但背景仍會靜默持續嘗試,
餘額足夠的那次 tick 就會成功並發 `auto_pay_executed` 通知)。

呼叫入口:跟 `credit_card_reminders`/`recurring_materializer.
materialize_due_transfer_rules` 同一個 15 分鐘 loop(`main.py` 的
`_run_debt_reminders_once`),也可以手動 `POST /internal/tasks/
materialize-recurring`(admin scope)立即觸發。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, ReadTxProjection, UserAccountProjection
from . import credit_card, credit_card_billing
from . import notifications as notification_service
from .recurring_materializer import compute_account_balance, emit_tx, new_sync_id

logger = logging.getLogger(__name__)


def _already_marked(
    db: Session, *, user_id: str, account_id: str, cycle_end_iso: str, kind: str,
) -> bool:
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "card_due",
        )
    ).all()
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("accountId") == account_id
            and payload.get("cycleEnd") == cycle_end_iso
            and payload.get("kind") == kind
        ):
            return True
    return False


def _account_name(db: Session, *, user_id: str, sync_id: str | None) -> str | None:
    if not sync_id:
        return None
    return db.scalar(
        select(UserAccountProjection.name).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == sync_id,
        )
    )


def materialize_due_card_autopay(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """掃所有 `auto_pay_enabled=True` 的 billing-root 帳戶,到期(`today >=
    due_date`)且尚未針對這一期執行過的,查來源帳戶餘額決定執行或跳過+通知。
    不 commit —— 調用方決定事務邊界。返回 {"executed": N,
    "skipped_insufficient": M}。"""
    now = now or datetime.now(timezone.utc)
    today = now.date()

    candidates = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.account_type.in_(["account_group", "credit_card"]),
            UserAccountProjection.auto_pay_enabled.is_(True),
            UserAccountProjection.auto_pay_from_account_id.is_not(None),
            UserAccountProjection.billing_day.is_not(None),
            UserAccountProjection.payment_due_day.is_not(None),
        )
    ).all()
    roots = [a for a in candidates if credit_card_billing.is_billing_root(a)]

    executed = 0
    skipped = 0
    for group in roots:
        assert group.billing_day is not None and group.payment_due_day is not None
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(today, group.billing_day)
        due_date = credit_card.due_date_for_cycle_end(cycle_end, group.payment_due_day)
        if today < due_date:
            continue  # 还没到繳款截止日,不提前扣款

        cycle_end_iso = cycle_end.isoformat()
        if _already_marked(
            db, user_id=group.user_id, account_id=group.sync_id,
            cycle_end_iso=cycle_end_iso, kind="auto_pay_executed",
        ):
            continue  # 这一期已经自动繳过了,不因为截止日后又有新消費而重繳

        children = credit_card_billing.resolve_billing_children(db, account=group)
        if not children:
            continue

        # 2026-08-03:group 自己現在也可能直接持有分期期數交易(§2.3 第四輪),
        # 找 ledger 時併入 group 自己的 sync_id。
        child_ids = credit_card_billing.billing_member_ids(group, children)
        ledger_id = db.scalar(
            select(ReadTxProjection.ledger_id).where(
                ReadTxProjection.user_id == group.user_id,
                ReadTxProjection.account_sync_id.in_(child_ids),
            ).limit(1)
        )
        if ledger_id is None:
            continue
        ledger_external_id = notification_service.resolve_ledger_external_id(db, ledger_id)

        billing = credit_card_billing.compute_group_billing(
            db, ledger_id=ledger_id, group=group, children=children, now=now,
        )
        remaining_due = billing["remaining_due"]
        if remaining_due <= 0.01:
            continue  # 已繳清/溢繳,無需自動扣款

        from_account_id = group.auto_pay_from_account_id
        assert from_account_id is not None  # WHERE 已过滤
        balance = compute_account_balance(db, user_id=group.user_id, account_sync_id=from_account_id)
        if balance < remaining_due - 1e-9:
            if not _already_marked(
                db, user_id=group.user_id, account_id=group.sync_id,
                cycle_end_iso=cycle_end_iso, kind="auto_pay_skipped_insufficient",
            ):
                notification_service.create_notification(
                    db,
                    user_id=group.user_id,
                    category="card_due",
                    title=f"自動扣繳未執行：{group.name or '信用卡'}",
                    body=(
                        f"來源帳戶餘額不足(需要 {remaining_due:.2f},目前餘額 {balance:.2f}),"
                        f"本期自動扣繳未執行,系統會持續每 15 分鐘重試一次。"
                    ),
                    payload={
                        "accountId": group.sync_id,
                        "cycleEnd": cycle_end_iso,
                        "kind": "auto_pay_skipped_insufficient",
                        "ledgerId": ledger_external_id,
                    },
                )
                skipped += 1
            continue

        from_account_name = _account_name(db, user_id=group.user_id, sync_id=from_account_id)
        child_by_id = {c.sync_id: c for c in children}
        allocations = credit_card_billing.compute_card_payment_allocations(
            group_sync_id=group.sync_id,
            remaining_due_by_child=billing["per_child_remaining_due"],
            amount=remaining_due,
        )
        note = f"自動扣繳(帳單 {cycle_start.isoformat()}~{cycle_end_iso})"
        for target_id, amount in allocations.items():
            if amount <= 0:
                continue
            to_name = group.name if target_id == group.sync_id else child_by_id[target_id].name
            item: dict[str, Any] = {
                "syncId": new_sync_id("tx"),
                "type": "transfer",
                "amount": amount,
                "happenedAt": now.isoformat(),
                "note": note,
                "fromAccountId": from_account_id,
                "fromAccountName": from_account_name,
                "toAccountId": target_id,
                "toAccountName": to_name,
                "createdByUserId": group.user_id,
                "updatedByUserId": group.user_id,
            }
            emit_tx(db, ledger_id=ledger_id, user_id=group.user_id, now=now, item=item)

        notification_service.create_notification(
            db,
            user_id=group.user_id,
            category="card_due",
            title=f"自動扣繳成功：{group.name or '信用卡'}",
            body=f"已從指定帳戶自動繳清本期帳單 {remaining_due:.2f}。",
            payload={
                "accountId": group.sync_id,
                "cycleEnd": cycle_end_iso,
                "kind": "auto_pay_executed",
                "ledgerId": ledger_external_id,
            },
        )
        executed += 1

    if executed or skipped:
        logger.info(
            "credit_card_autopay: executed=%d skipped_insufficient=%d", executed, skipped,
        )
    return {"executed": executed, "skipped_insufficient": skipped}
