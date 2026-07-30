"""installment amortization fields + read_installment_period_projection +
recurring window generation fields

§2.12 Phase 1.5(MOZE_FEATURE_GAP_SD.md)设计修正:
- read_installment_plan_projection 加攤還算法相关 6 个字段(repayment_method/
  interest_period/interest_rate/round_amounts/remainder_position/
  grace_period_months)
- 新表 read_installment_period_projection:每期本金/利息/合计明细
- read_tx_projection 加 recurring_rule_sync_id(反查)+
  recurring_occurrence_overridden(单期编辑标记)
- read_recurring_rule_projection 加 generated_until_at(视窗续产生进度)+
  advanced_rule_json(简单 frequency+interval 表达不了的进阶规则)

Revision ID: 0023_installment_amortization_and_recurring_windows
Revises: 0022_recurring_and_installment
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op


revision = "0023_installment_amortization_and_recurring_windows"
down_revision = "0022_recurring_and_installment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_installment_plan_projection",
        sa.Column(
            "repayment_method", sa.String(32), nullable=False,
            server_default="equal_principal",
        ),
    )
    op.add_column(
        "read_installment_plan_projection",
        sa.Column(
            "interest_period", sa.String(16), nullable=False,
            server_default="monthly",
        ),
    )
    op.add_column(
        "read_installment_plan_projection",
        sa.Column("interest_rate", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "read_installment_plan_projection",
        sa.Column(
            "round_amounts", sa.Boolean(), nullable=False, server_default=sa.true(),
        ),
    )
    op.add_column(
        "read_installment_plan_projection",
        sa.Column(
            "remainder_position", sa.String(16), nullable=False, server_default="last",
        ),
    )
    op.add_column(
        "read_installment_plan_projection",
        sa.Column(
            "grace_period_months", sa.Integer(), nullable=False, server_default="0",
        ),
    )

    op.create_table(
        "read_installment_period_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("plan_sync_id", sa.String(255), nullable=False),
        sa.Column("period_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("interest_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="generated"),
        sa.Column("tx_sync_id", sa.String(255), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_read_installment_period_user_id",
        "read_installment_period_projection",
        ["user_id"],
    )
    op.create_index(
        "ix_read_installment_period_plan",
        "read_installment_period_projection",
        ["plan_sync_id", "period_no"],
    )

    op.add_column(
        "read_tx_projection",
        sa.Column("recurring_rule_sync_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column(
            "recurring_occurrence_overridden", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("generated_until_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("advanced_rule_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_recurring_rule_projection", "advanced_rule_json")
    op.drop_column("read_recurring_rule_projection", "generated_until_at")

    op.drop_column("read_tx_projection", "recurring_occurrence_overridden")
    op.drop_column("read_tx_projection", "recurring_rule_sync_id")

    op.drop_index("ix_read_installment_period_plan", table_name="read_installment_period_projection")
    op.drop_index("ix_read_installment_period_user_id", table_name="read_installment_period_projection")
    op.drop_table("read_installment_period_projection")

    op.drop_column("read_installment_plan_projection", "grace_period_months")
    op.drop_column("read_installment_plan_projection", "remainder_position")
    op.drop_column("read_installment_plan_projection", "round_amounts")
    op.drop_column("read_installment_plan_projection", "interest_rate")
    op.drop_column("read_installment_plan_projection", "interest_period")
    op.drop_column("read_installment_plan_projection", "repayment_method")
