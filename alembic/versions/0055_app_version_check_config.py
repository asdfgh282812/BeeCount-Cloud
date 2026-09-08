"""app_version_check_config: App 端新版本提醒

docs/superpowers/specs/2026-09-08-app-update-reminder-design.md §1:新增單例
設定表(固定 id=1),存「目前最新版本號」+ NAS WebDAV 偵測連線設定 + 上次
偵測時間/錯誤訊息。只建表,不需要 backfill(全新功能,沒有舊資料)。

Revision ID: 0055_app_version_check_config
Revises: 0054_project_category_budgets
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0055_app_version_check_config"
down_revision = "0054_project_category_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_version_check_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("latest_version", sa.String(32), nullable=True),
        sa.Column("nas_webdav_url", sa.String(500), nullable=True),
        sa.Column("nas_webdav_user", sa.String(200), nullable=True),
        sa.Column("nas_webdav_password", sa.String(500), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_error", sa.String(1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_version_check_config")
