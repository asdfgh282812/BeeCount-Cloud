"""project 附加設定欄位 + read_project_category_budget_projection

docs/2026-09-06-project-category-budget-period-switch-design.md §7.1/§7.2:
1) `read_project_projection` 新增 4 個附加設定欄位(收入併入預算/每日預算/
   預算提醒門檻),對應 App 端已定義的 wire key。
2) 新增 `read_project_category_budget_projection`(全新 ledger-scoped entity,
   專案下對一級分類的固定金額/比例子預算分配)。按 CLAUDE.md「新增 entity」
   checklist 第 1 步,合併成一支 migration,比照 0040_projects 當時「新表 +
   對既有表的欄位異動」合併的做法。

Revision ID: 0054_project_category_budgets
Revises: 0053_category_color
Create Date: 2026-09-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0054_project_category_budgets"
down_revision = "0053_category_color"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_project_projection",
        sa.Column("income_included_in_budget", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "read_project_projection",
        sa.Column("daily_budget_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "read_project_projection",
        sa.Column("daily_budget_mode", sa.String(16), nullable=True),
    )
    op.add_column(
        "read_project_projection",
        sa.Column("reminder_threshold_percent", sa.Integer(), nullable=True),
    )

    op.create_table(
        "read_project_category_budget_projection",
        sa.Column(
            "ledger_id", sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column("sync_id", sa.String(255), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("project_sync_id", sa.String(255), nullable=False),
        sa.Column("category_sync_id", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="fixed"),
        sa.Column("fixed_amount", sa.Float(), nullable=True),
        sa.Column("percentage", sa.Float(), nullable=True),
        sa.Column("carryover_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        # 唯一約束併入 create_table(而非事後 op.create_unique_constraint)——
        # SQLite 的 ALTER 不支援事後加 constraint(需要 batch mode 的
        # copy-and-move,徒增複雜度),併入建表語句在 SQLite/PostgreSQL 都通用。
        sa.UniqueConstraint(
            "ledger_id", "project_sync_id", "category_sync_id",
            name="uq_read_pcb_project_category",
        ),
    )
    op.create_index(
        "ix_read_pcb_user_id", "read_project_category_budget_projection", ["user_id"],
    )
    op.create_index(
        "ix_read_pcb_ledger_project", "read_project_category_budget_projection",
        ["ledger_id", "project_sync_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_read_pcb_ledger_project", table_name="read_project_category_budget_projection")
    op.drop_index("ix_read_pcb_user_id", table_name="read_project_category_budget_projection")
    op.drop_table("read_project_category_budget_projection")

    op.drop_column("read_project_projection", "reminder_threshold_percent")
    op.drop_column("read_project_projection", "daily_budget_mode")
    op.drop_column("read_project_projection", "daily_budget_enabled")
    op.drop_column("read_project_projection", "income_included_in_budget")
