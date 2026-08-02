"""user_account_projection.parent_account_id

§2.9 信用卡管理整組功能(Phase 4,MOZE_FEATURE_GAP_SD.md)。主帳戶(合併帳單):
附卡/子卡透過 `parent_account_id` 自我參照掛在主卡下，讀路徑
(`GET /ledgers/{id}/accounts/{account_id}/billing-summary`)合併計算帳單。
`account` 是 user-global entity(PK 是 `(user_id, sync_id)`)，
`parent_account_id` 因此也只在同一個 user 底下的帳戶之間互相參照。

Revision ID: 0027_account_parent_id
Revises: 0026_debt_closed_at
Create Date: 2026-08-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_account_parent_id"
down_revision = "0026_debt_closed_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column("parent_account_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_user_account_parent",
        "user_account_projection",
        ["user_id", "parent_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_account_parent", table_name="user_account_projection")
    op.drop_column("user_account_projection", "parent_account_id")
