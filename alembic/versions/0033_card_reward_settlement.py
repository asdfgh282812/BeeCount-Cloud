"""信用卡紅利回饋自動入帳(§2.9.5.4)

新增規則的結算時機/目的帳戶欄位(settlement_type/settlement_days/
reward_account_id),以及自動入帳去重台帳 card_reward_payouts。詳見
`src/services/card_reward_payout.py` docstring。

Revision ID: 0033_card_reward_settlement
Revises: 0032_tx_reward_rule_ids
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_card_reward_settlement"
down_revision = "0032_tx_reward_rule_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("settlement_type", sa.String(24), nullable=False, server_default="manual"),
    )
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("settlement_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("reward_account_id", sa.String(255), nullable=True),
    )
    op.create_table(
        "card_reward_payouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_sync_id", sa.String(255), nullable=False),
        sa.Column("dedup_key", sa.String(255), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("payout_tx_sync_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ux_card_reward_payouts_dedup",
        "card_reward_payouts",
        ["user_id", "rule_sync_id", "dedup_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_card_reward_payouts_dedup", table_name="card_reward_payouts")
    op.drop_table("card_reward_payouts")
    op.drop_column("read_card_reward_rule_projection", "reward_account_id")
    op.drop_column("read_card_reward_rule_projection", "settlement_days")
    op.drop_column("read_card_reward_rule_projection", "settlement_type")
