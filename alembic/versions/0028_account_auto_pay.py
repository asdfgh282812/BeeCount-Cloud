"""user_account_projection.auto_pay_enabled / auto_pay_from_account_id

§2.9 信用卡管理(Phase 4)自動扣繳改版(2026-08-04)。使用者反馈自動扣繳
不該是一條完整的週期性收支規則,而是掛在信用卡/主帳戶自己身上的一個開關
+ 一個來源帳戶選擇:開啟後,到了繳款截止日,系統自動從指定帳戶轉帳繳清
應繳金額(帳戶有錢的前提下)。`auto_pay_from_account_id` 是同一個 user
底下另一個帳戶的 sync_id 自我參照,跟 `parent_account_id` 同一個模式。

Revision ID: 0028_account_auto_pay
Revises: 0027_account_parent_id
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_account_auto_pay"
down_revision = "0027_account_parent_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_account_projection",
        sa.Column(
            "auto_pay_enabled", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
    )
    op.add_column(
        "user_account_projection",
        sa.Column("auto_pay_from_account_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_account_projection", "auto_pay_from_account_id")
    op.drop_column("user_account_projection", "auto_pay_enabled")
