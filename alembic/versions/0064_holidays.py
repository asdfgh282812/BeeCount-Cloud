"""holidays: holiday_entries + holiday_dataset_meta

節日資料(docs/HOLIDAYS_SD.md)。全域市場資料,不分 user、不進 sync;資料由
`holiday_dataset_refresh` 排程產生,這裡只建表、不 seed。

Revision ID: 0064_holidays
Revises: 0063_notification_settings
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0064_holidays"
down_revision = "0063_notification_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holiday_entries",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name_zh_tw", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=False),
        sa.Column("name_local", sa.String(length=128), nullable=False),
        sa.Column("emoji", sa.String(length=16), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("year", "country", "date", "key"),
    )
    op.create_table(
        "holiday_dataset_meta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("year_hashes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("holiday_dataset_meta")
    op.drop_table("holiday_entries")
