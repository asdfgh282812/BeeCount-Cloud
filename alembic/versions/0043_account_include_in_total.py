"""user_account_projection: include_in_total — 帳戶納入總餘額開關

Phase 18(docs/PH17_USER_FEEDBACK_2026-08_SD.md §Phase 18)對齊 Moze「納入
總餘額」開關:關閉後帳戶餘額不列入淨資產/資產構成總額,但帳戶本身、個別餘額
顯示、底部分組列表均不受影響(跟既有 `hidden` 是兩個獨立維度)。

既有行升級後 include_in_total=true(server_default),維持現況「全部計入」
的行為不變;舊 App 不發該欄位時保持預設。

Revision ID: 0043_account_include_in_total
Revises: 0042_sso
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_account_include_in_total"
down_revision = "0042_sso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column(
            "include_in_total", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "include_in_total")
