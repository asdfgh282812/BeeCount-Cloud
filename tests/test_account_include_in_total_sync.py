"""帳戶「納入總餘額」開關(Phase 18,docs/PH17_USER_FEEDBACK_2026-08_SD.md
§Phase 18)的 merge 契約測試,比照 tests/test_account_hidden_sync.py /
tests/test_account_swipesmart_card_id_sync.py 的既有風格:

- mobile push payload 帶 `includeInTotal` → 落
  user_account_projection.include_in_total
- 全新帳戶不帶該鍵時預設 True(納入)
- partial update(後續 push 只改 name、不帶該鍵)時保持原值,不被靜默冲成
  預設值(CLAUDE.md L74-80 硬門檻)
- snapshot_builder 懶構建的 snapshot 無條件帶這個欄位(NOT NULL 布尔列,同
  hidden/autoPayEnabled)
- /read/ledgers/{id}/accounts(ReadAccountOut)回傳這個欄位
- web POST/PATCH 端點可寫入,PATCH 不帶該鍵時不冲掉已有設置
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User, UserAccountProjection


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


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": "pytest",
            "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


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


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def test_push_account_defaults_include_in_total_true_when_omitted():
    client, TS = _make_client()
    try:
        tok = _login(client, "inc1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "現金", "type": "cash", "currency": "CNY"})

        row = _account_row(TS, "inc1@t.com", "acc-1")
        assert row.include_in_total is True
    finally:
        app.dependency_overrides.clear()


def test_push_account_persists_include_in_total_false():
    client, TS = _make_client()
    try:
        tok = _login(client, "inc2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "定存", "type": "savings", "currency": "CNY",
               "includeInTotal": False})

        row = _account_row(TS, "inc2@t.com", "acc-2")
        assert row.include_in_total is False
    finally:
        app.dependency_overrides.clear()


def test_account_include_in_total_partial_update_keeps_existing_field():
    """**merge 契約(CLAUDE.md L74-80 硬門檻)**:先 push 一條帶
    includeInTotal=False 的帳戶,再 push 一條只改 name、不帶該鍵的 partial
    update —— include_in_total 必須仍保留原值 False,不能被靜默冲成預設值
    True。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "inc3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "定存", "type": "savings", "currency": "CNY",
               "includeInTotal": False})
        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "定存改名"})

        row = _account_row(TS, "inc3@t.com", "acc-3")
        assert row.name == "定存改名"
        assert row.include_in_total is False, "partial update 不帶該鍵時不能冲掉已有設置"
    finally:
        app.dependency_overrides.clear()


def test_snapshot_builder_keeps_include_in_total():
    from src.models import Ledger
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        tok = _login(client, "inc4@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-excluded",
              {"syncId": "acc-excluded", "name": "定存", "type": "savings",
               "currency": "CNY", "includeInTotal": False})
        _push(client, hdr, "lg1", "account", "acc-included",
              {"syncId": "acc-included", "name": "現金", "type": "cash", "currency": "CNY"})

        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        by_id = {acc["syncId"]: acc for acc in snap["accounts"]}
        assert by_id["acc-excluded"]["includeInTotal"] is False
        assert by_id["acc-included"]["includeInTotal"] is True
    finally:
        app.dependency_overrides.clear()


def test_read_ledger_accounts_expose_include_in_total():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "inc5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "inc5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-r1",
              {"syncId": "acc-r1", "name": "定存", "type": "savings",
               "currency": "CNY", "includeInTotal": False}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        acc = next(x for x in r.json() if x["id"] == "acc-r1")
        assert acc["include_in_total"] is False
    finally:
        app.dependency_overrides.clear()


def test_web_create_account_include_in_total_defaults_true():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "incw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "incw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw1", "ledger", "lgw1",
              {"syncId": "lgw1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgw1/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "現金", "account_type": "cash", "currency": "CNY"},
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]

        row = _account_row(TS, "incw1@t.com", account_id)
        assert row.include_in_total is True
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_include_in_total_omitted_keeps_existing():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "incw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "incw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw2", "ledger", "lgw2",
              {"syncId": "lgw2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw2", "account", "acc-w2",
              {"syncId": "acc-w2", "name": "定存", "type": "savings", "currency": "CNY",
               "includeInTotal": False}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-w2",
            headers=hdr_web,
            json={"base_change_id": 0, "note": "备注"},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "incw2@t.com", "acc-w2")
        assert row.include_in_total is False, "web update 不帶該鍵時不能冲掉已有設置"
        assert row.note == "备注"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_include_in_total_can_toggle():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "incw3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "incw3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw3", "ledger", "lgw3",
              {"syncId": "lgw3", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw3", "account", "acc-w3",
              {"syncId": "acc-w3", "name": "定存", "type": "savings", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw3/accounts/acc-w3",
            headers=hdr_web,
            json={"base_change_id": 0, "include_in_total": False},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, "incw3@t.com", "acc-w3")
        assert row.include_in_total is False

        r2 = client.patch(
            "/api/v1/write/ledgers/lgw3/accounts/acc-w3",
            headers=hdr_web,
            json={"base_change_id": 0, "include_in_total": True},
        )
        assert r2.status_code == 200, r2.text
        row2 = _account_row(TS, "incw3@t.com", "acc-w3")
        assert row2.include_in_total is True
    finally:
        app.dependency_overrides.clear()
