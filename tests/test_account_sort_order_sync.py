"""帳戶清單拖曳排序(App 端 docs/changes/2026-09-05-account-drag-reorder.md)
Cloud 端契約 —— 比照 tests/test_account_include_in_total_sync.py 的既有風格:

- mobile push payload 帶 `sortOrder` → 落 user_account_projection.sort_order
- partial update(後續 push 只改 name、不帶該鍵)時保持原值,不被靜默沖掉
  (CLAUDE.md L74-80 硬門檻)
- `/read/ledgers/{id}/accounts` 依 sort_order 排序(不是名稱字母序),None
  (舊資料)排到最後,並回傳這個欄位
- web PATCH 單筆可寫入,不帶該鍵時不沖掉已有設置
- 新增的批次排序端點 `POST .../accounts/reorder` 一次改多筆,且會經正常
  sync/pull 傳播到其它裝置
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


def test_push_account_persists_sort_order():
    client, TS = _make_client()
    try:
        tok = _login(client, "sort1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "現金", "type": "cash", "currency": "CNY",
               "sortOrder": 3})

        row = _account_row(TS, "sort1@t.com", "acc-1")
        assert row.sort_order == 3
    finally:
        app.dependency_overrides.clear()


def test_push_account_sort_order_defaults_null_when_omitted():
    client, TS = _make_client()
    try:
        tok = _login(client, "sort2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "定存", "type": "savings", "currency": "CNY"})

        row = _account_row(TS, "sort2@t.com", "acc-2")
        assert row.sort_order is None
    finally:
        app.dependency_overrides.clear()


def test_account_sort_order_partial_update_keeps_existing_field():
    """**merge 契約(CLAUDE.md L74-80 硬門檻)**:先 push 一條帶 sortOrder=5 的
    帳戶,再 push 一條只改 name、不帶該鍵的 partial update —— sort_order 必須
    仍保留原值 5,不能被靜默沖掉。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "sort3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "定存", "type": "savings", "currency": "CNY",
               "sortOrder": 5})
        _push(client, hdr, "lg1", "account", "acc-3",
              {"syncId": "acc-3", "name": "定存改名"})

        row = _account_row(TS, "sort3@t.com", "acc-3")
        assert row.name == "定存改名"
        assert row.sort_order == 5, "partial update 不帶該鍵時不能沖掉已有設置"
    finally:
        app.dependency_overrides.clear()


def test_read_ledger_accounts_ordered_by_sort_order_not_name():
    """故意讓 sort_order 順序跟名稱字母序相反,證明 read API 是照 sort_order
    排、不是照名稱排。缺 sort_order(None)的舊資料排到最後。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "sort4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "sort4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        # 名稱字母序:A, B, C;sortOrder 故意反過來,No-sort-order 的排最後。
        _push(client, hdr_app, "lg1", "account", "acc-a",
              {"syncId": "acc-a", "name": "A", "type": "cash", "currency": "CNY",
               "sortOrder": 2}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-b",
              {"syncId": "acc-b", "name": "B", "type": "cash", "currency": "CNY",
               "sortOrder": 0}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-c",
              {"syncId": "acc-c", "name": "C", "type": "cash", "currency": "CNY",
               "sortOrder": 1}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-legacy",
              {"syncId": "acc-legacy", "name": "AAA最先", "type": "cash",
               "currency": "CNY"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        ids = [a["id"] for a in r.json()]
        assert ids == ["acc-b", "acc-c", "acc-a", "acc-legacy"]

        by_id = {a["id"]: a for a in r.json()}
        assert by_id["acc-b"]["sort_order"] == 0
        assert by_id["acc-legacy"]["sort_order"] is None
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_sort_order_omitted_keeps_existing():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "sortw1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "sortw1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw1", "ledger", "lgw1",
              {"syncId": "lgw1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgw1", "account", "acc-w1",
              {"syncId": "acc-w1", "name": "定存", "type": "savings", "currency": "CNY",
               "sortOrder": 7}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgw1/accounts/acc-w1",
            headers=hdr_web,
            json={"base_change_id": 0, "note": "备注"},
        )
        assert r.status_code == 200, r.text

        row = _account_row(TS, "sortw1@t.com", "acc-w1")
        assert row.sort_order == 7, "web update 不帶該鍵時不能沖掉已有設置"
        assert row.note == "备注"
    finally:
        app.dependency_overrides.clear()


def test_web_reorder_accounts_batch_updates_multiple_and_syncs_to_app():
    """新增的批次排序端點:一次呼叫改多筆 sort_order,且改動要經正常
    sync/pull 傳播到其它裝置(App 端下拉同步靠這個生效)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "sortw2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "sortw2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgw2", "ledger", "lgw2",
              {"syncId": "lgw2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        for sync_id, name in [("acc-x", "X"), ("acc-y", "Y"), ("acc-z", "Z")]:
            _push(client, hdr_app, "lgw2", "account", sync_id,
                  {"syncId": sync_id, "name": name, "type": "cash", "currency": "CNY"},
                  device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgw2/accounts/reorder",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "items": [
                    {"account_id": "acc-z", "sort_order": 0},
                    {"account_id": "acc-x", "sort_order": 1},
                    {"account_id": "acc-y", "sort_order": 2},
                ],
            },
        )
        assert r.status_code == 200, r.text

        assert _account_row(TS, "sortw2@t.com", "acc-z").sort_order == 0
        assert _account_row(TS, "sortw2@t.com", "acc-x").sort_order == 1
        assert _account_row(TS, "sortw2@t.com", "acc-y").sort_order == 2

        # App 端走正常 /sync/pull 收敛(增量同步,不带 device_id 免得被自己
        # 的 push 过滤掉)。
        r2 = client.get("/api/v1/sync/pull?since=0", headers=hdr_app)
        assert r2.status_code == 200, r2.text
        sort_orders = {
            c["entity_sync_id"]: c["payload"].get("sortOrder")
            for c in r2.json()["changes"]
            if c["entity_type"] == "account"
        }
        assert sort_orders["acc-z"] == 0
        assert sort_orders["acc-x"] == 1
        assert sort_orders["acc-y"] == 2
    finally:
        app.dependency_overrides.clear()
