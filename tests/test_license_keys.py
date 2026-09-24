"""授權金鑰 + App 最低可同步版本(docs/LICENSE_KEYS.md)的 server 端契約測試。

覆蓋:
1. 沒有授權的一般帳號:所有需登入的端點(read/sync/write/profile)一律 402,
   只有 `/license/*`、`/auth/*` 可用;admin 免金鑰。
2. 啟用金鑰:格式容錯、一把金鑰只能啟用一次、效期從啟用當下起算一年、
   已有授權時新金鑰接在剩餘天數後面、撤銷立即生效、速率限制。
3. 管理後台:非 admin 拒絕、產生/列表/撤銷/刪除未使用金鑰。
4. PAT/MCP、WebSocket 兩條不走 `get_current_user` 的入口也被擋。
5. App 最低可同步版本:沒帶 `X-App-Version`(舊版 App)或版本過舊 → 426;
   web client 不受影響;公開 `/app-version/latest` 回傳門檻。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from src.database import Base, get_db
from src.main import app
from src.models import LicenseKey, User
from src.services import license as license_service

_TEST_SESSION: sessionmaker | None = None


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    global _TEST_SESSION
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _TEST_SESSION = TS

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    # WS / MCP middleware 直接用 SessionLocal,不走 get_db,一起指到測試 DB。
    monkeypatch.setattr("src.routers.ws.SessionLocal", TS)
    monkeypatch.setattr("src.mcp.auth.SessionLocal", TS)
    monkeypatch.setenv("LICENSE_ENFORCEMENT", "true")
    license_service.reset_activate_rate_limit()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        license_service.reset_activate_rate_limit()


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login(client: TestClient, email: str, client_type: str = "web") -> str:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _make_admin(email: str) -> None:
    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        user = db.query(User).filter(User.email == email).one()
        user.is_admin = True
        db.commit()


def _admin_token(client: TestClient, email: str = "admin@t.com") -> str:
    _register(client, email)
    _make_admin(email)
    return _login(client, email)


def _h(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def _create_keys(client: TestClient, admin: str, **body) -> list[dict]:
    res = client.post("/api/v1/admin/licenses", headers=_h(admin), json=body or {"count": 1})
    assert res.status_code == 200, res.text
    return res.json()["items"]


def _as_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1. 沒授權就擋
# --------------------------------------------------------------------------- #


def test_unlicensed_user_is_blocked_everywhere_but_license_and_auth(client):
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")

    for path in (
        "/api/v1/sync/ledgers",
        "/api/v1/sync/pull?since=0",
        "/api/v1/read/ledgers",
        "/api/v1/profile/me",
        "/api/v1/devices",
        "/api/v1/profile/pats",
    ):
        res = client.get(path, headers=_h(token))
        assert res.status_code == 402, (path, res.text)
        assert res.json()["error"]["code"] == "LICENSE_REQUIRED"

    res = client.post("/api/v1/write/ledgers", headers=_h(token), json={"ledger_name": "x"})
    assert res.status_code == 402, res.text

    res = client.get("/api/v1/license/status", headers=_h(token))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["licensed"] is False
    assert body["expires_at"] is None
    assert body["offline_grace_days"] == 7


def test_admin_is_exempt(client):
    admin = _admin_token(client)
    assert client.get("/api/v1/sync/ledgers", headers=_h(admin)).status_code == 200
    body = client.get("/api/v1/license/status", headers=_h(admin)).json()
    assert body["licensed"] is True
    assert body["exempt"] is True


def test_existing_token_is_blocked_after_enforcement(client, monkeypatch):
    """功能上線前就發出去的 token 一樣要被擋(檢查在每次請求,不在發 token 時)。"""
    monkeypatch.setenv("LICENSE_ENFORCEMENT", "false")
    _register(client, "old@t.com")
    token = _login(client, "old@t.com")
    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 200
    monkeypatch.setenv("LICENSE_ENFORCEMENT", "true")
    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 402


def test_enforcement_env_switch_ignored_outside_development(client, monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("LICENSE_ENFORCEMENT", "false")
    monkeypatch.setattr(get_settings(), "app_env", "production")
    assert license_service.is_enforcement_enabled() is True


# --------------------------------------------------------------------------- #
# 2. 啟用金鑰
# --------------------------------------------------------------------------- #


def test_activate_key_grants_one_year_and_unblocks(client):
    admin = _admin_token(client)
    key = _create_keys(client, admin, count=1)[0]["key"]
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")

    # 小寫 + 空白 + 沒連字號也要認得
    messy = key.lower().replace("-", " ")
    before = datetime.now(timezone.utc)
    res = client.post("/api/v1/license/activate", headers=_h(token), json={"key": messy})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["licensed"] is True
    expires = _as_utc(body["expires_at"])
    assert timedelta(days=364, hours=23) < expires - before < timedelta(days=365, minutes=5)

    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 200


def test_key_can_only_be_redeemed_once(client):
    admin = _admin_token(client)
    key = _create_keys(client, admin)[0]["key"]
    _register(client, "a@t.com")
    _register(client, "b@t.com")
    ta, tb = _login(client, "a@t.com"), _login(client, "b@t.com")

    assert client.post("/api/v1/license/activate", headers=_h(ta), json={"key": key}).status_code == 200
    res = client.post("/api/v1/license/activate", headers=_h(tb), json={"key": key})
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "LICENSE_KEY_ALREADY_REDEEMED"
    # 同一個帳號重複輸入同一把也不能再延長
    assert client.post("/api/v1/license/activate", headers=_h(ta), json={"key": key}).status_code == 409
    assert client.get("/api/v1/sync/ledgers", headers=_h(tb)).status_code == 402


def test_invalid_and_unknown_keys(client):
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    res = client.post("/api/v1/license/activate", headers=_h(token), json={"key": "hello"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "LICENSE_KEY_INVALID"
    res = client.post(
        "/api/v1/license/activate", headers=_h(token), json={"key": license_service.generate_key()}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "LICENSE_KEY_NOT_FOUND"


def test_second_key_extends_from_current_expiry(client):
    admin = _admin_token(client)
    k1, k2 = (k["key"] for k in _create_keys(client, admin, count=2))
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    first = _as_utc(
        client.post("/api/v1/license/activate", headers=_h(token), json={"key": k1}).json()["expires_at"]
    )
    second = _as_utc(
        client.post("/api/v1/license/activate", headers=_h(token), json={"key": k2}).json()["expires_at"]
    )
    assert abs((second - first) - timedelta(days=365)) < timedelta(seconds=5)


def test_expired_license_blocks_and_new_key_restarts_from_now(client):
    admin = _admin_token(client)
    k1, k2 = (k["key"] for k in _create_keys(client, admin, count=2))
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    assert client.post("/api/v1/license/activate", headers=_h(token), json={"key": k1}).status_code == 200

    assert _TEST_SESSION is not None
    with _TEST_SESSION() as db:
        row = db.query(LicenseKey).filter(LicenseKey.key == k1).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.commit()

    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 402
    status_body = client.get("/api/v1/license/status", headers=_h(token)).json()
    assert status_body["licensed"] is False
    assert status_body["expires_at"] is not None  # 顯示「已於 X 過期」用

    # 過期的金鑰不能重複用,要換一把新的
    assert client.post("/api/v1/license/activate", headers=_h(token), json={"key": k1}).status_code == 409
    before = datetime.now(timezone.utc)
    body = client.post("/api/v1/license/activate", headers=_h(token), json={"key": k2}).json()
    assert body["licensed"] is True
    assert _as_utc(body["expires_at"]) - before > timedelta(days=364)


def test_revoke_takes_effect_immediately(client):
    admin = _admin_token(client)
    item = _create_keys(client, admin)[0]
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    client.post("/api/v1/license/activate", headers=_h(token), json={"key": item["key"]})
    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 200

    res = client.post(f"/api/v1/admin/licenses/{item['id']}/revoke", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "revoked"
    assert client.get("/api/v1/sync/ledgers", headers=_h(token)).status_code == 402


def test_revoked_unused_key_cannot_be_redeemed(client):
    admin = _admin_token(client)
    item = _create_keys(client, admin)[0]
    client.post(f"/api/v1/admin/licenses/{item['id']}/revoke", headers=_h(admin))
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    res = client.post("/api/v1/license/activate", headers=_h(token), json={"key": item["key"]})
    assert res.status_code == 410
    assert res.json()["error"]["code"] == "LICENSE_KEY_REVOKED"


def test_activate_rate_limited(client):
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    for _ in range(10):
        client.post("/api/v1/license/activate", headers=_h(token), json={"key": license_service.generate_key()})
    res = client.post("/api/v1/license/activate", headers=_h(token), json={"key": license_service.generate_key()})
    assert res.status_code == 429


def test_custom_duration(client):
    admin = _admin_token(client)
    key = _create_keys(client, admin, count=1, duration_days=30, note="試用")[0]
    assert key["duration_days"] == 30
    assert key["note"] == "試用"
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    before = datetime.now(timezone.utc)
    body = client.post("/api/v1/license/activate", headers=_h(token), json={"key": key["key"]}).json()
    assert timedelta(days=29, hours=23) < _as_utc(body["expires_at"]) - before < timedelta(days=30, minutes=5)


# --------------------------------------------------------------------------- #
# 3. 管理後台
# --------------------------------------------------------------------------- #


def test_admin_endpoints_require_admin(client):
    admin = _admin_token(client)
    key = _create_keys(client, admin)[0]["key"]
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    client.post("/api/v1/license/activate", headers=_h(token), json={"key": key})
    # 有授權的一般使用者也不能進管理後台
    assert client.get("/api/v1/admin/licenses", headers=_h(token)).status_code == 403
    assert client.post("/api/v1/admin/licenses", headers=_h(token), json={"count": 5}).status_code == 403


def test_admin_list_filters_and_delete(client):
    admin = _admin_token(client)
    items = _create_keys(client, admin, count=3, note="batch-1")
    assert all(i["status"] == "unused" for i in items)
    assert len({i["key"] for i in items}) == 3
    assert all(license_service.normalize_key(i["key"]) == i["key"] for i in items)

    _register(client, "buyer@t.com")
    token = _login(client, "buyer@t.com")
    client.post("/api/v1/license/activate", headers=_h(token), json={"key": items[0]["key"]})

    res = client.get("/api/v1/admin/licenses?status=active", headers=_h(admin)).json()
    assert res["total"] == 1
    assert res["items"][0]["redeemed_by_email"] == "buyer@t.com"
    assert client.get("/api/v1/admin/licenses?status=unused", headers=_h(admin)).json()["total"] == 2
    assert client.get("/api/v1/admin/licenses?q=buyer", headers=_h(admin)).json()["total"] == 1

    # 已啟用的金鑰不能刪(要用撤銷);未使用的可以刪
    assert client.delete(f"/api/v1/admin/licenses/{items[0]['id']}", headers=_h(admin)).status_code == 409
    assert client.delete(f"/api/v1/admin/licenses/{items[1]['id']}", headers=_h(admin)).status_code == 200
    assert client.get("/api/v1/admin/licenses", headers=_h(admin)).json()["total"] == 2


# --------------------------------------------------------------------------- #
# 4. 其它入口:PAT / MCP、WebSocket
# --------------------------------------------------------------------------- #


def test_mcp_pat_blocked_without_license(client, monkeypatch):
    monkeypatch.setenv("LICENSE_ENFORCEMENT", "false")
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    res = client.post(
        "/api/v1/profile/pats",
        headers=_h(token),
        json={"name": "mcp", "scopes": ["mcp:read"], "expires_in_days": 30},
    )
    assert res.status_code == 201, res.text
    pat = res.json()["token"]
    monkeypatch.setenv("LICENSE_ENFORCEMENT", "true")

    res = client.post(
        "/api/v1/mcp",
        headers={"Authorization": f"Bearer {pat}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert res.status_code == 402, res.text


def test_websocket_blocked_without_license(client):
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.receive_text()
    assert exc.value.code == 4402


def test_websocket_allowed_with_license(client):
    admin = _admin_token(client)
    key = _create_keys(client, admin)[0]["key"]
    _register(client, "u@t.com")
    token = _login(client, "u@t.com")
    client.post("/api/v1/license/activate", headers=_h(token), json={"key": key})
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_text('{"type":"ping"}')
        assert ws.receive_text() == '{"type":"pong"}'


# --------------------------------------------------------------------------- #
# 5. App 最低可同步版本
# --------------------------------------------------------------------------- #


def _set_min_version(client: TestClient, admin: str, value: str) -> None:
    res = client.put("/api/v1/admin/app-version-config", headers=_h(admin), json={"min_sync_version": value})
    assert res.status_code == 200, res.text
    assert res.json()["min_sync_version"] == (value.strip() or None)


def test_min_sync_version_blocks_old_and_headerless_app(client):
    admin = _admin_token(client)
    _set_min_version(client, admin, "3.6.0")
    # admin 的 App 一樣要受版本門檻限制(相容性問題,不是授權問題)
    app_token = _login(client, "admin@t.com", client_type="app")

    res = client.get("/api/v1/sync/ledgers", headers=_h(app_token))
    assert res.status_code == 426, res.text
    body = res.json()
    assert body["error"]["code"] == "APP_VERSION_TOO_OLD"
    assert body["min_version"] == "3.6.0"
    assert "3.6.0" in body["detail"]

    res = client.get("/api/v1/sync/ledgers", headers=_h(app_token, **{"X-App-Version": "3.5.7"}))
    assert res.status_code == 426
    for ok in ("3.6.0", "3.6.0+2", "3.10.1", "4.0"):
        res = client.get("/api/v1/sync/ledgers", headers=_h(app_token, **{"X-App-Version": ok}))
        assert res.status_code == 200, (ok, res.text)

    # web client 不帶版本也不受影響
    assert client.get("/api/v1/sync/ledgers", headers=_h(admin)).status_code == 200


def test_version_gate_runs_before_license_for_app(client):
    admin = _admin_token(client)
    _set_min_version(client, admin, "3.6.0")
    _register(client, "u@t.com")
    token = _login(client, "u@t.com", client_type="app")
    # 舊版 App 先看到「版本過舊」,不是「需要授權」
    assert client.get("/api/v1/license/status", headers=_h(token)).status_code == 426
    res = client.get("/api/v1/license/status", headers=_h(token, **{"X-App-Version": "3.6.0"}))
    assert res.status_code == 200
    assert res.json()["licensed"] is False


def test_old_app_can_still_report_version_and_refresh(client):
    admin = _admin_token(client)
    _set_min_version(client, admin, "3.6.0")
    reg = _register(client, "u@t.com")
    token = reg["access_token"]
    res = client.post(
        f"/api/v1/devices/{reg['device_id']}/report-version",
        headers=_h(token),
        json={"app_version": "3.5.7 1"},
    )
    assert res.status_code != 426, res.text
    assert res.status_code != 402, res.text
    res = client.post("/api/v1/auth/refresh", json={"refresh_token": reg["refresh_token"]})
    assert res.status_code == 200, res.text


def test_websocket_rejects_old_app(client):
    admin = _admin_token(client)
    _set_min_version(client, admin, "3.6.0")
    app_token = _login(client, "admin@t.com", client_type="app")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws?token={app_token}") as ws:
            ws.receive_text()
    assert exc.value.code == 4426
    with client.websocket_connect(f"/ws?token={app_token}&app_version=3.6.0") as ws:
        ws.send_text('{"type":"ping"}')
        assert ws.receive_text() == '{"type":"pong"}'


def test_min_sync_version_validation_and_public_endpoint(client):
    admin = _admin_token(client)
    res = client.put("/api/v1/admin/app-version-config", headers=_h(admin), json={"min_sync_version": "abc"})
    assert res.status_code == 400
    assert client.get("/api/v1/app-version/latest").json()["min_sync_version"] is None
    _set_min_version(client, admin, "3.6.0")
    assert client.get("/api/v1/app-version/latest").json()["min_sync_version"] == "3.6.0"
    _set_min_version(client, admin, "")
    assert client.get("/api/v1/app-version/latest").json()["min_sync_version"] is None
    app_token = _login(client, "admin@t.com", client_type="app")
    assert client.get("/api/v1/sync/ledgers", headers=_h(app_token)).status_code == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3.5.7", (3, 5, 7)),
        ("3.5.7+1", (3, 5, 7)),
        ("3.5.7 1", (3, 5, 7)),
        (" 3.6 ", (3, 6)),
        ("", None),
        (None, None),
        ("v3.6", None),
    ],
)
def test_parse_version(raw, expected):
    assert license_service.parse_version(raw) == expected


def test_version_compare_pads_zeros():
    assert not license_service.is_version_below((3, 6), (3, 6, 0))
    assert license_service.is_version_below((3, 5, 9), (3, 6))
    assert not license_service.is_version_below((3, 10), (3, 9, 9))
