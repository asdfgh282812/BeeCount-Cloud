"""read_tx_projection.reward_source_tx_sync_id

§2.9.5.4 補強(2026-08-04 使用者反饋):逐筆結算(immediate_after_tx/
after_posting_date)產生的信用卡回饋 income 交易,原本只在備註文字裡嵌入
原交易的 sync_id(對使用者不友善,也不能點擊跳轉)。新增 nullable 反查
欄位,跟 `refund_of_sync_id`/`installment_plan_sync_id` 同一個模式,讓 web
UI 可以在交易詳情弹窗渲染一個可點擊的「關聯消費」連結,跳去看原始那筆
消費的完整明細。

Revision ID: 0034_tx_reward_source
Revises: 0033_card_reward_settlement
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_tx_reward_source"
down_revision = "0033_card_reward_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("reward_source_tx_sync_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "reward_source_tx_sync_id")
