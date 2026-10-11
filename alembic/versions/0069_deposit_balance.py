"""read_deposit_balance_projection(訂金尾款追蹤)

對齊 doc.moze.app/record/deposit。新 entity `deposit_balance`,ledger-scoped,
PK=(ledger_id, sync_id),跟 read_debt_projection 同款。尾款在補上前只是這張
表的一列(不是交易),所以不影響任何統計/餘額;補上時才建立真實交易並把
status 翻成 completed、寫 completed_tx_sync_id。

Revision ID: 0069_deposit_balance
Revises: 0068_quote_open_price_market_closures
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op


revision = "0069_deposit_balance"
down_revision = "0068_quote_open_price_market_closures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_deposit_balance_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source_tx_sync_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("completed_tx_sync_id", sa.String(255), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_deposit_balance_user_id",
        "read_deposit_balance_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_deposit_balance_ledger_due",
        "read_deposit_balance_projection",
        ["ledger_id", "due_at"],
    )
    op.create_index(
        "ix_read_deposit_balance_source_tx",
        "read_deposit_balance_projection",
        ["ledger_id", "source_tx_sync_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_read_deposit_balance_source_tx",
        table_name="read_deposit_balance_projection",
    )
    op.drop_index(
        "ix_read_deposit_balance_ledger_due",
        table_name="read_deposit_balance_projection",
    )
    op.drop_index(
        "ix_read_deposit_balance_user_id",
        table_name="read_deposit_balance_projection",
    )
    op.drop_table("read_deposit_balance_projection")
