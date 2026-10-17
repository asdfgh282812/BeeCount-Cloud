"""信用卡回饋改成「即時連動的專屬回饋交易」

Revision ID: 0076_card_reward_live
Revises: 0075_game_inventory_location

設計見 App repo `docs/changes/2026-10-16-live-card-reward-transactions.md`、
本 repo `src/services/card_reward_live.py`。

1. `card_reward_payouts` 加即時欄位(live / ledger_id / period_* / proposed_* /
   dismissed / notified_at)。既有列全部是舊排程寫的,`live` 預設 false。
2. `read_tx_projection` 加 `reward_payout_rule_sync_id` / `reward_payout_key`
   (即時回饋交易連回規則 + 期別,給 App/Web 顯示)。
3. 新表 `card_reward_legacy_links`,並把升級當下所有「消費 × 規則」勾選配對
   寫進去——這就是「部署前已套用」的完整快照:這些配對繼續由舊排程入帳,之後
   才勾的配對走即時機制(新舊以交易為切點)。
4. 排程設定補一列 `card_reward_live_reconcile`(每小時保險重算 + 入帳通知)。
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0076_card_reward_live"
down_revision = "0075_game_inventory_location"
branch_labels = None
depends_on = None

_JOB_KEY = "card_reward_live_reconcile"
_INTERVAL_SECONDS = 3600


def upgrade() -> None:
    with op.batch_alter_table("card_reward_payouts") as batch:
        batch.add_column(sa.Column("live", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("ledger_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("period_start", sa.Date(), nullable=True))
        batch.add_column(sa.Column("period_end", sa.Date(), nullable=True))
        batch.add_column(sa.Column("proposed_happened_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("proposed_account_sync_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("proposed_project_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("read_tx_projection") as batch:
        batch.add_column(sa.Column("reward_payout_rule_sync_id", sa.String(255), nullable=True))
        batch.add_column(sa.Column("reward_payout_key", sa.String(255), nullable=True))

    op.create_table(
        "card_reward_legacy_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tx_sync_id", sa.String(255), nullable=False),
        sa.Column("rule_sync_id", sa.String(255), nullable=False),
    )
    op.create_index(
        "ux_card_reward_legacy_links", "card_reward_legacy_links",
        ["user_id", "tx_sync_id", "rule_sync_id"], unique=True,
    )
    op.create_index(
        "ix_card_reward_legacy_links_rule", "card_reward_legacy_links", ["user_id", "rule_sync_id"],
    )

    # 部署前已套用的配對快照。
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT user_id, sync_id, reward_rule_sync_ids_json FROM read_tx_projection "
            "WHERE reward_rule_sync_ids_json IS NOT NULL"
        )
    ).fetchall()
    seen: set[tuple[str, str, str]] = set()
    links: list[dict[str, str]] = []
    for user_id, tx_sync_id, raw in rows:
        try:
            rule_ids = json.loads(raw or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(rule_ids, list):
            continue
        for rule_id in rule_ids:
            key = (str(user_id), str(tx_sync_id), str(rule_id))
            if key in seen:
                continue
            seen.add(key)
            links.append({"user_id": key[0], "tx_sync_id": key[1], "rule_sync_id": key[2]})
    if links:
        link_table = sa.table(
            "card_reward_legacy_links",
            sa.column("user_id", sa.String),
            sa.column("tx_sync_id", sa.String),
            sa.column("rule_sync_id", sa.String),
        )
        for start in range(0, len(links), 1000):
            op.bulk_insert(link_table, links[start:start + 1000])

    now = datetime.now(timezone.utc)
    job_table = sa.table(
        "scheduled_job_configs",
        sa.column("job_key", sa.String),
        sa.column("interval_seconds", sa.Integer),
        sa.column("enabled", sa.Boolean),
        sa.column("next_run_at", sa.DateTime),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        job_table,
        [{
            "job_key": _JOB_KEY,
            "interval_seconds": _INTERVAL_SECONDS,
            "enabled": True,
            "next_run_at": None,
            "created_at": now,
            "updated_at": now,
        }],
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM scheduled_job_configs WHERE job_key = :k").bindparams(k=_JOB_KEY))
    op.drop_index("ix_card_reward_legacy_links_rule", table_name="card_reward_legacy_links")
    op.drop_index("ux_card_reward_legacy_links", table_name="card_reward_legacy_links")
    op.drop_table("card_reward_legacy_links")
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.drop_column("reward_payout_key")
        batch.drop_column("reward_payout_rule_sync_id")
    with op.batch_alter_table("card_reward_payouts") as batch:
        for col in (
            "notified_at", "dismissed", "proposed_project_id", "proposed_account_sync_id",
            "proposed_happened_at", "period_end", "period_start", "ledger_id", "live",
        ):
            batch.drop_column(col)
