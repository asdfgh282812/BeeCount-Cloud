"""user_category_projection: color — 分類專屬顏色

App 端(docs/changes/2026-09-05-category-colors-and-add-tile.md)為一級分類加上
自動配色,並在 sync payload 帶上 `color`(十六進位字串如 `#FF9800`)。Server 端
比照 `user_tag_projection.color`(0010_user_global_projections)補上同名欄位,
nullable(舊資料/未帶顏色的分類留 null,read 端 fallback 回原本灰色渲染)。

Revision ID: 0053_category_color
Revises: 0052_notification_priority
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_category_color"
down_revision = "0052_notification_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_category_projection",
        sa.Column("color", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_category_projection", "color")
