"""scheduled_job_configs: 背景排程管理後台設定表

Revision ID: 0036_scheduled_job_configs
Revises: 0035_deferred_posting_and_reconciliation
Create Date: 2026-08-07

把 `main.py` 裡原本散落在 4 條各自獨立 asyncio 迴圈的 7 個排程動作收斂成一張
設定表,seed 7 筆預設值(interval 對齊改版前現況數字)。`mcp_log_retention`/
`recurring_materializer` 兩筆的 `next_run_at` seed 成「現在+間隔」,保留改版
前「冷啟動要等滿一個 interval 才跑第一次」的行為,避免部署當下就跑一次原本
要等 24 小時的清理/物化;其餘 5 筆 `next_run_at` 留空,視為立即到期,保留
改版前「啟動後很快跑第一次」的行為。
"""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op


revision = "0036_scheduled_job_configs"
down_revision = "0035_deferred_posting_and_reconciliation"
branch_labels = None
depends_on = None


_JOBS = [
    # (job_key, interval_seconds, cold_start_delay)
    ("mcp_log_retention", 24 * 3600, True),
    ("recurring_materializer", 24 * 3600, True),
    ("debt_reminders", 15 * 60, False),
    ("card_due_reminders", 15 * 60, False),
    ("transfer_rule_materialization", 15 * 60, False),
    ("card_autopay", 15 * 60, False),
    ("card_reward_payout", 5 * 60, False),
]


def upgrade() -> None:
    op.create_table(
        "scheduled_job_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_key", sa.String(64), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(16), nullable=True),
        sa.Column("last_run_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_scheduled_job_configs_job_key", "scheduled_job_configs", ["job_key"], unique=True
    )

    now = datetime.now(timezone.utc)
    table = sa.table(
        "scheduled_job_configs",
        sa.column("job_key", sa.String),
        sa.column("interval_seconds", sa.Integer),
        sa.column("enabled", sa.Boolean),
        sa.column("next_run_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        table,
        [
            {
                "job_key": job_key,
                "interval_seconds": interval_seconds,
                "enabled": True,
                "next_run_at": (now + timedelta(seconds=interval_seconds)) if cold_start_delay else None,
                "created_at": now,
                "updated_at": now,
            }
            for job_key, interval_seconds, cold_start_delay in _JOBS
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_job_configs_job_key", table_name="scheduled_job_configs")
    op.drop_table("scheduled_job_configs")
