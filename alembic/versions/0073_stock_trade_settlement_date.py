"""股票交易明細新增交割日(settlement_date)

買進/賣出的款券交割通常是 T+2:成交當天股數就變動,但帳戶的錢要等交割日才真的
扣/入。`settlement_date` 就是綁定轉帳交易的 `happened_at`(錢實際移動的瞬間);
NULL = 沒有交割延遲(舊資料、定期定額、期初持股/配股/分割/股利),行為跟升級前
完全一樣——轉帳日期 = 成交日。

不需要回填:舊資料維持 NULL。

Revision ID: 0073_stock_trade_settlement_date
Revises: 0072_tx_template_v2
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op


revision = "0073_stock_trade_settlement_date"
down_revision = "0072_tx_template_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("read_stock_trade_projection") as batch_op:
        batch_op.add_column(sa.Column("settlement_date", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("read_stock_trade_projection") as batch_op:
        batch_op.drop_column("settlement_date")
