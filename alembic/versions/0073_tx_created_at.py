"""交易真實建立／伺服器首次接收時間（農場 M3-2 契約）

Revision ID: 0073_tx_created_at
Revises: 0072_tx_template_v2
"""

import sqlalchemy as sa
from alembic import op

revision = "0073_tx_created_at"
down_revision = "0072_tx_template_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.add_column(sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.drop_column("first_seen_at")
        batch.drop_column("client_created_at")
