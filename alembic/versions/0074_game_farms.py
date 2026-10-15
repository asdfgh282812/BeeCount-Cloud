"""Server-authoritative farm, atomic receipts and privacy-safe weekly boards.

Revision ID: 0074_game_farms
Revises: 0073_tx_created_at
"""

import sqlalchemy as sa
from alembic import op

revision = "0074_game_farms"
down_revision = "0073_tx_created_at"
branch_labels = None
depends_on = None


def _user(primary=False):
    return sa.Column(
        "user_id",
        sa.String(36),
        sa.ForeignKey("game_farms.user_id", ondelete="CASCADE"),
        primary_key=primary,
        nullable=False,
    )


def upgrade():
    # sqlite3 legacy mode does not BEGIN for DDL. Reserve a real transaction
    # before the first table so a partial failure can roll back and be retried.
    # Existing Alembic transactions (e.g. earlier revisions' version writes)
    # must not receive a nested BEGIN. PostgreSQL already has transactional DDL.
    if not op.get_context().as_sql:
        connection = op.get_bind()
        if (
            connection.dialect.name == "sqlite"
            and not connection.connection.driver_connection.in_transaction
        ):
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    op.create_table(
        "game_farms",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("layout", sa.JSON()),
        sa.Column("turnips", sa.JSON()),
        sa.Column(
            "scoring_ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="SET NULL")
        ),
        sa.Column("anonymous", sa.Boolean(), nullable=False),
        sa.Column("conscience", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(64)),
        sa.Column("thief_until", sa.DateTime(timezone=True)),
        sa.Column("newbie_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_slots",
        _user(True),
        sa.Column("idx", sa.Integer(), primary_key=True),
        sa.Column("plot", sa.JSON(), nullable=False),
        sa.Column("stolen_qty", sa.Integer(), nullable=False),
    )
    op.create_table(
        "game_inventory",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _user(),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "game_wallet_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        _user(),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("ref_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "reason", "ref_id", name="uq_game_wallet_receipt"),
    )
    op.create_table(
        "game_listings",
        _user(True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "game_weekly_settlements",
        _user(True),
        sa.Column("week", sa.String(10), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("anomaly_flags", sa.JSON(), nullable=False),
    )
    op.create_table(
        "game_leaderboards",
        sa.Column("week", sa.String(10), primary_key=True),
        sa.Column("board", sa.String(16), primary_key=True),
        _user(True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_table(
        "game_action_receipts",
        _user(True),
        sa.Column("request_id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_social_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "actor_id",
            sa.String(36),
            sa.ForeignKey("game_farms.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("game_farms.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_weather_cache",
        sa.Column("city_code", sa.String(32), primary_key=True),
        sa.Column("hour", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "game_market_snapshots",
        sa.Column("tick", sa.Integer(), primary_key=True),
        sa.Column("crop_id", sa.String(64), primary_key=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("supply_quantity", sa.Integer(), nullable=False),
    )
    for table in ["game_inventory", "game_wallet_entries"]:
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
    for column in ["actor_id", "owner_id", "created_at"]:
        op.create_index(f"ix_game_social_events_{column}", "game_social_events", [column])


def downgrade():
    for table in [
        "game_market_snapshots",
        "game_weather_cache",
        "game_social_events",
        "game_action_receipts",
        "game_leaderboards",
        "game_weekly_settlements",
        "game_listings",
        "game_wallet_entries",
        "game_inventory",
        "game_slots",
        "game_farms",
    ]:
        op.drop_table(table)
