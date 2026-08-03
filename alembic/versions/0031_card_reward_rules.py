"""read_card_reward_rule_projection

§2.9.5 信用卡紅利回饋(Phase 4.5,MOZE_FEATURE_GAP_SD.md)。新表,
user-global(PK=user_id+sync_id,同 user_account_projection 的模式)——
規則綁定的信用卡帳戶本身就是 user-global 實體,規則跟著走同一個 scope,
不掛任何 ledger。

Revision ID: 0031_card_reward_rules
Revises: 0030_installment_offset_breakdown
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_card_reward_rules"
down_revision = "0030_installment_offset_breakdown"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_card_reward_rule_projection",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("account_sync_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("category_sync_ids_json", sa.Text(), nullable=True),
        sa.Column("rate_type", sa.String(16), nullable=False, server_default="percentage"),
        sa.Column("rate_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rounding", sa.String(8), nullable=False, server_default="round"),
        sa.Column("calc_basis", sa.String(24), nullable=False, server_default="transaction_date"),
        sa.Column("interval", sa.String(16), nullable=False, server_default="billing_cycle"),
        sa.Column("min_spend_threshold", sa.Float(), nullable=True),
        sa.Column("min_tx_amount", sa.Float(), nullable=True),
        sa.Column("cap_amount", sa.Float(), nullable=True),
        sa.Column("cap_shared_key", sa.String(64), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sync_id"),
    )
    op.create_index(
        "ix_read_card_reward_rule_account",
        "read_card_reward_rule_projection",
        ["user_id", "account_sync_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_read_card_reward_rule_account",
        table_name="read_card_reward_rule_projection",
    )
    op.drop_table("read_card_reward_rule_projection")
