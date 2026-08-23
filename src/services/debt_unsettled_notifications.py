"""借還款未結清對象清單(§5.5,對齐 Moze「通知中心：列出所有未結清對象」)。

跟 `debt_reminders.py` 是不同機制:那個只看 `due_at` 在 3 天內(含逾期)的
欠款;這個不管有沒有設定到期日,只要還沒結清(狀態 open/partial)就要出現,
按對象(counterparty)分組——同一對象底下可能有多筆欠款,只彙總成一條。

落地為 `Notification` 記錄(category="debt_unsettled",跟 `reminder` 同款
`pinned=True`,享有已讀狀態/跨裝置一致),不是純查詢式的 UI 呈現——每個
`(user, ledger, counterparty)` 分組維護「至多一條未讀通知」:
- 分組仍未結清 → 更新既有未讀通知的 title/body(反映最新總額/筆數),或
  建立新的一條(`created_at` 不變,不會被之後的更新洗到列表下方——`pinned`
  已經確保這點)。
- 分組已經全部結清/結案(不再出現在未結清集合裡)→ 把既有未讀通知標記
  已讀,等同「從清單消失」,不刪除歷史記錄。

調用入口:`services/scheduled_jobs.py`(統一排程器),跟 `debt_reminders`
同一個 15 分鐘 job_key 級距,各自獨立的 job_key,互不影響。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, ReadDebtProjection, ReadTxProjection
from . import notifications as notification_service

logger = logging.getLogger(__name__)


def sync_unsettled_counterparty_notifications(db: Session, *, now: datetime | None = None) -> int:
    """掃全部使用者的欠款,依 `(user_id, ledger_id, counterparty_name)` 分組
    算出未結清(open/partial)總額,建立/更新/自動清除對應的 pinned 通知。
    回傳這次建立+更新的分組數。不 commit——調用方決定事務邊界(跟
    `send_due_debt_reminders` 同款約定)。"""
    now = now or datetime.now(timezone.utc)

    debts = db.scalars(select(ReadDebtProjection)).all()
    if not debts:
        return 0

    debt_ids = [d.sync_id for d in debts]
    repaid_by_debt: dict[str, float] = {}
    for debt_sync_id, amount in db.execute(
        select(ReadTxProjection.debt_sync_id, ReadTxProjection.amount).where(
            ReadTxProjection.debt_sync_id.in_(debt_ids),
        )
    ).all():
        repaid_by_debt[debt_sync_id] = repaid_by_debt.get(debt_sync_id, 0.0) + abs(float(amount or 0))

    # 分組:(user_id, ledger_id, counterparty_name) -> {remaining, count}
    groups: dict[tuple[str, str, str], dict[str, float]] = {}
    for debt in debts:
        if debt.closed_at is not None:
            continue  # 手動結案,不算未結清(跟到期提醒同款排除規則)
        repaid = repaid_by_debt.get(debt.sync_id, 0.0)
        remaining = max(float(debt.principal_amount or 0) - repaid, 0.0)
        if remaining <= 0.01:
            continue  # 已結清
        key = (debt.user_id, debt.ledger_id, debt.counterparty_name or "")
        g = groups.setdefault(key, {"remaining": 0.0, "count": 0.0})
        g["remaining"] += remaining
        g["count"] += 1

    # 既有未讀的 debt_unsettled 通知,按 (user_id, ledger 外部 id,
    # counterparty_name) 索引,方便更新/自動清除比對。
    existing_by_key: dict[tuple[str, str, str], Notification] = {}
    unread_rows = db.scalars(
        select(Notification).where(
            Notification.category == "debt_unsettled",
            Notification.read_at.is_(None),
        )
    ).all()
    for row in unread_rows:
        payload = row.payload_json or {}
        ledger_external_id = payload.get("ledgerId")
        counterparty_name = payload.get("counterpartyName")
        if not isinstance(ledger_external_id, str) or not isinstance(counterparty_name, str):
            continue
        existing_by_key[(row.user_id, ledger_external_id, counterparty_name)] = row

    touched = 0
    seen_keys: set[tuple[str, str, str]] = set()
    for (user_id, ledger_id, counterparty_name), agg in groups.items():
        ledger_external_id = notification_service.resolve_ledger_external_id(db, ledger_id)
        if ledger_external_id is None:
            continue
        lookup_key = (user_id, ledger_external_id, counterparty_name)
        seen_keys.add(lookup_key)
        remaining_total = agg["remaining"]
        count = int(agg["count"])
        display_name = counterparty_name or "未命名對象"
        title = f"{display_name}有未結清款項"
        body = f"共 {count} 筆未結清,尚餘 {remaining_total:.2f}"

        existing = existing_by_key.get(lookup_key)
        if existing is not None:
            existing.title = title
            existing.body = body
            db.add(existing)
        else:
            notification_service.create_notification(
                db,
                user_id=user_id,
                category="debt_unsettled",
                title=title,
                body=body,
                payload={"ledgerId": ledger_external_id, "counterpartyName": counterparty_name},
                pinned=True,
            )
        touched += 1

    # 已經全部結清/結案的分組:既有未讀通知標記已讀,等同「從清單消失」。
    for lookup_key, row in existing_by_key.items():
        if lookup_key not in seen_keys:
            row.read_at = now
            db.add(row)

    if touched:
        logger.info(
            "debt_unsettled_notifications: touched %d counterparty groups", touched,
        )
    return touched
