"""信用卡紅利回饋規則:基本回饋 is_basic + 回饋金歸屬專案 reward_project_id

對齊 Moze 紅利回饋(https://doc.moze.app/credit-card/rewards)新增的兩項規則設定
(docs/MOZE_FEATURE_GAP_SD.md §2.9.5):

- `is_basic`:基本回饋旗標,記帳時選到該帳戶就由前端自動帶入這條規則。
  既有規則升級後一律 false(server_default),行為不變。
- `reward_project_id`:自動入帳的回饋交易歸屬哪個專案;NULL = 逐筆結算沿用來源
  消費的專案、整期彙總結算不帶專案。

`interval` 新增的 `custom_range` 值不需要 schema 變更(String(16) 容得下,沿用
既有 starts_at/ends_at 欄位當活動起訖日)。

兩欄都是單純 add_column(Boolean 用 server_default=sa.false(),SQLite 渲染成 0、
Postgres 渲染成 false),不需要 batch_alter_table。

Revision ID: 0065_card_reward_basic_and_project
Revises: 0064_holidays
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0065_card_reward_basic_and_project"
down_revision = "0064_holidays"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("is_basic", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "read_card_reward_rule_projection",
        sa.Column("reward_project_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_card_reward_rule_projection", "reward_project_id")
    op.drop_column("read_card_reward_rule_projection", "is_basic")
