"""user_account_projection.swipesmart_card_id + user_profiles.swipesmart_api_key_encrypted

Phase 14(docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md)SwipeSmart2 信用卡刷卡
建議整合:帳戶 <-> SwipeSmart 卡片對照欄位(走一般 sync entity 欄位流程),
以及使用者的 SwipeSmart Personal API Key(加密儲存,不走 sync)。

Revision ID: 0041_swipesmart
Revises: 0040_projects
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_swipesmart"
down_revision = "0040_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("swipesmart_card_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("swipesmart_api_key_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "swipesmart_api_key_encrypted")
    op.drop_column("user_account_projection", "swipesmart_card_id")
