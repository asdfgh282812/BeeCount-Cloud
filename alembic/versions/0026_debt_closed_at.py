"""read_debt_projection.closed_at

§2.5 借還款追蹤體驗補強:新增「結案」欄位(手動標記結束,不一定要還清
全額)。`closed_at` 非空 = 已結案,`list_debts` 讀路徑優先用它決定 status,
蓋過 remaining_amount 算出來的 open/partial/settled。None = 尚未結案。

Revision ID: 0026_debt_closed_at
Revises: 0025_debts_and_tx_templates
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_debt_closed_at"
down_revision = "0025_debts_and_tx_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_debt_projection",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_debt_projection", "closed_at")
