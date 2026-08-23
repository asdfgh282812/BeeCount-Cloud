"""debt.excluded_from_total

App 端(BeeCount 主 repo)這次對標 Moze「排除在帳戶總覽計算」開關(§5.4 對象
管理),欠款也要有等效欄位——加一個 nullable=False、server_default false
的 additive column,不影響既有資料(既有欠款一律預設「計入總額」,語意跟
今天的行為一致)。只影響淨資產/總額統計,不影響清單或通知的可見性。

Revision ID: 0049_debt_excluded_from_total
Revises: 0048_notification_pinned
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0049_debt_excluded_from_total"
down_revision = "0048_notification_pinned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_debt_projection",
        sa.Column(
            "excluded_from_total",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("read_debt_projection", "excluded_from_total")
