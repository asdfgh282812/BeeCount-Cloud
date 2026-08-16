"""read_recurring_rule_projection: base_amount / fee_amount / fee_label /
discount_amount / discount_label / reward_rule_sync_ids_json

使用者回饋(2026-08):透過交易表單「設為週期性」開關建立的規則,第一筆
occurrence 正確帶有手續費/折扣與信用卡回饋勾選,但系統之後自動產生的第二筆
起卻遺失這兩塊資料——根因是 RecurringRule entity 原本刻意不儲存這六個欄位
(當初認為它們是「每一筆交易當下的獨立決定」,見 models.py 舊版 docstring),
所以三個產生 occurrence 交易的地方都沒有東西可以繼承。這裡補上欄位,改成
跟 merchant/project/tags 同一類「規則固定屬性」,搭配 write endpoint 與
materializer 一起補上轉發。

Revision ID: 0046_recurring_rule_fee_discount_reward
Revises: 0045_recurring_rule_merchant_project_tags
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_recurring_rule_fee_discount_reward"
down_revision = "0045_recurring_rule_merchant_project_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("base_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("fee_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("fee_label", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("discount_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("discount_label", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("reward_rule_sync_ids_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_recurring_rule_projection", "reward_rule_sync_ids_json")
    op.drop_column("read_recurring_rule_projection", "discount_label")
    op.drop_column("read_recurring_rule_projection", "discount_amount")
    op.drop_column("read_recurring_rule_projection", "fee_label")
    op.drop_column("read_recurring_rule_projection", "fee_amount")
    op.drop_column("read_recurring_rule_projection", "base_amount")
