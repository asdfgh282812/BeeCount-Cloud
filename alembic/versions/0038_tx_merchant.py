"""read_tx_projection.merchant

需求 #11(2026-08 使用者回饋改善 SD Phase 11):新增交易「商店」欄位(選填),
純展示用途,不參與任何統計/校驗。跟既有 `note` 欄位同款寫法(nullable Text)。

Revision ID: 0038_tx_merchant
Revises: 0037_card_reward_rounding_and_settlement_date
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op


revision = "0038_tx_merchant"
down_revision = "0037_card_reward_rounding_and_settlement_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("merchant", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "merchant")
