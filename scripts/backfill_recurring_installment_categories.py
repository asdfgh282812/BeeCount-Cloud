"""一次性回填腳本(需求 #14,2026-08 使用者回饋改善 SD Phase 12):

分類必填校驗(`_assert_category_required`,見
`src/routers/write/_shared.py`)只擋得住**之後新建/編輯**的週期性收支規則
(非轉帳)與分期付款計畫,擋不住上線前就已經存在、`category_sync_id` 是
NULL 的舊資料——這些舊規則會持續產生沒有分類的交易(週期性收支靠
`services.recurring_materializer.refill_recurring_windows` 續產生未來
occurrence,分期付款雖然所有期數都在建立當下一次生成完、不會再產生新的,
但規則本身的分類欄位仍應該補上,對齊報表/管理頁的顯示)。

這支腳本把每一條缺分類的規則/計畫,歸到該使用者名下「未分類」這個專屬
分類（`services.card_rewards.ensure_uncategorized_category`，跟
`ensure_reward_category`/`ensure_refund_category`/`ensure_debt_category`
同一套「找不到就建」模式，同名同 kind 只會建一次），並用跟一般 web 寫入
路徑相同的「sync push 等价」方式（`SyncChange` + `sync_applier.
apply_change_to_projection`）局部更新（只帶 `categoryId`，其餘欄位靠既有
merge 邏輯保留原樣）——不是直接改 projection 表，這樣其它裝置下次同步時
也能正確拉到這次補上的分類，且不會在下一次 `_commit_write` 重新 diff 快照
時被判定為「本來就沒有」而撤銷。

**只補「規則/計畫本身」的分類**，不回頭改寫已經生成過的歷史交易——週期性
收支的舊 occurrence 已經是既定事實，改分類的意義主要在於「回填之後，這條
規則產生的下一筆交易起才會正確帶上分類」；分期付款所有期數在建立當下就已
生成完畢，回填只補計畫本身的分類欄位（管理頁顯示用），不會回頭改寫已生成
的各期交易。

用法:
    cd /path/to/BeeCount-Cloud
    python -m scripts.backfill_recurring_installment_categories
    python -m scripts.backfill_recurring_installment_categories --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from src.concurrency import lock_ledger_for_materialize
from src.database import SessionLocal
from src.models import ReadInstallmentPlanProjection, ReadRecurringRuleProjection, SyncChange
from src.services.card_rewards import ensure_uncategorized_category
from src.sync_applier import apply_change_to_projection

_DEVICE_ID = "server-uncategorized-backfill"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill category_sync_id for recurring rules / installment plans created before it was required"
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be fixed, do not write")
    return p.parse_args()


def _emit_category_patch(
    db, *, ledger_id: str, entity_type: str, sync_id: str, user_id: str, category_sync_id: str
) -> None:
    """跟 web 寫入路徑同款的「局部更新」寫法:只帶 categoryId,其它欄位由
    `apply_change_to_projection` 內部的 merge 邏輯從既有行補齊,不會誤把
    其它欄位覆蓋掉。"""
    lock_ledger_for_materialize(db, ledger_id)
    now = datetime.now(timezone.utc)
    change = SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type=entity_type,
        entity_sync_id=sync_id,
        action="upsert",
        payload_json={"syncId": sync_id, "categoryId": category_sync_id},
        updated_at=now,
        updated_by_device_id=_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change)
    db.flush()
    # 沿用該筆規則/計畫既有的 user_id(而非某個「帳本擁有者」概念)——
    # projection.upsert_recurring_rule/upsert_installment_plan 每次 upsert
    # 都會把 user_id 參數原樣寫回那一列,傳錯值等於把這條規則的建立者悄悄
    # 改掉。
    apply_change_to_projection(db, ledger_id=ledger_id, ledger_owner_id=user_id, change=change)


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        rule_rows = db.execute(
            select(ReadRecurringRuleProjection).where(
                ReadRecurringRuleProjection.tx_type != "transfer",
                ReadRecurringRuleProjection.category_sync_id.is_(None),
            )
        ).scalars().all()
        plan_rows = db.execute(
            select(ReadInstallmentPlanProjection).where(
                ReadInstallmentPlanProjection.category_sync_id.is_(None),
            )
        ).scalars().all()

        print(
            f"found {len(rule_rows)} recurring rule(s) and {len(plan_rows)} "
            "installment plan(s) without a category"
        )

        fixed_rules = 0
        for row in rule_rows:
            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} recurring_rule {row.sync_id} "
                f"(user={row.user_id}, tx_type={row.tx_type})"
            )
            if args.dry_run:
                continue
            category_id = ensure_uncategorized_category(db, user_id=row.user_id, kind=row.tx_type)
            _emit_category_patch(
                db,
                ledger_id=row.ledger_id,
                entity_type="recurring_rule",
                sync_id=row.sync_id,
                user_id=row.user_id,
                category_sync_id=category_id,
            )
            fixed_rules += 1

        fixed_plans = 0
        for row in plan_rows:
            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} installment_plan {row.sync_id} "
                f"(user={row.user_id})"
            )
            if args.dry_run:
                continue
            # 分期付款恆為 expense,無轉帳語意,見 WriteInstallmentPlanCreateRequest。
            category_id = ensure_uncategorized_category(db, user_id=row.user_id, kind="expense")
            _emit_category_patch(
                db,
                ledger_id=row.ledger_id,
                entity_type="installment_plan",
                sync_id=row.sync_id,
                user_id=row.user_id,
                category_sync_id=category_id,
            )
            fixed_plans += 1

        if not args.dry_run:
            db.commit()
        print(f"\nDone. fixed_rules={fixed_rules} fixed_plans={fixed_plans}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
