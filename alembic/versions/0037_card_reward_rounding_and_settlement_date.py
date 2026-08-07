"""信用卡紅利回饋規則管理優化(Phase 8,§2.9.5 補強)

新增 `total_rounding`(總額取整方式,對齊 Moze 兩段式取整設計)、
`settlement_month_offset`/`settlement_day_of_month`(週期結束後一次結算的
回饋入帳日可設定)。詳見 `docs/PH6_USER_FEEDBACK_2026-08_SD.md` Phase 8。

Revision ID: 0037_card_reward_rounding_and_settlement_date
Revises: 0036_scheduled_job_configs
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_card_reward_rounding_and_settlement_date"
down_revision = "0036_scheduled_job_configs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("total_rounding", sa.String(8), nullable=False, server_default="round"),
    )
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("settlement_month_offset", sa.Integer(), nullable=True),
    )
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("settlement_day_of_month", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_card_reward_rule_projection", "settlement_day_of_month")
    op.drop_column("read_card_reward_rule_projection", "settlement_month_offset")
    op.drop_column("read_card_reward_rule_projection", "total_rounding")
