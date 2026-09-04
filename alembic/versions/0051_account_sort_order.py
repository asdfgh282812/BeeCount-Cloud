"""user_account_projection: sort_order — 帳戶清單拖曳排序

帳戶清單「編輯排序」拖曳功能(App 端 docs/changes/2026-09-05-account-drag
-reorder.md):App 端 `Accounts.sortOrder` 一直存在,但 server 端沒有對應欄位,
read API 一直是強制按名稱字母排序,跟 App/Web 拖曳完的實際順序對不上。這裡比照
`user_category_projection.sort_order`(0010_user_global_projections)補上同名
欄位,nullable(舊資料/舊版 App 沒有這個值時留 null,read 端排序時 fallback 到
名稱)。

Revision ID: 0051_account_sort_order
Revises: 0050_refresh_token_retention
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_account_sort_order"
down_revision = "0050_refresh_token_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("sort_order", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "sort_order")
