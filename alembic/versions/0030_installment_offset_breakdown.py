"""read_installment_plan_projection.offset_breakdown_json

§2.3 補強(2026-08-02 第三輪)。使用者反饋:①帳單分期沖銷不該產生一筆
真實交易(會出現在交易明細裡);②刪除分期計畫時沖銷也要一起復原,回到
「尚未繳費」狀態。把沖銷改成純虛擬記帳調整,不落地為 `read_tx_projection`
交易 —— 存 `{child_account_sync_id: amount}` 的 JSON(對齊主帳戶分攤到各
子帳戶的既有模式),讀路徑(`services.credit_card_billing`)計算應繳金額
時直接扣掉這個值,刪除計畫這一行就自動失效,不需要另外清理任何交易。

Revision ID: 0030_installment_offset_breakdown
Revises: 0029_account_avatar
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_installment_offset_breakdown"
down_revision = "0029_account_avatar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_installment_plan_projection",
        sa.Column("offset_breakdown_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_installment_plan_projection", "offset_breakdown_json")
