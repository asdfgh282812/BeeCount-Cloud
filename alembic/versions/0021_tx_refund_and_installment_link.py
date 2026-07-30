"""read_tx_projection: refund_of_sync_id + installment_plan_sync_id

§2.6 退款 / §2.3 分期付款(MOZE_FEATURE_GAP_SD.md Phase 1)的反查字段,两个都
是 nullable String 列,轻量迁移,不需要 backfill(None = 普通交易)。

Revision ID: 0021_tx_refund_and_installment_link
Revises: 0020_notifications
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op


revision = "0021_tx_refund_and_installment_link"
down_revision = "0020_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("refund_of_sync_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("installment_plan_sync_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "installment_plan_sync_id")
    op.drop_column("read_tx_projection", "refund_of_sync_id")
