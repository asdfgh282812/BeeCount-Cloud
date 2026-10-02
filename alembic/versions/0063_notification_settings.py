"""notification_settings: user_profiles.notification_settings_json

通知設定(記帳提醒 / 信用卡提醒)綁帳號,跨裝置同步。新欄位 nullable,不需要 backfill。

Revision ID: 0063_notification_settings
Revises: 0062_dashboard_layout
Create Date: 2026-10-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0063_notification_settings"
down_revision = "0062_dashboard_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_profiles") as batch:
        batch.add_column(sa.Column("notification_settings_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_profiles") as batch:
        batch.drop_column("notification_settings_json")
