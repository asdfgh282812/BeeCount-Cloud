"""read_tx_projection.base_amount/fee_amount/fee_label/discount_amount/discount_label

2026-08 使用者需求:比照 Moze(record/introduction)金額旁邊的「手續費/折扣」
額外金額欄位,名稱可自訂。`base_amount` 是使用者輸入的原始金額(信用卡回饋
計算的權威基準,見 src/services/card_rewards.py::_reward_base_amount),
既有 `amount` 欄位語意不變,仍是換算後、實際影響帳戶餘額的總額。五欄皆
nullable——`base_amount IS NULL` = 從未使用過這個功能,行為與既有交易完全
一致。

Revision ID: 0039_tx_fee_discount
Revises: 0038_tx_merchant
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op


revision = "0039_tx_fee_discount"
down_revision = "0038_tx_merchant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("base_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("fee_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("fee_label", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("discount_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("discount_label", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "discount_label")
    op.drop_column("read_tx_projection", "discount_amount")
    op.drop_column("read_tx_projection", "fee_label")
    op.drop_column("read_tx_projection", "fee_amount")
    op.drop_column("read_tx_projection", "base_amount")
