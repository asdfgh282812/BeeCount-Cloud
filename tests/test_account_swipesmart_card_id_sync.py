"""帳戶 <-> SwipeSmart 卡片對照欄位(Phase 14,
docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md §6 第 7 步)的 merge 契約測試,
比照 tests/test_account_hidden_sync.py 的既有風格:

- mobile push payload 帶 `swipesmartCardId` → 落
  user_account_projection.swipesmart_card_id
- partial update(後續 push 只改 name、不帶該鍵)時保持原值,不被靜默清空
  (CLAUDE.md L74-80 硬門檻)
- partial update **顯式**帶空字串(使用者主動解除對照)時正常清空
- snapshot_builder 懶構建的 snapshot 帶這個欄位(否則重裝/新設備丟對照)
- /read/ledgers/{id}/accounts(ReadAccountOut)回傳這個欄位
- web PATCH 端點可寫入/清空,且不帶該鍵時不冲掉已有對照
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


def test_push_account_persists_swipesmart_card_id():
    client, TS = _make_client()
    try:
        tok = _login(client, "ssm1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"})

        row = _account_row(TS, "ssm1@t.com", "acc-1")
        assert row.swipesmart_card_id == "CARD_A"
    finally:
        app.dependency_overrides.clear()


def test_account_swipesmart_card_id_partial_update_keeps_existing_field():
    """**merge 契約(CLAUDE.md L74-80 硬門檻)**:先 push 一條帶
    swipesmartCardId 的帳戶,再 push 一條只改 name、不帶該鍵的 partial
    update —— swipesmart_card_id 必須仍保留原值,不能被靜默冲成 null。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "ssm2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"})
        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "信用卡改名"})

        row = _account_row(TS, "ssm2@t.com", "acc-2")
        assert row.name == "信用卡改名"
        assert row.swipesmart_card_id == "CARD_A", "partial update 不帶該鍵時不能冲掉已有對照"
    finally:
        app.dependency_overrides.clear()


def test_account_swipesmart_card_id_can_explicitly_clear():
    client, TS = _make_client()
    try:
        tok = _login(client, "ssm3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"})
        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "信用卡", "swipesmartCardId": ""})

        row = _account_row(TS, "ssm3@t.com", "acc-3")
        assert row.swipesmart_card_id is None
    finally:
        app.dependency_overrides.clear()


def test_snapshot_builder_keeps_swipesmart_card_id():
    from src.models import Ledger
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        tok = _login(client, "ssm4@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-mapped",
              {"syncId": "acc-mapped", "name": "信用卡", "type": "credit_card",
               "currency": "CNY", "swipesmartCardId": "CARD_A"})
        _push(client, hdr, "lg1", "account", "acc-unmapped",
              {"syncId": "acc-unmapped", "name": "現金", "type": "cash", "currency": "CNY"})

        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        by_id = {acc["syncId"]: acc for acc in snap["accounts"]}
        assert by_id["acc-mapped"]["swipesmartCardId"] == "CARD_A"
        assert "swipesmartCardId" not in by_id["acc-unmapped"]
    finally:
        app.dependency_overrides.clear()


def test_read_ledger_accounts_expose_swipesmart_card_id():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ssm5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ssm5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-r1",
              {"syncId": "acc-r1", "name": "信用卡", "type": "credit_card",
               "currency": "CNY", "swipesmartCardId": "CARD_A"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        acc = next(x for x in r.json() if x["id"] == "acc-r1")
        assert acc["swipesmart_card_id"] == "CARD_A"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_swipesmart_card_id_omitted_keeps_existing():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ssmw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ssmw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw1", "ledger", "lgw1",
              {"syncId": "lgw1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw1", "account", "acc-w1",
              {"syncId": "acc-w1", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw1/accounts/acc-w1",
            headers=hdr_web,
            json={"base_change_id": 0, "note": "备注"},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "ssmw1@t.com", "acc-w1")
        assert row.swipesmart_card_id == "CARD_A", "web update 不帶該鍵時不能冲掉已有對照"
        assert row.note == "备注"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_swipesmart_card_id_can_set_and_clear():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "ssmw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "ssmw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw2", "ledger", "lgw2",
              {"syncId": "lgw2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw2", "account", "acc-w2",
              {"syncId": "acc-w2", "name": "信用卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-w2",
            headers=hdr_web,
            json={"base_change_id": 0, "swipesmart_card_id": "CARD_B"},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, "ssmw2@t.com", "acc-w2")
        assert row.swipesmart_card_id == "CARD_B"

        r2 = client.patch(
            "/api/v1/write/ledgers/lgw2/accounts/acc-w2",
            headers=hdr_web,
            json={"base_change_id": 0, "swipesmart_card_id": ""},
        )
        assert r2.status_code == 200, r2.text
        row2 = _account_row(TS, "ssmw2@t.com", "acc-w2")
        assert row2.swipesmart_card_id is None
    finally:
        app.dependency_overrides.clear()
