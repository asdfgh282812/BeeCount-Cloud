"""一次性回填腳本(2026-09-06 使用者回饋:日幣卡的信用卡回饋金加總失真):

`card_reward_payout._emit_reward_tx` 原本組出的交易 payload 完全沒有
`currencyCode`/`nativeAmount` 兩個欄位——回饋帳戶幣別跟帳本本位幣不同時
(例如日幣信用卡、帳本本位幣是台幣),App 拉取新交易對缺鍵退化成
`nativeAmount = amount`(1:1 原樣帶入日幣數字),導致週曆/月報等「帳本維度
統計讀 `nativeAmount`」的加總把日幣金額直接當台幣加,總額嚴重失真
(對照:帳戶維度的餘額/應繳計算讀的是 `amount` 本身,不受影響,金額顯示
一直是對的,只有統計加總錯)。程式碼本身已經修好(`_emit_reward_tx` 現在
會呼叫 `_currency_fields_for_reward` 補上這兩個欄位),但這支腳本上線
**之前**已經生成、卡在 projection/App 本地資料庫裡的歷史回饋交易不會被
自動修好——這支腳本就是補這些舊資料。

只找「有實際入帳」的回饋(`CardRewardPayout.payout_tx_sync_id` 不是
NULL——金額被上限夾到 0 的那些從未 emit 過交易,沒有東西可修),而且只挑
`currency_code IS NULL`(還沒被修過,含手動編輯過一次、已經有正確
`currencyCode` 的舊回饋交易也會被排除,不會被這支腳本誤動)且「回饋
帳戶幣別 != 帳本本位幣」的列。換算邏輯直接重用 `card_reward_payout.
_currency_fields_for_reward`(手動 override 優先,自動源 fetcher 次之,
兩者皆缺退化 1:1),跟程式碼修復後新產生的回饋交易走同一套規則,保持
一致——**唯一差異**是這裡沒有歷史匯率可查,一律是用「跑這支腳本當下」的
匯率換算,不是回饋金當初入帳那天的匯率,跟 App 端既有的「重新計算外幣
折算」L11 橫幅(`recomputeForeignTxForLedger`)同樣的已知限制,只能盡量
接近、不是精確重現。

寫入方式比照 `scripts/backfill_recurring_rule_fee_discount_reward.py`:
局部更新(`SyncChange` + `sync_applier.apply_change_to_projection`),只帶
`syncId`/`currencyCode`/`nativeAmount` 三個 key,不帶 `amount`——
`merge_with_existing`/`_sync_native_amount_after_merge` 因此不會誤觸發
「amount 變了要等比縮放」那條分支,其餘欄位(note、分類等)完全不動。
projection 修正落地後,下次任一裝置同步 pull 就會拿到正確的
`currencyCode`/`nativeAmount`,不需要使用者在 App 端額外操作。

用法:
    cd /path/to/BeeCount-Cloud
    python -m scripts.backfill_card_reward_currency
    python -m scripts.backfill_card_reward_currency --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select

from src.concurrency import lock_ledger_for_materialize
from src.database import SessionLocal
from src.models import CardRewardPayout, ReadTxProjection, SyncChange
from src.services.card_reward_payout import _currency_fields_for_reward
from src.sync_applier import apply_change_to_projection

_DEVICE_ID = "server-card-reward-currency-backfill"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill currencyCode/nativeAmount on historical card-reward "
            "transactions whose reward account currency differs from the "
            "ledger's base currency"
        )
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be fixed, do not write")
    return p.parse_args()


def _emit_currency_patch(db, *, tx: ReadTxProjection, fields: dict[str, object]) -> None:
    lock_ledger_for_materialize(db, tx.ledger_id)
    now = datetime.now(timezone.utc)
    payload: dict[str, object] = {"syncId": tx.sync_id, **fields}
    change = SyncChange(
        user_id=tx.user_id,
        ledger_id=tx.ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx.sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_DEVICE_ID,
        updated_by_user_id=tx.user_id,
    )
    db.add(change)
    db.flush()
    apply_change_to_projection(db, ledger_id=tx.ledger_id, ledger_owner_id=tx.user_id, change=change)


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        payout_rows = db.execute(
            select(CardRewardPayout).where(CardRewardPayout.payout_tx_sync_id.is_not(None))
        ).scalars().all()
        print(f"found {len(payout_rows)} settled card-reward payout(s) to check")

        fixed = 0
        skipped_already_set = 0
        skipped_no_tx = 0
        skipped_same_currency = 0
        for payout in payout_rows:
            tx = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == payout.payout_tx_sync_id)
            )
            if tx is None:
                skipped_no_tx += 1  # 回饋交易本身已被刪除(退款沖銷/編輯重算等既有流程)
                continue
            if tx.currency_code is not None:
                skipped_already_set += 1  # 已经修过或本来就有值,不重复动
                continue

            fields = _currency_fields_for_reward(
                db, user_id=tx.user_id, ledger_id=tx.ledger_id,
                reward_account_id=tx.account_sync_id, amount=tx.amount,
            )
            if not fields:
                skipped_same_currency += 1  # 回饋帳戶幣別本來就等於帳本本位幣,不需要修
                continue

            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} tx {tx.sync_id} "
                f"(user={tx.user_id}, ledger={tx.ledger_id}) amount={tx.amount} -> {fields}"
            )
            if args.dry_run:
                continue
            _emit_currency_patch(db, tx=tx, fields=fields)
            fixed += 1

        if not args.dry_run:
            db.commit()
        print(
            f"\nDone. fixed={fixed} skipped_already_set={skipped_already_set} "
            f"skipped_no_tx={skipped_no_tx} skipped_same_currency={skipped_same_currency}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
