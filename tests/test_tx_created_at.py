import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from test_tx_multi_currency import _get_tx, _ledger_internal_id, _make_client, _push, _two_tokens

from src.main import app
from src.models import Ledger, ReadTxProjection
from src.projection import rebuild_from_snapshot
from src.snapshot_builder import build
from src.snapshot_mutator import create_transaction, update_transaction


@pytest.fixture
def session():
    client, factory = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "created-at@t.com")
        app_headers = {"Authorization": f"Bearer {app_token}"}
        web_headers = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "d-web"}
        _push(
            client,
            app_headers,
            "lg1",
            "ledger",
            "lg1",
            {"syncId": "lg1", "ledgerName": "L", "currency": "TWD"},
        )
        yield client, factory, app_headers, web_headers
    finally:
        app.dependency_overrides.clear()


def test_mobile_created_at_partial_immutable_and_snapshot(session):
    client, factory, headers, web_headers = session
    created = "2026-10-13T16:30:00Z"  # 台北 10/14 凌晨00:30
    _push(
        client,
        headers,
        "lg1",
        "transaction",
        "tx1",
        {
            "type": "expense",
            "amount": 100,
            "happenedAt": "2026-10-01T00:00:00Z",
            "createdAt": created,
        },
    )
    lid = _ledger_internal_id(factory, "lg1")
    before = _get_tx(factory, lid, "tx1")
    assert before.client_created_at.replace(tzinfo=timezone.utc).isoformat() == created.replace(
        "Z", "+00:00"
    )
    assert before.first_seen_at is not None
    for payload in [{"note": "edited"}, {"createdAt": None}, {"createdAt": "2027-01-01T00:00:00Z"}]:
        _push(client, headers, "lg1", "transaction", "tx1", payload)
        after = _get_tx(factory, lid, "tx1")
        assert after.client_created_at == before.client_created_at
        assert after.first_seen_at == before.first_seen_at
    with factory() as db:
        ledger = db.get(Ledger, lid)
        snapshot = build(db, ledger)
        assert snapshot["items"][0]["createdAt"] == created.replace("Z", "+00:00")
        rebuild_from_snapshot(
            db, ledger_id=lid, user_id=ledger.user_id, snapshot=snapshot, source_change_id=100
        )
        db.commit()
    after = _get_tx(factory, lid, "tx1")
    assert after.first_seen_at == before.first_seen_at
    response = client.get("/api/v1/read/ledgers/lg1/transactions", headers=web_headers)
    assert response.status_code == 200, response.text
    assert response.json()[0]["created_at"] is not None
    response = client.get("/api/v1/read/workspace/transactions", headers=web_headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["created_at"] is not None


def test_legacy_or_invalid_created_at_remains_null(session):
    client, factory, headers, _ = session
    for idx, extra in enumerate([{}, {"createdAt": None}, {"createdAt": "invalid"}]):
        _push(
            client,
            headers,
            "lg1",
            "transaction",
            f"legacy-{idx}",
            {"type": "expense", "amount": 1, "happenedAt": "2026-10-14T00:00:00Z", **extra},
        )
        row = _get_tx(factory, _ledger_internal_id(factory, "lg1"), f"legacy-{idx}")
        assert row.client_created_at is None
        assert row.first_seen_at is not None


def test_web_creation_uses_server_now_and_edit_keeps_timestamp(session):
    client, factory, _, headers = session
    before = datetime.now(timezone.utc)
    response = client.post(
        "/api/v1/write/ledgers/lg1/transactions",
        headers=headers,
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": 10,
            "happened_at": "2026-01-01T00:00:00Z",
            "created_at": "2000-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 200, response.text
    with factory() as db:
        row = db.scalar(select(ReadTxProjection))
        assert (
            before
            <= row.client_created_at.replace(tzinfo=timezone.utc)
            <= datetime.now(timezone.utc)
        )
        created = row.client_created_at
        snapshot = build(db, db.get(Ledger, row.ledger_id))
        edited = update_transaction(snapshot, row.sync_id, {"note": "edited"})
        assert edited["items"][0]["createdAt"] == created.replace(tzinfo=timezone.utc).isoformat()


def test_mutator_does_not_copy_happened_at_or_client_creation_field():
    snapshot, tx_id = create_transaction(
        {},
        {
            "tx_type": "income",
            "amount": 1,
            "happened_at": "2020-01-01T00:00:00Z",
            "created_at": "2020-01-01T00:00:00Z",
        },
    )
    item = next(x for x in snapshot["items"] if x["syncId"] == tx_id)
    assert datetime.fromisoformat(item["createdAt"]).year >= 2026
    assert item["createdAt"] != item["happenedAt"]


def test_migration_keeps_legacy_temporal_fields_null():
    path = Path(__file__).parents[1] / "alembic/versions/0073_tx_created_at.py"
    spec = importlib.util.spec_from_file_location("migration73", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE read_tx_projection (sync_id TEXT PRIMARY KEY, amount REAL)")
        )
        conn.execute(text("INSERT INTO read_tx_projection VALUES ('old', 100)"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
        row = conn.execute(
            text("SELECT amount, client_created_at, first_seen_at FROM read_tx_projection")
        ).one()
        assert tuple(row) == (100.0, None, None)
