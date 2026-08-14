"""read_recurring_rule_projection: merchant / project_sync_id / tag_sync_ids_json

Phase 24(docs/PH17_USER_FEEDBACK_2026-08_SD.md 問題 B 第二層)：「連同未來
週期」批次更新希望也能涵蓋商家/專案/標籤這三個「重複性交易固定屬性」概念,
但 RecurringRule entity 原本完全沒有這三個欄位。這裡新增,搭配
`update-from` 端點與 create/update 端點一起補上轉發。

Revision ID: 0045_recurring_rule_merchant_project_tags
Revises: 0044_tx_transfer_to_amount
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0045_recurring_rule_merchant_project_tags"
down_revision = "0044_tx_transfer_to_amount"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("merchant", sa.Text(), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("project_sync_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "read_recurring_rule_projection",
        sa.Column("tag_sync_ids_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_recurring_rule_projection", "tag_sync_ids_json")
    op.drop_column("read_recurring_rule_projection", "project_sync_id")
    op.drop_column("read_recurring_rule_projection", "merchant")
