"""`/workspace/debts`:淨資產卡片用,跨帳本按幣種彙總未結清、未排除的欠款/
應收(見 `src/routers/read/workspace.py` `_workspace_debt_currency_totals`
docstring)。此前 web 端淨資產卡完全沒查過 debts 表,只算 accounts,
造成「對方欠我」的應收沒被算進資產,跟 app 端(LocalRepository.
getNetWorthBreakdown 把 debt 併進 accounts 淨資產)對不上帳。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _register_and_token(client: TestClient, email: str, *, device_id: str, client_type: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _two_tokens(client, email):
    app_token = _register_and_token(client, email, device_id="d-app", client_type="app")
    web_token = _register_and_token(client, email, device_id="d-web", client_type="web")
    return app_token, web_token


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": "d-app", "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_workspace_debts_sums_receivable_and_payable_by_currency():
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "wsdebt1@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "个人账本", "currency": "TWD"})
        _push(client, hdr_app, "lg1", "debt", "debt-recv",
              {"syncId": "debt-recv", "direction": "receivable",
               "counterpartyName": "易游网", "principalAmount": 2920.0})
        _push(client, hdr_app, "lg1", "debt", "debt-pay",
              {"syncId": "debt-pay", "direction": "payable",
               "counterpartyName": "小張", "principalAmount": 500.0})

        r = client.get("/api/v1/read/workspace/debts", headers=hdr_web)
        assert r.status_code == 200, r.text
        rows = {row["currency"]: row for row in r.json()}
        assert rows["TWD"]["receivable_total"] == 2920.0
        assert rows["TWD"]["payable_total"] == 500.0
    finally:
        app.dependency_overrides.clear()


def test_workspace_debts_excludes_excluded_from_total_and_closed():
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "wsdebt2@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "个人账本", "currency": "TWD"})
        _push(client, hdr_app, "lg1", "debt", "debt-excluded",
              {"syncId": "debt-excluded", "direction": "receivable",
               "counterpartyName": "阿明", "principalAmount": 1000.0,
               "excludedFromTotal": True})
        _push(client, hdr_app, "lg1", "debt", "debt-closed",
              {"syncId": "debt-closed", "direction": "receivable",
               "counterpartyName": "阿華", "principalAmount": 800.0,
               "closedAt": _iso()})

        r = client.get("/api/v1/read/workspace/debts", headers=hdr_web)
        assert r.status_code == 200, r.text
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


def test_workspace_debts_excludes_fully_repaid():
    """已還清(remaining <= 0.01)的欠款不該計入,避免已還清部分重複算進總額。"""
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "wsdebt3@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "个人账本", "currency": "TWD"})
        _push(client, hdr_app, "lg1", "debt", "debt-1",
              {"syncId": "debt-1", "direction": "payable",
               "counterpartyName": "小張", "principalAmount": 500.0})
        _push(client, hdr_app, "lg1", "transaction", "tx-repay",
              {"syncId": "tx-repay", "type": "expense", "amount": 500.0,
               "happenedAt": _iso(), "debtId": "debt-1"})

        r = client.get("/api/v1/read/workspace/debts", headers=hdr_web)
        assert r.status_code == 200, r.text
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()
