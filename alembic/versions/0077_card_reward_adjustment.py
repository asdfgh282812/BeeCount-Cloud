"""即時回饋交易的手動調整金額

Revision ID: 0077_card_reward_adjustment
Revises: 0076_card_reward_live

使用者改即時回饋交易的金額時,差額存成 `read_tx_projection.reward_adjustment_amount`
(正 = 多拿、負 = 扣掉),交易金額 = 系統計算 + 調整;系統重算只動計算的部分。
見 App repo `docs/changes/2026-10-16-live-card-reward-transactions.md`「手動調整」一節。
"""

import sqlalchemy as sa
from alembic import op

revision = "0077_card_reward_adjustment"
down_revision = "0076_card_reward_live"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.add_column(sa.Column("reward_adjustment_amount", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.drop_column("reward_adjustment_amount")
