"""借還款未結清對象清單(§5.5,對齐 Moze「通知中心：列出所有未結清對象」)。

跟 `debt_reminders.py` 是不同機制:那個只看 `due_at` 在 3 天內(含逾期)的
欠款;這個不管有沒有設定到期日,只要還沒結清(狀態 open/partial)就要出現,
按對象(counterparty)分組——同一對象底下可能有多筆欠款,只彙總成一條。

落地為 `Notification` 記錄(category="debt_unsettled",priority=2,享有
已讀狀態/跨裝置一致),不是純查詢式的 UI 呈現——每個
`(user, ledger, counterparty)` 分組維護「至多一條通知,只要分組仍未結清就
一直維持未讀」:
- 找既有通知時**不能只看未讀的**——使用者可能點過「全部已讀」或打開過
  通知把它標成已讀,若這時只查未讀會找不到既有記錄,誤判成「還沒建立過」
  而重複新建一條,導致同一個對象洗出一串重複通知(2026-09-05 實測踩到的
  bug)。改成查該 `(user, ledger, counterparty)` 底下**全部**歷史記錄
  (不分已讀/未讀),取最新的一筆當作既有記錄:
  - 分組仍未結清 → 更新既有記錄的 title/body(反映最新總額/筆數),並把
    `read_at` 重設回 `None`——只要還沒結清就該一直維持未讀,不因為使用者
    曾經讀過就不再提醒。真的找不到既有記錄才新建一條。
  - 同一分組若有多筆歷史記錄(舊 bug 留下的重複通知),除了最新一筆以外
    全部標記已讀,等同收斂回「至多一條」的不變量,不需要另外寫 backfill
    腳本清資料。
- 分組已經全部結清/結案(不再出現在未結清集合裡)→ 把既有記錄標記已讀,
  等同「從清單消失」,不刪除歷史記錄。

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
    算出未結清(open/partial)總額,建立/更新/自動清除對應的高優先度通知。
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

    # 既有的 debt_unsettled 通知(不分已讀/未讀——已讀的也要找到,否則會
    # 誤判成沒建立過而重複新建),按 (user_id, ledger 外部 id,
    # counterparty_name) 索引,同一個 key 只保留最新一筆,其餘視為重複記錄
    # 一併標記已讀收斂掉。
    existing_by_key: dict[tuple[str, str, str], Notification] = {}
    duplicate_rows: list[Notification] = []
    all_rows = db.scalars(
        select(Notification).where(Notification.category == "debt_unsettled")
    ).all()
    for row in all_rows:
        payload = row.payload_json or {}
        ledger_external_id = payload.get("ledgerId")
        counterparty_name = payload.get("counterpartyName")
        if not isinstance(ledger_external_id, str) or not isinstance(counterparty_name, str):
            continue
        key = (row.user_id, ledger_external_id, counterparty_name)
        current = existing_by_key.get(key)
        if current is None or (row.created_at, row.id) > (current.created_at, current.id):
            if current is not None:
                duplicate_rows.append(current)
            existing_by_key[key] = row
        else:
            duplicate_rows.append(row)

    for dup in duplicate_rows:
        if dup.read_at is None:
            dup.read_at = now
            db.add(dup)

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
            # 只要分組還沒結清就一直維持未讀,不因為使用者曾經讀過/點過
            # 「全部已讀」就不再提醒——否則下次掃描找不到未讀記錄會誤判成
            # 沒建立過而重複新建一條。
            existing.read_at = None
            db.add(existing)
        else:
            notification_service.create_notification(
                db,
                user_id=user_id,
                category="debt_unsettled",
                title=title,
                body=body,
                payload={"ledgerId": ledger_external_id, "counterpartyName": counterparty_name},
                priority=2,
            )
        touched += 1

    # 已經全部結清/結案的分組:既有記錄標記已讀,等同「從清單消失」。
    for lookup_key, row in existing_by_key.items():
        if lookup_key not in seen_keys:
            row.read_at = now
            db.add(row)

    if touched:
        logger.info(
            "debt_unsettled_notifications: touched %d counterparty groups", touched,
        )
    return touched
