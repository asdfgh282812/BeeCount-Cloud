"""read_debt_projection + read_tx_template_projection + tx debt_sync_id

§2.5 借還款追蹤 / §2.7 範本(MOZE_FEATURE_GAP_SD.md Phase 3)的新 entity,
按 CLAUDE.md「新增 entity」checklist 第 1 步:新表 + migration。

Revision ID: 0025_debts_and_tx_templates
Revises: 0024_tx_splits
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op


revision = "0025_debts_and_tx_templates"
down_revision = "0024_tx_splits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("debt_sync_id", sa.String(255), nullable=True),
    )

    op.create_table(
        "read_debt_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("direction", sa.String(16), nullable=False, server_default="payable"),
        sa.Column("counterparty_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("principal_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_debt_user_id", "read_debt_projection", ["user_id"],
    )
    op.create_index(
        "ix_read_debt_ledger_due", "read_debt_projection", ["ledger_id", "due_at"],
    )

    op.create_table(
        "read_tx_template_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("tx_type", sa.String(16), nullable=False, server_default="expense"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("from_account_sync_id", sa.String(255), nullable=True),
        sa.Column("to_account_sync_id", sa.String(255), nullable=True),
        sa.Column("tag_sync_ids_json", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_tx_template_user_id", "read_tx_template_projection", ["user_id"],
    )
    op.create_index(
        "ix_read_tx_template_ledger_sort",
        "read_tx_template_projection",
        ["ledger_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_tx_template_ledger_sort", table_name="read_tx_template_projection")
    op.drop_index("ix_read_tx_template_user_id", table_name="read_tx_template_projection")
    op.drop_table("read_tx_template_projection")

    op.drop_index("ix_read_debt_ledger_due", table_name="read_debt_projection")
    op.drop_index("ix_read_debt_user_id", table_name="read_debt_projection")
    op.drop_table("read_debt_projection")

    op.drop_column("read_tx_projection", "debt_sync_id")
