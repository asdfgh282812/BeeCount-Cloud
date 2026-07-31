"""transaction category splits (§2.4 拆帳 MOZE_FEATURE_GAP_SD.md Phase 2)

- read_tx_projection 加 has_splits(快速判断标记)+ splits_json(LWW merge
  fallback 用,跟 attachments_json 同款模式,权威值仍是 read_tx_split_projection)
- 新表 read_tx_split_projection:一笔交易拆成多个分类的明细行,
  (ledger_id, tx_sync_id, sort_order) 复合 PK,每次交易 upsert 时整批
  delete-then-insert(见 projection.py replace_tx_splits)

Revision ID: 0024_tx_splits
Revises: 0023_installment_amortization_and_recurring_windows
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op


revision = "0024_tx_splits"
down_revision = "0023_installment_amortization_and_recurring_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column(
            "has_splits", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("splits_json", sa.Text(), nullable=True),
    )

    op.create_table(
        "read_tx_split_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("tx_sync_id", sa.String(255), primary_key=True),
        sa.Column("sort_order", sa.Integer(), primary_key=True, server_default="0"),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_read_tx_split_user_id",
        "read_tx_split_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_tx_split_ledger_tx",
        "read_tx_split_projection",
        ["ledger_id", "tx_sync_id"],
    )
    op.create_index(
        "ix_read_tx_split_ledger_category",
        "read_tx_split_projection",
        ["ledger_id", "category_sync_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_tx_split_ledger_category", table_name="read_tx_split_projection")
    op.drop_index("ix_read_tx_split_ledger_tx", table_name="read_tx_split_projection")
    op.drop_index("ix_read_tx_split_user_id", table_name="read_tx_split_projection")
    op.drop_table("read_tx_split_projection")

    op.drop_column("read_tx_projection", "splits_json")
    op.drop_column("read_tx_projection", "has_splits")
