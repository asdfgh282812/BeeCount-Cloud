"""一次性回填腳本(2026-08 使用者回饋:週期性交易自動產生時遺失回饋/手續費
折扣):

`RecurringRule` entity 原本刻意不儲存 `base_amount/fee_amount/fee_label/
discount_amount/discount_label/reward_rule_sync_ids_json`(見 models.py 修改
前的 docstring),導致規則之後自動產生的每一期 occurrence 都沒有這些欄位可以
繼承——只有「建交易當下順便設週期」那個特例的第一筆,因為整包複製原始請求
才剛好保留。write endpoint / materializer 已經改成會把這六個欄位當「規則
固定屬性」儲存並轉發,但這只解決**之後**新建的規則;上線前就已經存在、規則
本身這六個欄位是 NULL 的舊規則不會被自動修好，因為它們早就已經生成過至少一
期(通常是使用者手動建立、資料正確的那第一筆)，只是規則本身沒有把這份資料
存下來。

這支腳本針對每一條「規則本身完全沒有這六個欄位」的規則，找它名下**最早一筆
帶有** `base_amount`（用過手續費/折扣）或 `reward_rule_sync_ids_json`（勾選過
回饋規則）**的 occurrence 交易，把那一筆的六個欄位複製回規則本身**，用跟一般
web 寫入路徑相同的「sync push 等价」方式（`SyncChange` + `sync_applier.
apply_change_to_projection`）局部更新（只帶這六個 key，其餘欄位靠既有 merge
邏輯保留原樣）。

**只補「規則本身」的欄位，不回頭改寫已經生成過的歷史交易**——比照
`scripts/backfill_recurring_installment_categories.py` 的既定慣例：回填的
意義在於「規則補上之後，之後自動產生的下一筆才會正確帶上這些欄位」，不修改
使用者已經在畫面上看到的既有交易記錄。

用法:
    cd /path/to/BeeCount-Cloud
    python -m scripts.backfill_recurring_rule_fee_discount_reward
    python -m scripts.backfill_recurring_rule_fee_discount_reward --dry-run
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import or_, select

from src.concurrency import lock_ledger_for_materialize
from src.database import SessionLocal
from src.models import ReadRecurringRuleProjection, ReadTxProjection, SyncChange
from src.sync_applier import apply_change_to_projection

_DEVICE_ID = "server-recurring-fee-discount-reward-backfill"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill base_amount/fee_amount/fee_label/discount_amount/"
            "discount_label/reward_rule_ids on recurring rules from their "
            "earliest occurrence that already carries this data"
        )
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be fixed, do not write")
    return p.parse_args()


def _emit_fee_discount_reward_patch(
    db, *, rule: ReadRecurringRuleProjection, source_tx: ReadTxProjection,
) -> None:
    """跟 web 寫入路徑同款的「局部更新」寫法:只帶這六個 key,其它欄位由
    `apply_change_to_projection` 內部的 merge 邏輯從既有行補齊。"""
    lock_ledger_for_materialize(db, rule.ledger_id)
    now = datetime.now(timezone.utc)
    payload = {
        "syncId": rule.sync_id,
        "baseAmount": source_tx.base_amount,
        "feeAmount": source_tx.fee_amount,
        "feeLabel": source_tx.fee_label,
        "discountAmount": source_tx.discount_amount,
        "discountLabel": source_tx.discount_label,
    }
    if source_tx.reward_rule_sync_ids_json:
        try:
            reward_ids = json.loads(source_tx.reward_rule_sync_ids_json)
            if isinstance(reward_ids, list):
                payload["rewardRuleIds"] = reward_ids
        except json.JSONDecodeError:
            pass
    change = SyncChange(
        user_id=rule.user_id,
        ledger_id=rule.ledger_id,
        scope="ledger",
        entity_type="recurring_rule",
        entity_sync_id=rule.sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_DEVICE_ID,
        updated_by_user_id=rule.user_id,
    )
    db.add(change)
    db.flush()
    apply_change_to_projection(db, ledger_id=rule.ledger_id, ledger_owner_id=rule.user_id, change=change)


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        rule_rows = db.execute(
            select(ReadRecurringRuleProjection).where(
                ReadRecurringRuleProjection.base_amount.is_(None),
                ReadRecurringRuleProjection.reward_rule_sync_ids_json.is_(None),
            )
        ).scalars().all()

        print(f"found {len(rule_rows)} recurring rule(s) with no fee/discount/reward data")

        fixed = 0
        skipped = 0
        for rule in rule_rows:
            source_tx = db.execute(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == rule.ledger_id,
                    ReadTxProjection.recurring_rule_sync_id == rule.sync_id,
                    or_(
                        ReadTxProjection.base_amount.is_not(None),
                        ReadTxProjection.reward_rule_sync_ids_json.is_not(None),
                    ),
                ).order_by(ReadTxProjection.happened_at.asc()).limit(1)
            ).scalar_one_or_none()
            if source_tx is None:
                skipped += 1
                continue
            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} recurring_rule {rule.sync_id} "
                f"(user={rule.user_id}) <- tx {source_tx.sync_id} "
                f"(base_amount={source_tx.base_amount}, reward_rule_ids={source_tx.reward_rule_sync_ids_json})"
            )
            if args.dry_run:
                continue
            _emit_fee_discount_reward_patch(db, rule=rule, source_tx=source_tx)
            fixed += 1

        if not args.dry_run:
            db.commit()
        print(f"\nDone. fixed={fixed} skipped_no_source_tx={skipped}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
