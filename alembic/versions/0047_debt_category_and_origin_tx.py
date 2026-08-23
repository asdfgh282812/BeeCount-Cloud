"""read_debt_projection: category_sync_id / origin_tx_sync_id

App 端反馈:借還款目前不能选分类(§2.5 原始设计刻意留空),使用者要求补上;
同一批顺手补「起点交易反查」——欠款建立时 App 会同时写一笔起点交易让帳戶
餘額立即變動,但那笔交易不带 debt_sync_id(避免被 list_debts 的还款汇总
误计入),之前完全没有办法从欠款反查回那笔起点交易。这里补一个欠款側的
反查栏位 origin_tx_sync_id,只在建立时写入、之后不变——不放在
read_tx_projection 上是因为欠款还没建立时那笔交易的 syncId 已知,反过来
（交易先写入 debt_sync_id 指向还没建立的债务)才需要额外一次 UPDATE 或调
整既有的「先建交易、后建债务」顺序,详见 App 端 change doc。

两个栏位都是 nullable 的 additive add-column,不影响既有资料。

Revision ID: 0047_debt_category_and_origin_tx
Revises: 0046_recurring_rule_fee_discount_reward
Create Date: 2026-08-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_debt_category_and_origin_tx"
down_revision = "0046_recurring_rule_fee_discount_reward"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_debt_projection",
        sa.Column("category_sync_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "read_debt_projection",
        sa.Column("origin_tx_sync_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_debt_projection", "origin_tx_sync_id")
    op.drop_column("read_debt_projection", "category_sync_id")
