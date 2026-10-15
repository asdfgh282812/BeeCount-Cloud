"""Farm authority is independent of bookkeeping projections and sync changes."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...database import Base


class GameFarm(Base):
    __tablename__ = "game_farms"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[dict] = mapped_column(JSON)
    layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    turnips: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scoring_ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledgers.id", ondelete="SET NULL"), nullable=True
    )
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    conscience: Mapped[int] = mapped_column(Integer, default=100)
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    thief_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    newbie_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GameSlot(Base):
    __tablename__ = "game_slots"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), primary_key=True
    )
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    plot: Mapped[dict] = mapped_column(JSON)
    stolen_qty: Mapped[int] = mapped_column(Integer, default=0)


class GameInventory(Base):
    __tablename__ = "game_inventory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GameWallet(Base):
    __tablename__ = "game_wallet_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "reason", "ref_id", name="uq_game_wallet_receipt"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), index=True
    )
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    ref_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GameListing(Base):
    __tablename__ = "game_listings"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), primary_key=True
    )
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class GameSettlement(Base):
    __tablename__ = "game_weekly_settlements"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), primary_key=True
    )
    week: Mapped[str] = mapped_column(String(10), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    anomaly_flags: Mapped[list] = mapped_column(JSON, default=list)


class GameLeaderboard(Base):
    __tablename__ = "game_leaderboards"
    week: Mapped[str] = mapped_column(String(10), primary_key=True)
    board: Mapped[str] = mapped_column(String(16), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[dict] = mapped_column(JSON)


class GameAction(Base):
    __tablename__ = "game_action_receipts"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GameEvent(Base):
    __tablename__ = "game_social_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("game_farms.user_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class GameWeather(Base):
    __tablename__ = "game_weather_cache"
    city_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GameMarketSnapshot(Base):
    __tablename__ = "game_market_snapshots"
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    crop_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    price: Mapped[int] = mapped_column(Integer)
    supply_quantity: Mapped[int] = mapped_column(Integer)
