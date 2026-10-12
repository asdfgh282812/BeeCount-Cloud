"""refresh token 輪替寬限(refresh_tokens.replaced_by_id / revoke_reason)

App 掛後台時 refresh 的回應可能在路上遺失(iOS 把 App 凍結在請求途中、背景任務
時間到被系統砍掉、網路中斷),server 已經輪替並撤銷舊 token,用戶端卻只拿得到
舊的 —— 下次 refresh 必定 401,使用者被迫重新登入。記錄輪替鏈,讓「後代從沒被
用戶端用過」的舊 token 還能再換一次(routers/auth.py::refresh)。

兩欄都 nullable、無 server_default;既有已撤銷的列沿用「硬撤銷」語意,行為不變。

Revision ID: 0071_refresh_rotation_grace
Revises: 0070_flexible_billing
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op


revision = "0071_refresh_rotation_grace"
down_revision = "0070_flexible_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.add_column(sa.Column("replaced_by_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("revoke_reason", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.drop_column("revoke_reason")
        batch_op.drop_column("replaced_by_id")
