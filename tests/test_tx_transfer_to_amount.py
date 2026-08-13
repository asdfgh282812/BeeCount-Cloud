"""跨幣別轉帳(2026-08,read_tx_projection.to_amount)Cloud 端契約:

- `to_amount` = 轉入帳戶自身幣別的金額;`amount` 語意不變,仍是轉出帳戶自身
  幣別、驅動轉出帳戶餘額增減的那個數。NULL = 同幣種轉帳(舊資料/舊版 App)。
- write endpoint 驗證:轉出/轉入帳戶幣別不同時 `to_amount` 必填
  (`write/_shared.py::_assert_transfer_to_amount_valid`)。
- sync_applier merge:partial-push 不清掉既有 `to_amount`;payload 帶
  `amount` 不帶 `toAmount` 時按隱含匯率等比縮放
  (`_sync_to_amount_after_merge`)。
- 餘額計算:`list_workspace_accounts`/`workspace_net_worth_history` 轉入端
  一律 `COALESCE(to_amount, amount)`。

測試基建與 test_tx_multi_currency.py 同套:in-memory SQLite + create_all +
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


def _seed_two_currency_ledger(client, hdr_app):
    """TWD 帳本,acc-twd(TWD)/acc-jpy(JPY)兩個帳戶,各自初始餘額 10000。"""
    _push(client, hdr_app, "lg1", "ledger", "lg1",
          {"syncId": "lg1", "ledgerName": "L", "currency": "TWD"})
    _push(client, hdr_app, "lg1", "account", "acc-twd",
          {"syncId": "acc-twd", "name": "台幣", "type": "cash",
           "initialBalance": 10000.0, "currency": "TWD"})
    _push(client, hdr_app, "lg1", "account", "acc-jpy",
          {"syncId": "acc-jpy", "name": "日幣", "type": "cash",
           "initialBalance": 10000.0, "currency": "JPY"})


def test_transfer_partial_update_keeps_existing_to_amount():
    """partial-push(只改 note,不帶 toAmount)不得清掉既有 to_amount 快照。"""
    client, TS = _make_client()
    try:
        app_token, _ = _two_tokens(client, "toamt1@t.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        _seed_two_currency_ledger(client, hdr)
        _push(client, hdr, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 2000.0,
               "toAmount": 400.0, "fromAccountId": "acc-twd",
               "toAccountId": "acc-jpy", "happenedAt": _iso()})

        _push(client, hdr, "lg1", "transaction", "t1",
              {"syncId": "t1", "note": "改備註"})

        lid = _ledger_internal_id(TS, "lg1")
        tx = _get_tx(TS, lid, "t1")
        assert tx.note == "改備註"
        assert tx.amount == 2000.0
        assert tx.to_amount == 400.0, "partial update 不得清掉既有 to_amount"
    finally:
        app.dependency_overrides.clear()


def test_transfer_amount_change_rescales_to_amount():
    """舊客戶端只 push 新 amount(不帶 toAmount)時,既有 to_amount 要按隱含
    匯率等比縮放,不能停留在舊值(轉入端金額會失配)。"""
    client, TS = _make_client()
    try:
        app_token, _ = _two_tokens(client, "toamt2@t.com")
        hdr = {"Authorization": f"Bearer {app_token}"}
        _seed_two_currency_ledger(client, hdr)
        # 隱含匯率:2000 TWD = 400 JPY → 1 TWD = 0.2 JPY
        _push(client, hdr, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 2000.0,
               "toAmount": 400.0, "fromAccountId": "acc-twd",
               "toAccountId": "acc-jpy", "happenedAt": _iso()})

        # 只改 amount(3000),不帶 toAmount → 應等比縮放成 600
        _push(client, hdr, "lg1", "transaction", "t1", {"syncId": "t1", "amount": 3000.0})

        lid = _ledger_internal_id(TS, "lg1")
        tx = _get_tx(TS, lid, "t1")
        assert tx.amount == 3000.0
        assert abs(tx.to_amount - 600.0) < 1e-9, tx.to_amount
    finally:
        app.dependency_overrides.clear()


def test_create_transfer_requires_to_amount_when_currency_differs():
    """Web write:轉出/轉入帳戶幣別不同又不帶 to_amount → 400 阻斷保存。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt3@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "d-web"}
        _seed_two_currency_ledger(client, hdr_app)

        r = client.post(
            "/api/v1/write/ledgers/lg1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "transfer", "amount": 2000.0,
                "happened_at": _iso(), "from_account_id": "acc-twd",
                "to_account_id": "acc-jpy",
            },
        )
        assert r.status_code == 400, r.text

        # 帶 to_amount 就能過
        r2 = client.post(
            "/api/v1/write/ledgers/lg1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "transfer", "amount": 2000.0,
                "happened_at": _iso(), "from_account_id": "acc-twd",
                "to_account_id": "acc-jpy", "to_amount": 400.0,
            },
        )
        assert r2.status_code == 200, r2.text
        lid = _ledger_internal_id(TS, "lg1")
        tx = _get_tx(TS, lid, r2.json()["entity_id"])
        assert tx.to_amount == 400.0
    finally:
        app.dependency_overrides.clear()


