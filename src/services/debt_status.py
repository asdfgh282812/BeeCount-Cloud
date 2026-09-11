"""欠款即時結清狀態查詢 —— 給通知列表排序用(2026-09-11)。

`debt_reminders.py`/`debt_unsettled_notifications.py` 兩個背景 job 建立
notification 時把 `priority=2` 寫死在那筆記錄上;債務事後還清了,`priority`
欄位不會回頭更新(`debt_unsettled_notifications` 唯一做的是把 `read_at`
重設為已讀,見該檔案最後一段迴圈,但 `priority` 仍留在 2)。通知列表排序
需要「已結清/已結案就不再享有較高優先度」,所以在查詢當下重新計算一次
即時狀態,不去動兩個 job 本身的寫入邏輯與既有記錄。

跟兩個 job 用同一套公式:repaid = sum(abs(amount)),
remaining = principal_amount - repaid,remaining <= 0.01 視為已結清,
`closed_at` 有值視為已結案 —— 抽成共用函式避免第三份重複。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ledger, ReadDebtProjection, ReadTxProjection


def _repaid_by_debt(db: Session, debt_sync_ids: list[str]) -> dict[str, float]:
    if not debt_sync_ids:
        return {}
    repaid: dict[str, float] = {}
    for debt_sync_id, amount in db.execute(
        select(ReadTxProjection.debt_sync_id, ReadTxProjection.amount).where(
            ReadTxProjection.debt_sync_id.in_(debt_sync_ids),
        )
    ).all():
        repaid[debt_sync_id] = repaid.get(debt_sync_id, 0.0) + abs(float(amount or 0))
    return repaid


def _ledger_ids_by_external(
    db: Session, *, user_id: str, external_ids: set[str]
) -> dict[str, str]:
    """回傳 {external_id: 內部 id},限定該使用者名下的帳本 —— `external_id`
    只在同一使用者底下唯一(見 `Ledger.uq_ledgers_user_external`)。"""
    if not external_ids:
        return {}
    return dict(
        db.execute(
            select(Ledger.external_id, Ledger.id).where(
                Ledger.user_id == user_id,
                Ledger.external_id.in_(external_ids),
            )
        ).all()
    )


def unsettled_debt_reminder_keys(
    db: Session, *, user_id: str, pairs: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """`pairs` = {(ledgerExternalId, debtId)},取自 category='reminder' 通知
    的 payload。回傳其中「現在」仍未結清的子集 —— 債務已被刪除或已結案/
    還清的,都不算在內。"""
    if not pairs:
        return set()
    ledger_ids = _ledger_ids_by_external(db, user_id=user_id, external_ids={p[0] for p in pairs})
    if not ledger_ids:
        return set()
    debt_ids = {p[1] for p in pairs}
    debts = db.scalars(
        select(ReadDebtProjection).where(
            ReadDebtProjection.sync_id.in_(debt_ids),
            ReadDebtProjection.ledger_id.in_(ledger_ids.values()),
        )
    ).all()
    external_by_internal = {v: k for k, v in ledger_ids.items()}
    debts_by_key = {(external_by_internal[d.ledger_id], d.sync_id): d for d in debts}
    repaid = _repaid_by_debt(db, list(debt_ids))
    out: set[tuple[str, str]] = set()
    for pair in pairs:
        debt = debts_by_key.get(pair)
        if debt is None or debt.closed_at is not None:
            continue
        remaining = float(debt.principal_amount or 0) - repaid.get(debt.sync_id, 0.0)
        if remaining > 0.01:
            out.add(pair)
    return out


def unsettled_counterparty_group_keys(
    db: Session, *, user_id: str, pairs: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """`pairs` = {(ledgerExternalId, counterpartyName)},取自
    category='debt_unsettled' 通知的 payload。回傳其中「現在」仍未結清的
    子集,分組加總邏輯對齊
    `debt_unsettled_notifications.sync_unsettled_counterparty_notifications`。"""
    if not pairs:
        return set()
    ledger_ids = _ledger_ids_by_external(db, user_id=user_id, external_ids={p[0] for p in pairs})
    if not ledger_ids:
        return set()
    external_by_internal = {v: k for k, v in ledger_ids.items()}
    debts = db.scalars(
        select(ReadDebtProjection).where(ReadDebtProjection.ledger_id.in_(ledger_ids.values()))
    ).all()
    repaid = _repaid_by_debt(db, [d.sync_id for d in debts])
    remaining_by_group: dict[tuple[str, str], float] = {}
    for debt in debts:
        if debt.closed_at is not None:
            continue
        ledger_ext = external_by_internal.get(debt.ledger_id)
        if ledger_ext is None:
            continue
        remaining = float(debt.principal_amount or 0) - repaid.get(debt.sync_id, 0.0)
        if remaining <= 0.01:
            continue
        key = (ledger_ext, debt.counterparty_name or "")
        remaining_by_group[key] = remaining_by_group.get(key, 0.0) + remaining
    return {pair for pair in pairs if remaining_by_group.get(pair, 0.0) > 0.01}
