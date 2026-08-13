"""read_tx_projection: to_amount — 跨幣別轉帳轉入金額

轉帳(tx_type=transfer)過去只有一個 `amount`,同時套用在轉出帳戶(扣)與轉入
帳戶(加),只在轉出/轉入帳戶同幣別時數字才正確。`to_amount` = 轉入帳戶自身
幣別的金額,只在轉出/轉入帳戶幣別不同時才有值;`amount` 欄位語意不變,仍是
轉出帳戶自身幣別、驅動轉出帳戶餘額增減的那個數。

NULL = 同幣別轉帳(舊資料、舊版 App 皆是如此) —— 所有讀取端一律
`COALESCE(to_amount, amount)`,語意上不需要回填,故不比照 0018 做全表
UPDATE。

Revision ID: 0044_tx_transfer_to_amount
Revises: 0043_account_include_in_total
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0044_tx_transfer_to_amount"
down_revision = "0043_account_include_in_total"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("to_amount", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "to_amount")
