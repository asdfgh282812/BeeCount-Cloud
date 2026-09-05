"""notifications.priority — 取代 pinned,支援分級優先度排序

使用者反饋:通知列表要先分優先度再排時間——欠款(debt_reminders/
debt_unsettled_notifications)優先度最高、信用卡帳單(credit_card_reminders)
次之、其餘維持預設。原本的 `pinned` 布林欄位只能表達「置頂/不置頂」二元
狀態,不夠表達多級優先度,改成整數 `priority`(數字越大排序越前面),既有
`pinned=True` 的資料換算成 `priority=2`(目前只有上述兩種欠款通知會傳
True,參見舊 migration 0048_notification_pinned 的說明)。

Revision ID: 0052_notification_priority
Revises: 0051_account_sort_order
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_notification_priority"
down_revision = "0051_account_sort_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    notifications = sa.table(
        "notifications",
        sa.column("pinned", sa.Boolean()),
        sa.column("priority", sa.Integer()),
    )
    op.execute(
        notifications.update().where(notifications.c.pinned.is_(True)).values(priority=2)
    )
    op.drop_column("notifications", "pinned")


def downgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    notifications = sa.table(
        "notifications",
        sa.column("pinned", sa.Boolean()),
        sa.column("priority", sa.Integer()),
    )
    op.execute(
        notifications.update().where(notifications.c.priority >= 2).values(pinned=True)
    )
    op.drop_column("notifications", "priority")
