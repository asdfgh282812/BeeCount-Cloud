"""security_quotes.open_price + market_closure_entries

股票定期定額改用「成交日當天的開盤價」(docs/STOCK_HOLDINGS_SD.md §10.2b):
- `security_quotes.open_price`:該報價所屬交易日的開盤價(沒有 = 還沒開盤/來源沒給)。
- `market_closure_entries`:各交易所「平日」休市日,跟節日資料集同一個版本號
  (`holiday_dataset_meta`)一起發給 App,App 不必為了休市表發版。

Revision ID: 0068_quote_open_price_market_closures
Revises: 0067_debt_kind_installment
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0068_quote_open_price_market_closures"
down_revision = "0067_debt_kind_installment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("security_quotes") as batch:
        batch.add_column(sa.Column("open_price", sa.Float(), nullable=True))
    op.create_table(
        "market_closure_entries",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("year", "exchange", "date"),
    )


def downgrade() -> None:
    op.drop_table("market_closure_entries")
    with op.batch_alter_table("security_quotes") as batch:
        batch.drop_column("open_price")