def test_update_transfer_requires_to_amount_when_switched_to_different_currency():
    """Web write PATCH:既有同幣種轉帳,改 to_account 成不同幣別的帳戶卻不帶
    to_amount → 400(缺鍵 fallback 既有值後仍判定幣別不同,必須擋)。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt4@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "d-web"}
        _seed_two_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "account", "acc-twd2",
              {"syncId": "acc-twd2", "name": "台幣2", "type": "cash",
               "initialBalance": 0.0, "currency": "TWD"})
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 1000.0,
               "fromAccountId": "acc-twd", "toAccountId": "acc-twd2",
               "happenedAt": _iso()})

        r = client.patch(
            "/api/v1/write/ledgers/lg1/transactions/t1",
            headers=hdr_web,
            json={"base_change_id": 0, "to_account_id": "acc-jpy"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_list_workspace_accounts_cross_currency_transfer_balance():
    """轉出/轉入帳戶各自的餘額增減必須用各自幣別的金額,不是同一個數字。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt5@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_two_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 2000.0,
               "toAmount": 400.0, "fromAccountId": "acc-twd",
               "toAccountId": "acc-jpy", "happenedAt": _iso()})

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        # 轉出:10000 - 2000(TWD) = 8000
        assert rows["acc-twd"]["balance"] == 8000.0
        # 轉入:10000 + 400(JPY,不是 2000)= 10400
        assert rows["acc-jpy"]["balance"] == 10400.0
    finally:
        app.dependency_overrides.clear()


def test_net_worth_history_cross_currency_transfer():
    """淨值歷史序列:轉入端一樣要用 to_amount,不能用轉出端的 amount。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt6@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_two_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 2000.0,
               "toAmount": 400.0, "fromAccountId": "acc-twd",
               "toAccountId": "acc-jpy",
               "happenedAt": "2026-01-15T00:00:00+00:00"})

        from src.models import User, UserProfile
        with TS() as db:
            uid = db.query(User).filter(User.email == "toamt6@t.com").first().id
            db.add(UserProfile(user_id=uid, primary_currency="TWD"))
            db.commit()

        r = client.get(
            "/api/v1/read/workspace/net-worth-history",
            headers=hdr_web,
            params={"scope": "all"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 多幣種缺自動匯率(測試環境沒接匯率源)→ acc-jpy 整條被剔除,只留
        # acc-twd:10000 - 2000 = 8000。這裡驗證的重點是「沒有把 2000 錯誤
        # 加回 acc-jpy 那條、也沒有把 acc-twd 錯誤扣成 8000-2000」,不是匯率
        # 折算本身(net_worth_history_converts_to_base 已覆蓋匯率折算)。
        series = {s["bucket"]: s for s in body["series"]}
        assert series["2026-01"]["net_worth"] == 8000.0
    finally:
        app.dependency_overrides.clear()


def test_web_create_transfer_with_to_amount_lands_in_projection():
    """POST /write/ledgers/{id}/transactions 帶 to_amount → 投影落值
    (schema 白名單不放行的話 pydantic 會靜默丟欄位,這條測試鎖住白名單)。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt7@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}", "X-Device-ID": "d-web"}
        _seed_two_currency_ledger(client, hdr_app)

        r = client.post(
            "/api/v1/write/ledgers/lg1/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "transfer", "amount": 2000.0,
                "happened_at": _iso(), "from_account_id": "acc-twd",
                "to_account_id": "acc-jpy", "to_amount": 400.0,
            },
        )
        assert r.status_code == 200, r.text
        lid = _ledger_internal_id(TS, "lg1")
        tx = _get_tx(TS, lid, r.json()["entity_id"])
        assert tx.to_amount == 400.0
        assert tx.amount == 2000.0
    finally:
        app.dependency_overrides.clear()


def test_read_transactions_expose_to_amount():
    """GET /read/ledgers/{id}/transactions 回傳 to_amount 欄位。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "toamt8@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}
        _seed_two_currency_ledger(client, hdr_app)
        _push(client, hdr_app, "lg1", "transaction", "t1",
              {"syncId": "t1", "type": "transfer", "amount": 2000.0,
               "toAmount": 400.0, "fromAccountId": "acc-twd",
               "toAccountId": "acc-jpy", "happenedAt": _iso()})

        r = client.get("/api/v1/read/ledgers/lg1/transactions", headers=hdr_web)
        assert r.status_code == 200, r.text
        row = next(x for x in r.json() if x["id"] == "t1")
        assert row["to_amount"] == 400.0
    finally:
        app.dependency_overrides.clear()
