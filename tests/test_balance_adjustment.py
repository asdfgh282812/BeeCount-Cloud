"""餘額調整(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5)—— `tx_type=adjustment` 契约:

- `POST /write/ledgers/{id}/accounts/{account_id}/balance-adjustment`:
  語意化端點,server 算出 `target_balance - 當下餘額` 差額寫成一筆
  `tx_type=adjustment` 交易,走一般交易寫權限(不是 owner-only)。
- `adjustment` 交易只能有 account_id,不能帶分類/轉帳對象/拆帳/退款/欠款/
  紅利回饋 —— write 層兜底校驗(create/update 兩條 fast path)。
- 帳戶餘額計算(`list_workspace_accounts`/`compute_account_balance`)要把
  `adjustment` 金額算進去。
- account_group 不能是餘額調整目標。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
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


def _register(client, email, client_type="app", device_id="d1"):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
            "device_id": device_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _login_web(client, email):
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
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
        json={"device_id": device_id, "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _latest_change_id(client, token, ledger_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _setup(email, ledger_id="L_ADJ1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    hdr_app = {"Authorization": f"Bearer {app_token}"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": ledger_id, "currency": "CNY"}, device_id=device)
    _push(client, hdr_app, ledger_id, "account", "acc1",
          {"syncId": "acc1", "name": "現金", "type": "cash", "currency": "CNY", "initialBalance": 1000.0},
          device_id=device)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, app_token, device, token, hdr, ledger_id


def _adjust(client, hdr, ledger_id, token, account_id="acc1", **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {"base_change_id": base, "target_balance": 1000.0}
    payload.update(overrides)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/{account_id}/balance-adjustment",
        headers=hdr, json=payload,
    )


def _account_balance(client, hdr, ledger_id, account_id="acc1"):
    r = client.get("/api/v1/read/workspace/accounts", headers=hdr, params={"ledger_id": ledger_id})
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()}
    return rows[account_id]["balance"]


# ---------------------------------------------------------------------------
# 語意化端點:算差額 → 建一筆 adjustment 交易
# ---------------------------------------------------------------------------


def test_balance_adjustment_creates_signed_diff_transaction():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj1@example.com")
    try:
        # 记账余额目前是 1000(initial_balance,无交易)。使用者核对后发现
        # 实际是 1200 → 差额应该是 +200。
        res = _adjust(client, hdr, ledger_id, token, target_balance=1200.0)
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        rows = {row["id"]: row for row in r.json()}
        tx = rows[tx_id]
        assert tx["tx_type"] == "adjustment"
        assert tx["amount"] == 200.0
        assert tx["account_id"] == "acc1"
        assert tx["category_id"] is None

        assert _account_balance(client, hdr, ledger_id) == 1200.0
    finally:
        client.close()


def test_balance_adjustment_negative_diff():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj2@example.com")
    try:
        res = _adjust(client, hdr, ledger_id, token, target_balance=850.0)
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        rows = {row["id"]: row for row in r.json()}
        assert rows[tx_id]["amount"] == -150.0
        assert _account_balance(client, hdr, ledger_id) == 850.0
    finally:
        client.close()


def test_balance_adjustment_accounts_for_existing_transactions():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj3@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr,
            json={
                "base_change_id": base, "tx_type": "expense", "amount": 300.0,
                "happened_at": _iso(), "account_id": "acc1", "account_name": "現金",
            },
        )
        assert tx.status_code == 200, tx.text
        # 当下余额是 1000 - 300 = 700。核对后实际是 750 → 差额 +50。
        res = _adjust(client, hdr, ledger_id, token, target_balance=750.0)
        assert res.status_code == 200, res.text
        adj_tx_id = res.json()["entity_id"]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        rows = {row["id"]: row for row in r.json()}
        assert rows[adj_tx_id]["amount"] == 50.0
        assert _account_balance(client, hdr, ledger_id) == 750.0
    finally:
        client.close()


def test_balance_adjustment_zero_diff_still_allowed_with_custom_note():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj4@example.com")
    try:
        res = _adjust(client, hdr, ledger_id, token, target_balance=1000.0, note="已核對，無差異")
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]
        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        rows = {row["id"]: row for row in r.json()}
        assert rows[tx_id]["amount"] == 0.0
        assert rows[tx_id]["note"] == "已核對，無差異"
    finally:
        client.close()


def test_balance_adjustment_rejects_account_group_target():
    client, _TS, app_tok, device, token, hdr, ledger_id = _setup("adj5@example.com")
    try:
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        _push(client, hdr_app, ledger_id, "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id=device)
        res = _adjust(client, hdr, ledger_id, token, account_id="acc-group", target_balance=100.0)
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_balance_adjustment_editor_role_allowed():
    """走一般交易寫權限(_TRANSACTION_WRITE_ROLES),不是 owner-only —— 用
    `_prepare_write` 的角色檢查直接驗證(editor 不會被 404/403 擋在
    `_prepare_write` 這一關)。account 解析走 `_resolve_account_display`
    (跟 `card_payment_ep`/manual-payout 同款既有模式,按 `current_user.id`
    查 user-global 帳戶),所以這裡讓 editor 在自己名下也建一份同 sync_id
    的帳戶,只用來驗證權限閘門本身不是 owner-only,不是驗證跨用戶帳戶解析
    (那是這幾個既有語意化端點共同的既有行為,不是本次改動範圍)。"""
    client, TS, app_tok, device, token, hdr, ledger_id = _setup("adj6owner@example.com")
    try:
        from src.models import Ledger, LedgerMember, User

        editor = _register(client, "adj6editor@example.com")
        editor_app_token, editor_device = editor["access_token"], editor["device_id"]
        editor_hdr_app = {"Authorization": f"Bearer {editor_app_token}"}
        _push(client, editor_hdr_app, ledger_id, "account", "acc1",
              {"syncId": "acc1", "name": "現金", "type": "cash", "currency": "CNY"},
              device_id=editor_device)
        editor_web = _login_web(client, "adj6editor@example.com")
        editor_token = editor_web["access_token"]
        editor_hdr = {"Authorization": f"Bearer {editor_token}"}

        with TS() as db:
            editor_user = db.scalar(select(User).where(User.email == "adj6editor@example.com"))
            ledger_row = db.scalar(select(Ledger).where(Ledger.external_id == ledger_id))
            db.add(LedgerMember(ledger_id=ledger_row.id, user_id=editor_user.id, role="editor"))
            db.commit()

        res = _adjust(client, editor_hdr, ledger_id, editor_token, target_balance=1300.0)
        assert res.status_code == 200, res.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 直接建 adjustment 交易的兜底校驗(不透過語意化端點)
# ---------------------------------------------------------------------------


def test_direct_adjustment_tx_requires_account_id():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj7@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr,
            json={"base_change_id": base, "tx_type": "adjustment", "amount": 50.0, "happened_at": _iso()},
        )
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_direct_adjustment_tx_rejects_category():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj8@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr,
            json={
                "base_change_id": base, "tx_type": "adjustment", "amount": 50.0,
                "happened_at": _iso(), "account_id": "acc1", "category_name": "餐飲",
            },
        )
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_direct_adjustment_tx_valid_payload_succeeds():
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj9@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr,
            json={
                "base_change_id": base, "tx_type": "adjustment", "amount": -25.0,
                "happened_at": _iso(), "account_id": "acc1", "account_name": "現金",
                "note": "手動核對",
            },
        )
        assert res.status_code == 200, res.text
        assert _account_balance(client, hdr, ledger_id) == 975.0
    finally:
        client.close()


def test_update_transaction_to_adjustment_rejects_splits():
    """修改既有交易的 tx_type 变成 adjustment 时,merge 后的最终状态若还带
    着 splits(改之前是拆帳交易),也要被挡。"""
    client, _TS, _app_tok, _device, token, hdr, ledger_id = _setup("adj10@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr,
            json={
                "base_change_id": base, "tx_type": "expense", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc1", "account_name": "現金",
                "splits": [
                    {"category_id": "c1", "category_name": "餐飲", "amount": 60.0},
                    {"category_id": "c2", "category_name": "交通", "amount": 40.0},
                ],
            },
        )
        assert tx.status_code == 200, tx.text
        tx_id = tx.json()["entity_id"]

        base2 = _latest_change_id(client, token, ledger_id)
        upd = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}", headers=hdr,
            json={"base_change_id": base2, "tx_type": "adjustment"},
        )
        assert upd.status_code == 400, upd.text
    finally:
        client.close()
