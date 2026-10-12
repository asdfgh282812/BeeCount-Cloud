"""彈性結帳日 + 繳款期限模式(user_account_projection 四個新欄位)

對齊 MOZE `doc.moze.app/prepare/account/settings#flexible-closing-date` 與 App v71:
- billing_holiday_rule:結帳日遇非工作日 `postpone`(延後)/`advance`(提前),NULL = 不調整;
- billing_holiday_region:假日地區(TW/CN/HK/JP/KR/US),NULL = 只看週末;
- payment_due_mode:`fixed_day`(每月固定日,NULL 同義)/`days_after_closing`(結帳日後第 N 天,
  N 存在既有的 payment_due_day);
- billing_cycle_overrides_json:單期結帳日覆寫,JSON 物件 `{"yyyy-MM": "yyyy-MM-dd"}`,key 是該期
  原定結帳日所在年月。整包放帳戶列,不另開 entity。

全部 nullable、無 server_default:既有帳戶升級後行為跟升級前完全一樣。

Revision ID: 0070_flexible_billing
Revises: 0069_deposit_balance
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op


revision = "0070_flexible_billing"
down_revision = "0069_deposit_balance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_account_projection") as batch_op:
        batch_op.add_column(sa.Column("billing_holiday_rule", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("billing_holiday_region", sa.String(8), nullable=True))
        batch_op.add_column(sa.Column("payment_due_mode", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("billing_cycle_overrides_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_account_projection") as batch_op:
        batch_op.drop_column("billing_cycle_overrides_json")
        batch_op.drop_column("payment_due_mode")
        batch_op.drop_column("billing_holiday_region")
        batch_op.drop_column("billing_holiday_rule")
