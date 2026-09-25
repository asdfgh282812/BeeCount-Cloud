"""system_broadcasts + notifications.broadcast_id

管理者系統公告:後台對所有啟用中的使用者各寫一筆 `category='system'` 通知,
`broadcast_id` 指回公告紀錄,撤回時用它一次刪掉。全新功能,不需要 backfill
(既有通知的 broadcast_id 一律是 NULL)。

Revision ID: 0057_system_broadcasts
Revises: 0056_license_keys
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_system_broadcasts"
down_revision = "0056_license_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_broadcasts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_system_broadcasts_created_at", "system_broadcasts", ["created_at"]
    )
    with op.batch_alter_table("notifications") as batch:
        batch.add_column(sa.Column("broadcast_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_notifications_broadcast_id",
            "system_broadcasts",
            ["broadcast_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_notifications_broadcast_id", ["broadcast_id"])


def downgrade() -> None:
    with op.batch_alter_table("notifications") as batch:
        batch.drop_index("ix_notifications_broadcast_id")
        batch.drop_constraint("fk_notifications_broadcast_id", type_="foreignkey")
        batch.drop_column("broadcast_id")
    op.drop_index("ix_system_broadcasts_created_at", table_name="system_broadcasts")
    op.drop_table("system_broadcasts")
