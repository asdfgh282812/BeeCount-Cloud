"""read_project_projection + read_tx_projection.project_sync_id

Phase 13(docs/PH13_PROJECT_SD.md)專案功能的新 entity,按 CLAUDE.md
「新增 entity」checklist 第 1 步:新表 + migration。合併 tx 欄位跟新表
成一支 migration,比照 0025_debts_and_tx_templates 當時的做法。

Revision ID: 0040_projects
Revises: 0039_tx_fee_discount
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op


revision = "0040_projects"
down_revision = "0039_tx_fee_discount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("project_sync_id", sa.String(255), nullable=True),
    )

    op.create_table(
        "read_project_projection",
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
        sa.Column("icon", sa.String(32), nullable=True),
        sa.Column("budget_amount", sa.Float(), nullable=True),
        sa.Column("period_type", sa.String(16), nullable=False, server_default="monthly"),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("carryover_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("visible_on_home", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_project_user_id", "read_project_projection", ["user_id"],
    )
    op.create_index(
        "ix_read_project_ledger_sort", "read_project_projection", ["ledger_id", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_project_ledger_sort", table_name="read_project_projection")
    op.drop_index("ix_read_project_user_id", table_name="read_project_projection")
    op.drop_table("read_project_projection")

    op.drop_column("read_tx_projection", "project_sync_id")
