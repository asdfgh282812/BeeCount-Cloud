"""users.sso_subject

SSO(OIDC)登入 —— 生產環境改成強制走 OIDC 身分提供者登入，這個欄位存放
IdP 回傳的 `sub` claim，用來把 SSO 身分跟本地 users row 對起來（既有密碼
帳號第一次用 SSO 登入、email 對得上時，會自動 link 到這個欄位，不建重複
帳號）。

Revision ID: 0042_sso
Revises: 0041_swipesmart
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_sso"
down_revision = "0041_swipesmart"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sso_subject", sa.String(255), nullable=True))
    op.create_index("ix_users_sso_subject", "users", ["sso_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_sso_subject", table_name="users")
    op.drop_column("users", "sso_subject")
