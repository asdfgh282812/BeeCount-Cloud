"""商店(需求 #11,2026-08 使用者回饋改善 SD Phase 11)——`ReadTxProjection.merchant`
契约:

- web POST/PATCH `/write/ledgers/{id}/transactions` 接收 `merchant`,read 端点
  能读回;PATCH 不带 `merchant` 时保留既有值(partial update,同 note/
  deferred_posting_at 既有惯例)。
- mobile `/sync/push` 的 `transaction` merge 契约:partial update 缺键保留
  既有 `merchant` 标记(同 `_MERGE_SPECS["transaction"]` 里其它可选字段)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadTxProjection


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


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": device_id,
            "client_type": client_type, "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = _iso()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _ledger_internal_id(TS, external_id: str) -> str:
    with TS() as db:
        return db.scalar(select(Ledger.id).where(Ledger.external_id == external_id))


def _base_change_id(client: TestClient, web_token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {web_token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


# --------------------------------------------------------------------------- #
# Test 1: web create with merchant → response + projection                     #
# --------------------------------------------------------------------------- #

def test_web_create_and_read_tx_with_merchant() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "merchant_c1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "MC_C1")

        web_token = _login_web(client, "merchant_c1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "MC_C1")

        create_res = client.post(
            "/api/v1/write/ledgers/MC_C1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 42.0,
                "happened_at": _iso(),
                "merchant": "全家便利商店",
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]

        list_res = client.get(
            "/api/v1/read/ledgers/MC_C1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert list_res.status_code == 200, list_res.text
        items = [it for it in list_res.json() if it["id"] == tx_id]
        assert len(items) == 1, f"tx {tx_id} not in read list"
        assert items[0]["merchant"] == "全家便利商店"

        lid = _ledger_internal_id(TS, "MC_C1")
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.merchant == "全家便利商店"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 2: web update touches an unrelated field, merchant unchanged            #
# --------------------------------------------------------------------------- #

def test_web_update_tx_merchant_partial_update_keeps_value() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "merchant_u1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "MC_U1")

        web_token = _login_web(client, "merchant_u1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "MC_U1")

        create_res = client.post(
            "/api/v1/write/ledgers/MC_U1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 10.0,
                "happened_at": _iso(),
                "merchant": "星巴克",
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]
        new_base = int(create_res.json()["new_change_id"])

        # update: 只改 note,不带 merchant,应保留旧值
        update_res = client.patch(
            f"/api/v1/write/ledgers/MC_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={
                "base_change_id": new_base,
                "note": "改了備註",
            },
        )
        assert update_res.status_code == 200, update_res.text

        list_res = client.get(
            "/api/v1/read/ledgers/MC_U1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert list_res.status_code == 200, list_res.text
        item = [it for it in list_res.json() if it["id"] == tx_id][0]
        assert item["note"] == "改了備註"
        assert item["merchant"] == "星巴克"

        lid = _ledger_internal_id(TS, "MC_U1")
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.merchant == "星巴克"

        # explicit null 清空 merchant
        base2 = _base_change_id(client, web_token, "MC_U1")
        clear_res = client.patch(
            f"/api/v1/write/ledgers/MC_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={"base_change_id": base2, "merchant": None},
        )
        assert clear_res.status_code == 200, clear_res.text
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.merchant is None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 3: mobile /sync/push merge 契约                                         #
# --------------------------------------------------------------------------- #

def test_mobile_push_merchant_partial_update_keeps_value():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "merchant_m1@example.com", device_id="d-app", client_type="app")
        hdr = {"Authorization": f"Bearer {app_tok}"}
        client.post(
            "/api/v1/sync/push",
            headers=hdr,
            json={
                "device_id": "d-app",
                "changes": [{
                    "ledger_id": "ML1",
                    "entity_type": "ledger",
                    "entity_sync_id": "ML1",
                    "action": "upsert",
                    "updated_at": _iso(),
                    "payload": {"syncId": "ML1", "ledgerName": "ML1", "currency": "CNY"},
                }],
            },
        )

        now = datetime.now(timezone.utc)
        sync_id = "tx_merchant1"
        client.post(
            "/api/v1/sync/push",
            headers=hdr,
            json={
                "device_id": "d-app",
                "changes": [{
                    "ledger_id": "ML1",
                    "entity_type": "transaction",
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "updated_at": _iso(),
                    "payload": {
                        "syncId": sync_id, "type": "expense", "amount": 100.0,
                        "happenedAt": _iso(now), "merchant": "路易莎咖啡",
                    },
                }],
            },
        )

        # 只改 note,不带 merchant,应保留旧值
        res = client.post(
            "/api/v1/sync/push",
            headers=hdr,
            json={
                "device_id": "d-app",
                "changes": [{
                    "ledger_id": "ML1",
                    "entity_type": "transaction",
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "updated_at": _iso(),
                    "payload": {"syncId": sync_id, "note": "备注"},
                }],
            },
        )
        assert res.status_code == 200, res.text

        db = TS()
        try:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
            assert row is not None
            assert row.note == "备注"
            assert row.merchant == "路易莎咖啡"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
