"""拆帳欠款明細(App v67):投影表補上欠款明細欄位

App 的拆帳可以有「欠款明細」(支出拆帳=應收、收入拆帳=應付):splits 項帶
`debtId`、沒有 `categoryId`,背後是一筆 `originTxId` 指回這筆交易的 debt。
這部分是借貸不是收支,統計/預算要扣掉,帳戶餘額照整筆算。

- `read_tx_projection.debt_split_amount`:欠款明細金額合計(原幣),統計口徑
  見 `src/stats_amount.py`。
- `read_tx_split_projection.debt_sync_id`:欠款明細那一列指向的 debt。

回填:`splits_json` 原本就存 App 送來的原始 JSON(含 `debtId`),只是投影時
把沒有 `categoryId` 的項目濾掉了。這裡從 `splits_json` 重建含 `debtId` 的交易
的明細列,並算出 `debt_split_amount`。

Revision ID: 0066_split_debt_lines
Revises: 0065_card_reward_basic_and_project
Create Date: 2026-10-10
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0066_split_debt_lines"
down_revision = "0065_card_reward_basic_and_project"
branch_labels = None
depends_on = None


def _as_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("debt_split_amount", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "read_tx_split_projection",
        sa.Column("debt_sync_id", sa.String(255), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT ledger_id, sync_id, user_id, splits_json FROM read_tx_projection "
        "WHERE has_splits = :t AND splits_json LIKE :pat"
    ), {"t": True, "pat": "%debtId%"}).all()
    for ledger_id, tx_sync_id, user_id, splits_json in rows:
        try:
            splits = json.loads(splits_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(splits, list):
            continue
        debt_total = 0.0
        new_rows = []
        for idx, entry in enumerate(splits):
            if not isinstance(entry, dict):
                continue
            debt_id = entry.get("debtId")
            category_id = entry.get("categoryId")
            if not debt_id and not category_id:
                continue
            amount = _as_float(entry.get("amount"))
            sort_order = entry.get("sortOrder")
            new_rows.append({
                "ledger_id": ledger_id,
                "tx_sync_id": tx_sync_id,
                "sort_order": int(sort_order) if isinstance(sort_order, (int, float)) else idx,
                "user_id": user_id,
                "category_sync_id": None if debt_id else str(category_id),
                "category_name": None if debt_id else entry.get("categoryName"),
                "amount": amount,
                "note": entry.get("note"),
                "debt_sync_id": str(debt_id) if debt_id else None,
            })
            if debt_id:
                debt_total += amount
        bind.execute(sa.text(
            "DELETE FROM read_tx_split_projection WHERE ledger_id = :l AND tx_sync_id = :t"
        ), {"l": ledger_id, "t": tx_sync_id})
        for row in new_rows:
            bind.execute(sa.text(
                "INSERT INTO read_tx_split_projection "
                "(ledger_id, tx_sync_id, sort_order, user_id, category_sync_id, "
                "category_name, amount, note, debt_sync_id) VALUES "
                "(:ledger_id, :tx_sync_id, :sort_order, :user_id, :category_sync_id, "
                ":category_name, :amount, :note, :debt_sync_id)"
            ), row)
        bind.execute(sa.text(
            "UPDATE read_tx_projection SET debt_split_amount = :a "
            "WHERE ledger_id = :l AND sync_id = :t"
        ), {"a": debt_total, "l": ledger_id, "t": tx_sync_id})


def downgrade() -> None:
    op.drop_column("read_tx_split_projection", "debt_sync_id")
    op.drop_column("read_tx_projection", "debt_split_amount")
