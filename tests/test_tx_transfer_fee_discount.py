"""轉帳手續費/折損(2026-08-29,design doc
docs/superpowers/specs/2026-08-29-transfer-fee-discount-design.md §6)
Cloud 端契約:

- write endpoint:`tx_type == "transfer"` 不再被 `_normalize_fee_discount_amount`
  硬擋 400(見 test_tx_fee_discount.py),`fee_amount`/`discount_amount` 疊加
  在餘額計算上,不重算 `amount`。
- `/read/workspace/accounts` 餘額:轉出端疊加 `fee_amount`,轉入端疊加
  `discount_amount`(扣除),公式跟 App 端
  `LocalAccountRepository._transferOutEffect`/`_transferInEffect` 對齊。
- `/read/workspace/net-worth-history`:同一套公式套用在逐筆重播累加上。

測試基建與 test_tx_transfer_to_amount.py 同套:in-memory SQLite + create_all +
真實 `/sync/push` / write endpoint 流。
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


def _ledger_internal_id(TS, external_id):
    with TS() as db:
        return db.scalar(select(Ledger.id).where(Ledger.external_id == external_id))


def _get_tx(TS, ledger_internal_id, sync_id):
    with TS() as db:
        return db.scalar(select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_internal_id,
            ReadTxProjection.sync_id == sync_id,
        ))


def _seed_same_currency_ledger(client, hdr_app):
    """TWD 帳本,acc-a/acc-b 兩個同幣別帳戶,各自初始餘額 10000。"""
    _push(client, hdr_app, "lg1", "ledger", "lg1",
          {"syncId": "lg1", "ledgerName": "L", "currency": "TWD"})
    _push(client, hdr_app, "lg1", "account", "acc-a",
          {"syncId": "acc-a", "name": "現金", "type": "cash",
           "initialBalance": 10000.0, "currency": "TWD"})
    _push(client, hdr_app, "lg1", "account", "acc-b",
          {"syncId": "acc-b", "name": "銀行", "type": "cash",
           "initialBalance": 10000.0, "currency": "TWD"})


def test_sync_push_transfer_with_fee_discount_lands_in_projection():
    """mobile /sync/push 的 transaction merge spec 對 fee/discount 欄位本來
    就沒有 tx_type 限制,這裡驗證 transfer 型別也能正確落地。"""
    client, TS = _make_client()
    try:
        app_token, _ = _two_tokens(client, "txfee1@t.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        _seed_same_currency_ledger(client, hdr)
        _push(client, hdr, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 1000.0,
               "fromAccountId": "acc-a", "toAccountId": "acc-b",
               "feeAmount": 15.0, "feeLabel": "跨行手續費",
               "discountAmount": 8.0, "discountLabel": "到帳折損",
               "happenedAt": _iso()})

        lid = _ledger_internal_id(TS, "lg1")
        tx = _get_tx(TS, lid, "t1")
        assert tx.amount == 1000.0  # 不重算
        assert tx.fee_amount == 15.0
        assert tx.fee_label == "跨行手續費"
        assert tx.discount_amount == 8.0
        assert tx.discount_label == "到帳折損"
    finally:
        app.dependency_overrides.clear()


def test_list_workspace_accounts_transfer_fee_discount_balance():
    """轉出帳戶疊加手續費、轉入帳戶疊加折損,公式跟 App 端
    _transferOutEffect/_transferInEffect 對齐。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "txfee2@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_same_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 1000.0,
               "fromAccountId": "acc-a", "toAccountId": "acc-b",
               "feeAmount": 15.0, "discountAmount": 8.0,
               "happenedAt": _iso()})

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        # 轉出:10000 - (1000 + 15) = 8985
        assert rows["acc-a"]["balance"] == 8985.0
        # 轉入:10000 + (1000 - 8) = 10992
        assert rows["acc-b"]["balance"] == 10992.0
    finally:
        app.dependency_overrides.clear()


def test_list_workspace_accounts_transfer_without_fee_discount_unchanged():
    """回歸:沒有手續費/折損的舊資料,行為與改動前完全一致。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "txfee3@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_same_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 1000.0,
               "fromAccountId": "acc-a", "toAccountId": "acc-b",
               "happenedAt": _iso()})

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        assert rows["acc-a"]["balance"] == 9000.0
        assert rows["acc-b"]["balance"] == 11000.0
    finally:
        app.dependency_overrides.clear()


def test_net_worth_history_transfer_fee_discount():
    """淨值歷史序列:轉帳手續費/折損疊加後,兩個同幣別帳戶餘額變動相加起來
    (整體淨值)應該反映「手續費+折損」這兩筆從系統中蒸發的錢——
    淨值變化 = -(fee_amount + discount_amount)。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "txfee4@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_same_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 1000.0,
               "fromAccountId": "acc-a", "toAccountId": "acc-b",
               "feeAmount": 15.0, "discountAmount": 8.0,
               "happenedAt": "2026-01-15T00:00:00+00:00"})

        from src.models import User, UserProfile
        with TS() as db:
            uid = db.query(User).filter(User.email == "txfee4@t.com").first().id
            db.add(UserProfile(user_id=uid, primary_currency="TWD"))
            db.commit()

        r = client.get(
            "/api/v1/read/workspace/net-worth-history",
            headers=hdr_web,
            params={"scope": "all"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        series = {s["bucket"]: s for s in body["series"]}
        # 初始淨值 20000(兩個帳戶各 10000),轉帳本身淨值不變(左手換右手),
        # 但手續費 15 + 折損 8 = 23 從系統中蒸發 → 20000 - 23 = 19977。
        assert series["2026-01"]["net_worth"] == 19977.0
    finally:
        app.dependency_overrides.clear()
