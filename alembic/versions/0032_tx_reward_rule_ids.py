"""read_tx_projection.reward_rule_sync_ids_json

§2.9.5 信用卡紅利回饋改版(2026-08-06):回饋規則從「系統依 category/金額
自動比對」改成「使用者記交易當下手動勾選要走哪幾條規則」。新增 nullable
JSON array 欄位存使用者勾選的 `read_card_reward_rule_projection.sync_id`
列表,跟 `tag_sync_ids_json` 同一個模式(user-global 實體的 id 列表掛在
交易行上)。

Revision ID: 0032_tx_reward_rule_ids
Revises: 0031_card_reward_rules
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_tx_reward_rule_ids"
down_revision = "0031_card_reward_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("reward_rule_sync_ids_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "reward_rule_sync_ids_json")
