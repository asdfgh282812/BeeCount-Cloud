"""license_keys + app_version_check_config.min_sync_version

docs/LICENSE_KEYS.md:新增授權金鑰表(管理者產生、使用者啟用後綁定帳號,
預設效期一年),以及「最低可同步 App 版本」欄位。兩者都是全新功能,不需要
backfill —— 注意:上線後所有非管理員帳號都必須先輸入金鑰才能使用,部署前
要先在後台產生好金鑰發給使用者。

Revision ID: 0056_license_keys
Revises: 0055_app_version_check_config
Create Date: 2026-09-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0056_license_keys"
down_revision = "0055_app_version_check_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "license_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False, server_default="365"),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "redeemed_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_license_keys_key", "license_keys", ["key"], unique=True)
    op.create_index(
        "ix_license_keys_redeemed_by_user_id", "license_keys", ["redeemed_by_user_id"]
    )
    with op.batch_alter_table("app_version_check_config") as batch:
        batch.add_column(sa.Column("min_sync_version", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("app_version_check_config") as batch:
        batch.drop_column("min_sync_version")
    op.drop_index("ix_license_keys_redeemed_by_user_id", table_name="license_keys")
    op.drop_index("ix_license_keys_key", table_name="license_keys")
    op.drop_table("license_keys")
