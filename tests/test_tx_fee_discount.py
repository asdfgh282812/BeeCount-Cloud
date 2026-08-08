"""手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction)——
`ReadTxProjection.base_amount`/`fee_amount`/`fee_label`/`discount_amount`/
`discount_label` 契约:

- web POST/PATCH `/write/ledgers/{id}/transactions` 接收這五個欄位,server
  端依 tx_type 用 base_amount/fee_amount/discount_amount 重新算出權威的
  `amount`(expense: base+fee-discount;income: base-fee+discount)。
- PATCH 不带这些字段时保留既有值(partial update,同 merchant/note 既有
  惯例);显式传 null 清空该分量。
- transfer 类型带任一新欄位直接 400。
- mobile `/sync/push` 的 `transaction` merge 契约:partial update 缺键保留
  既有值。
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
# Test 1: web create expense with fee/discount → server 算出總額            #
# --------------------------------------------------------------------------- #

def test_web_create_expense_with_fee_discount_computes_amount() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "feediscount_c1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FD_C1")

        web_token = _login_web(client, "feediscount_c1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "FD_C1")

        # expense: amount = base + fee - discount = 790 + 0 - 100 = 690
        create_res = client.post(
            "/api/v1/write/ledgers/FD_C1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 999.0,  # 客端算错也没关系,server 端会覆盖
                "base_amount": 790.0,
                "fee_amount": 0.0,
                "discount_amount": 100.0,
                "discount_label": "滿千送百",
                "happened_at": _iso(),
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]

        list_res = client.get(
            "/api/v1/read/ledgers/FD_C1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert list_res.status_code == 200, list_res.text
        item = [it for it in list_res.json() if it["id"] == tx_id][0]
        assert item["amount"] == 690.0
        assert item["base_amount"] == 790.0
        assert item["fee_amount"] == 0.0
        assert item["discount_amount"] == 100.0
        assert item["discount_label"] == "滿千送百"

        lid = _ledger_internal_id(TS, "FD_C1")
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.amount == 690.0
            assert row.base_amount == 790.0
            assert row.discount_amount == 100.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 2: income 方向公式(base - fee + discount)                             #
# --------------------------------------------------------------------------- #

def test_web_create_income_with_fee_discount_computes_amount() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "feediscount_c2@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FD_C2")

        web_token = _login_web(client, "feediscount_c2@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "FD_C2")

        # income: amount = base - fee + discount = 1000 - 50 + 0 = 950
        create_res = client.post(
            "/api/v1/write/ledgers/FD_C2/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "income",
                "amount": 1000.0,
                "base_amount": 1000.0,
                "fee_amount": 50.0,
                "fee_label": "跨行手續費",
                "happened_at": _iso(),
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]

        list_res = client.get(
            "/api/v1/read/ledgers/FD_C2/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        item = [it for it in list_res.json() if it["id"] == tx_id][0]
        assert item["amount"] == 950.0
        assert item["fee_amount"] == 50.0
        assert item["fee_label"] == "跨行手續費"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 3: transfer 带手續費/折扣直接 400                                       #
# --------------------------------------------------------------------------- #

def test_web_create_transfer_with_fee_discount_rejected() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "feediscount_c3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FD_C3")

        web_token = _login_web(client, "feediscount_c3@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "FD_C3")

        create_res = client.post(
            "/api/v1/write/ledgers/FD_C3/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 100.0,
                "base_amount": 100.0,
                "fee_amount": 5.0,
                "happened_at": _iso(),
            },
        )
        assert create_res.status_code == 400, create_res.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 4: PATCH partial update 保留既有分量 + explicit null 清空              #
# --------------------------------------------------------------------------- #

def test_web_update_tx_fee_discount_partial_update_keeps_value() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "feediscount_u1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "FD_U1")

        web_token = _login_web(client, "feediscount_u1@example.com")["access_token"]
        web_hdr = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "pytest-web"}
        base = _base_change_id(client, web_token, "FD_U1")

        create_res = client.post(
            "/api/v1/write/ledgers/FD_U1/transactions",
            headers=web_hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 100.0,
                "base_amount": 100.0,
                "fee_amount": 10.0,
                "discount_amount": 20.0,
                "happened_at": _iso(),
            },
        )
        assert create_res.status_code == 200, create_res.text
        tx_id = create_res.json()["entity_id"]
        new_base = int(create_res.json()["new_change_id"])

        # 只改 note,不带任何 fee/discount 字段,应保留旧值 + amount 不变
        update_res = client.patch(
            f"/api/v1/write/ledgers/FD_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={"base_change_id": new_base, "note": "改了備註"},
        )
        assert update_res.status_code == 200, update_res.text

        list_res = client.get(
            "/api/v1/read/ledgers/FD_U1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        item = [it for it in list_res.json() if it["id"] == tx_id][0]
        assert item["note"] == "改了備註"
        assert item["fee_amount"] == 10.0
        assert item["discount_amount"] == 20.0
        assert item["amount"] == 90.0  # 100 + 10 - 20

        # 只改 fee_amount,base/discount fallback 现有值:
        # amount = 100 + 30 - 20 = 110
        base2 = _base_change_id(client, web_token, "FD_U1")
        update_res2 = client.patch(
            f"/api/v1/write/ledgers/FD_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={"base_change_id": base2, "fee_amount": 30.0},
        )
        assert update_res2.status_code == 200, update_res2.text
        list_res2 = client.get(
            "/api/v1/read/ledgers/FD_U1/transactions",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        item2 = [it for it in list_res2.json() if it["id"] == tx_id][0]
        assert item2["fee_amount"] == 30.0
        assert item2["discount_amount"] == 20.0
        assert item2["amount"] == 110.0

        # explicit null 清空 discount_amount:amount = 100 + 30 - 0 = 130
        base3 = _base_change_id(client, web_token, "FD_U1")
        clear_res = client.patch(
            f"/api/v1/write/ledgers/FD_U1/transactions/{tx_id}",
            headers=web_hdr,
            json={"base_change_id": base3, "discount_amount": None},
        )
        assert clear_res.status_code == 200, clear_res.text
        lid = _ledger_internal_id(TS, "FD_U1")
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == lid,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert row is not None
            assert row.discount_amount is None
            assert row.amount == 130.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Test 5: mobile /sync/push merge 契约                                        #
# --------------------------------------------------------------------------- #

def test_mobile_push_fee_discount_partial_update_keeps_value():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "feediscount_m1@example.com", device_id="d-app", client_type="app")
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
        sync_id = "tx_feediscount1"
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
                        "syncId": sync_id, "type": "expense", "amount": 90.0,
                        "happenedAt": _iso(now),
                        "baseAmount": 100.0, "feeAmount": 10.0,
                        "discountAmount": 20.0, "discountLabel": "折扣券",
                    },
                }],
            },
        )

        # 只改 note,不带任何 fee/discount 字段,应保留旧值
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
            assert row.base_amount == 100.0
            assert row.fee_amount == 10.0
            assert row.discount_amount == 20.0
            assert row.discount_label == "折扣券"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
