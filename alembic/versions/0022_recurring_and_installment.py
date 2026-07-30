"""read_recurring_rule_projection + read_installment_plan_projection

§2.2 週期性收支 / §2.3 分期付款(MOZE_FEATURE_GAP_SD.md Phase 1)的新 entity,
按 CLAUDE.md「新增 entity」checklist 第 1 步:新表 + migration。

Revision ID: 0022_recurring_and_installment
Revises: 0021_tx_refund_and_installment_link
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op


revision = "0022_recurring_and_installment"
down_revision = "0021_tx_refund_and_installment_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_recurring_rule_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("tx_type", sa.String(16), nullable=False, server_default="expense"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("from_account_sync_id", sa.String(255), nullable=True),
        sa.Column("to_account_sync_id", sa.String(255), nullable=True),
        sa.Column("frequency", sa.String(16), nullable=False, server_default="monthly"),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_recurring_rule_user_id",
        "read_recurring_rule_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_recurring_rule_due",
        "read_recurring_rule_projection",
        ["enabled", "next_run_at"],
    )

    op.create_table(
        "read_installment_plan_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("periods", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("period_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_period_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_period_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_periods", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("account_sync_id", sa.String(255), nullable=True),
        sa.Column("category_sync_id", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_installment_plan_user_id",
        "read_installment_plan_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_installment_plan_due",
        "read_installment_plan_projection",
        ["status", "next_period_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_installment_plan_due", table_name="read_installment_plan_projection")
    op.drop_index("ix_read_installment_plan_user_id", table_name="read_installment_plan_projection")
    op.drop_table("read_installment_plan_projection")

    op.drop_index("ix_read_recurring_rule_due", table_name="read_recurring_rule_projection")
    op.drop_index("ix_read_recurring_rule_user_id", table_name="read_recurring_rule_projection")
    op.drop_table("read_recurring_rule_projection")
