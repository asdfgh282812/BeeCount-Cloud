"""交易範本 v2:欄位對齊 App 記帳表單 + 使用次數記憶(read_tx_template_projection)

對齊 MOZE `doc.moze.app/record/template` 與 App 範本功能:
- 補欄位:merchant / project_sync_id / currency_code / to_amount / fee_amount / fee_label /
  discount_amount / discount_label / exclude_from_stats / exclude_from_budget /
  reward_rule_sync_ids_json;
- 記憶:usage_count(套用次數)+ last_used_at(最近套用時間),清單依此排「常用」;
- amount 語意放寬:0 = 套用時再填金額(舊資料不受影響,都 > 0)。

新欄位全部 nullable 或有 server_default:既有範本升級後行為跟升級前完全一樣。

Revision ID: 0072_tx_template_v2
Revises: 0071_refresh_rotation_grace
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op


revision = "0072_tx_template_v2"
down_revision = "0071_refresh_rotation_grace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("read_tx_template_projection") as batch_op:
        batch_op.add_column(sa.Column("merchant", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("project_sync_id", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("currency_code", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("to_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fee_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("fee_label", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("discount_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("discount_label", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("exclude_from_stats", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("exclude_from_budget", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("reward_rule_sync_ids_json", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("read_tx_template_projection") as batch_op:
        for col in (
            "last_used_at", "usage_count", "reward_rule_sync_ids_json",
            "exclude_from_budget", "exclude_from_stats", "discount_label",
            "discount_amount", "fee_label", "fee_amount", "to_amount",
            "currency_code", "project_sync_id", "merchant",
        ):
            batch_op.drop_column(col)
