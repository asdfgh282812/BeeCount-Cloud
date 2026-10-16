"""農場板條箱獨立於倉庫：庫存批次記錄所在位置

Revision ID: 0075_game_inventory_location
Revises: 0074_game_farms

既有批次全部歸「倉庫」；原本借用板條箱共用容量而超出倉庫上限的部分，
由 engine.advance 第一次讀取時搬進板條箱（需要解析 layout，不在 migration 裡做）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_game_inventory_location"
down_revision = "0074_game_farms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("game_inventory") as batch:
        batch.add_column(
            sa.Column("location", sa.String(16), nullable=False, server_default="warehouse")
        )


def downgrade() -> None:
    with op.batch_alter_table("game_inventory") as batch:
        batch.drop_column("location")
