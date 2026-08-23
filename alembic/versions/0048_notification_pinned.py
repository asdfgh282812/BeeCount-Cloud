"""notifications.pinned

使用者反饋:欠款到期提醒的通知會被之後新建立的其他通知洗到列表下方,
看不到、也就不會去處理還款。加一個 pinned 欄位讓特定通知(目前只有
debt_reminders 建立時傳 True)排在最上面,不受新通知擠動——見
`services/debt_reminders.py::send_due_debt_reminders` 呼叫
`create_notification(..., pinned=True)`。

Revision ID: 0048_notification_pinned
Revises: 0047_debt_category_and_origin_tx
Create Date: 2026-08-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0048_notification_pinned"
down_revision = "0047_debt_category_and_origin_tx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("notifications", "pinned")
