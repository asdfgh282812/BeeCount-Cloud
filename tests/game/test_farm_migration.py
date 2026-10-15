"""0074 round-trip runs only against ephemeral SQLite, preserving bookkeeping."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from src.database import Base
from src.services.game import models  # noqa: F401 -- register metadata


def test_farm_migration_upgrade_and_downgrade_preserves_bookkeeping():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0074_game_farms.py"
    spec = importlib.util.spec_from_file_location("farm_migration_0074", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db_engine = sa.create_engine("sqlite://")
    with db_engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("CREATE TABLE ledgers (id VARCHAR(36) PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO users (id) VALUES ('preserved-user')"))
        connection.execute(sa.text("INSERT INTO ledgers (id) VALUES ('preserved-ledger')"))
        mod.op = Operations(MigrationContext.configure(connection))
        mod.upgrade()
        inspector = sa.inspect(connection)
        tables = {name for name in inspector.get_table_names() if name.startswith("game_")}
        expected = {name for name in Base.metadata.tables if name.startswith("game_")}
        assert tables == expected and len(tables) >= 10
        for table in tables:
            assert {c["name"] for c in inspector.get_columns(table)} == set(
                Base.metadata.tables[table].columns.keys()
            )
            assert set(inspector.get_pk_constraint(table)["constrained_columns"]) == {
                c.name for c in Base.metadata.tables[table].primary_key.columns
            }
        wallet_constraints = inspector.get_unique_constraints("game_wallet_entries")
        assert any(
            set(c["column_names"]) == {"user_id", "reason", "ref_id"} for c in wallet_constraints
        )
        slot_fk = inspector.get_foreign_keys("game_slots")
        assert (
            slot_fk[0]["referred_table"] == "game_farms"
            and slot_fk[0]["options"]["ondelete"] == "CASCADE"
        )
        mod.downgrade()
        assert sa.inspect(connection).get_table_names() == ["ledgers", "users"]
        assert connection.scalar(sa.text("SELECT id FROM users")) == "preserved-user"
        assert connection.scalar(sa.text("SELECT id FROM ledgers")) == "preserved-ledger"
    db_engine.dispose()


def test_sqlite_partial_ddl_failure_rolls_back_and_can_retry(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0074_game_farms.py"
    spec = importlib.util.spec_from_file_location("farm_migration_atomic_0074", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE ledgers (id VARCHAR(36) PRIMARY KEY)")
        connection.commit()
        operations = Operations(MigrationContext.configure(connection))
        create = operations.create_table
        count = [0]

        def fail_partway(*args, **kwargs):
            count[0] += 1
            if count[0] == 5:
                raise RuntimeError("simulated interrupted migration")
            return create(*args, **kwargs)

        mod.op = operations
        monkeypatch.setattr(operations, "create_table", fail_partway)
        with pytest.raises(RuntimeError, match="interrupted"):
            with connection.begin():
                mod.upgrade()
        assert sa.inspect(connection).get_table_names() == ["ledgers", "users"]
        connection.rollback()
        monkeypatch.setattr(operations, "create_table", create)
        with connection.begin():
            mod.upgrade()
        assert "game_market_snapshots" in sa.inspect(connection).get_table_names()
    engine.dispose()
