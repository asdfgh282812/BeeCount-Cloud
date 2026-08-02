"""user_account_projection.avatar_cloud_file_id / avatar_cloud_sha256

§2.9 補強(2026-08-02)。使用者反饋光靠 bank_name 文字看不出是哪張卡,加一張
自訂圖片頭像。走跟 category icon 一樣的共用 attachment 池
(`attachment_files`,新 `attachment_kind="account_avatar"` 值,該欄位是
自由文本,不需要 migration),`avatar_cloud_file_id` 是唯一權威值,沒有
mobile 端"本地路徑"這種舊制概念要相容,所以只加這一個欄位 + sha256(dedup
用),不像 category 有 icon_type/custom_icon_path 那麼多歷史包袱。

Revision ID: 0029_account_avatar
Revises: 0028_account_auto_pay
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_account_avatar"
down_revision = "0028_account_auto_pay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("avatar_cloud_file_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "user_account_projection",
        sa.Column("avatar_cloud_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "avatar_cloud_sha256")
    op.drop_column("user_account_projection", "avatar_cloud_file_id")
