"""refresh_tokens: 清理排程 + 支撐索引

`refresh_tokens` 每次 `/auth/refresh` 都會 rotate(舊列標 revoked_at、新增一
列),但一直沒有任何清理機制,線上已經累積到 30 萬+行(全是已撤銷/已過期、
但從未被刪除的歷史列)。這裡:

1. 給 `expires_at`/`revoked_at` 補 index —— 新增的 `refresh_token_retention`
   排程 job 每天會用這兩個欄位掃「失效超過 2 天」的舊列來刪,百萬行規模下
   沒 index 會全表掃描。
2. 比照 `0036_scheduled_job_configs.py` 的 seed 慣例,補一筆
   `refresh_token_retention` 設定列(24h 跑一次)。`next_run_at` 留空視為立即
   到期 —— 跟 `mcp_log_retention` 的 24h cold-start 延遲不同,這裡是刻意的:
   線上已經堆了 30 萬+行歷史資料,希望這次升級部署後、下一次 60 秒排程輪詢
   就立刻清一輪,不要再多等 24 小時。之後每次都是 24h 一次的例行清理。

Revision ID: 0050_refresh_token_retention
Revises: 0049_debt_excluded_from_total
Create Date: 2026-09-03
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0050_refresh_token_retention"
down_revision = "0049_debt_excluded_from_total"
branch_labels = None
depends_on = None

_JOB_KEY = "refresh_token_retention"
_INTERVAL_SECONDS = 24 * 3600


def upgrade() -> None:
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    op.create_index("ix_refresh_tokens_revoked_at", "refresh_tokens", ["revoked_at"])

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
                "job_key": _JOB_KEY,
                "interval_seconds": _INTERVAL_SECONDS,
                "enabled": True,
                "next_run_at": None,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM scheduled_job_configs WHERE job_key = '{_JOB_KEY}'")
    op.drop_index("ix_refresh_tokens_revoked_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
