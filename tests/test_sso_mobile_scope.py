"""回归测试:SSO 回调对 mobile(App)登录要发 app_write scope 的 token,
不能像 web 登录一样只给 web_read/web_write/ops_write。

Bug 复现路径:`sso_callback` 之前无条件 `_issue_tokens(..., client_type="web")`,
不管 `state_payload["mobile"]` 是不是 True —— App 端走 SSO 登入后,
access_token 只有 web scope,没有 app_write。`/sync/push` 只认 app_write,
于是 App 新增的数据推不上服务器(403 Insufficient scope);`/sync/pull` 因为
接受 app_write OR web_read 两者之一,所以远端改动仍然能拉下来 —— 表现为
「新增交易同步失败,但下拉能看到别处改的数据」这种单向症状。
"""

from urllib.parse import parse_qsl, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.routers import auth as auth_router


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _fake_sso_callback(monkeypatch, *, mobile: bool, sso_subject: str, email: str):
    """monkeypatch 掉真正打网络的 oidc.exchange_code / verify_id_token,
    直接构造一份合法 state(复用 auth.py 自己的 `_encode_sso_state`),
    模拟 IdP 换 code 成功回调。"""
    monkeypatch.setattr(
        auth_router.oidc,
        "exchange_code",
        lambda *, code, redirect_uri: {"id_token": "dummy-id-token"},
    )
    monkeypatch.setattr(
        auth_router.oidc,
        "verify_id_token",
        lambda id_token: {"sub": sso_subject, "email": email},
    )
    return auth_router._encode_sso_state(
        device_id="pytest-device-1",
        device_name="pytest-device",
        platform="ios" if mobile else "web",
        app_version="1.0.0",
        os_version="18.0",
        device_model="iPhone17,1",
        redirect_path="/app/overview",
        mobile=mobile,
    )


def _access_token_from_redirect(location: str, *, expect_scheme: str) -> str:
    parsed = urlparse(location)
    assert f"{parsed.scheme}://{parsed.netloc}" == expect_scheme or location.startswith(
        expect_scheme
    )
    fragment = dict(parse_qsl(parsed.fragment))
    assert "access_token" in fragment
    return fragment["access_token"]


def test_sso_mobile_login_gets_app_write_scope_for_push(monkeypatch) -> None:
    client = _make_client()
    try:
        state = _fake_sso_callback(
            monkeypatch,
            mobile=True,
            sso_subject="sso-subject-mobile",
            email="sso-mobile@example.com",
        )

        callback = client.get(
            "/api/v1/auth/sso/callback",
            params={"code": "dummy-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        location = callback.headers["location"]
        assert location.startswith("beecount://auth-callback#")
        access_token = _access_token_from_redirect(
            location, expect_scheme="beecount://auth-callback"
        )

        # 核心断言:mobile SSO 登录拿到的 token 必须能推 sync/push
        # (app_write),而不是只有 web scope。
        push = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "device_id": "pytest-device-1",
                "changes": [
                    {
                        "ledger_id": "ledger-sso-mobile",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-sso-mobile",
                        "action": "upsert",
                        "payload": {
                            "content": (
                                '{"ledgerName":"SSO Mobile","currency":"CNY","count":0,'
                                '"items":[],"accounts":[],"categories":[],"tags":[]}'
                            )
                        },
                        "updated_at": "2026-08-14T00:00:00+00:00",
                    }
                ],
            },
        )
        assert push.status_code == 200, push.text
    finally:
        app.dependency_overrides.clear()


def test_sso_web_login_still_gets_web_scope_not_app_write(monkeypatch) -> None:
    client = _make_client()
    try:
        state = _fake_sso_callback(
            monkeypatch,
            mobile=False,
            sso_subject="sso-subject-web",
            email="sso-web@example.com",
        )

        callback = client.get(
            "/api/v1/auth/sso/callback",
            params={"code": "dummy-code", "state": state},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        location = callback.headers["location"]
        assert "/login/sso-complete#" in location
        access_token = _access_token_from_redirect(
            location, expect_scheme=location.split("/login/", 1)[0]
        )

        # web 登录不该拿到 app_write:sync/push 只认 app_write,web token
        # 打这个端点应该 403,而不是意外放行(否则就没法区分 app/web scope)。
        push = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "device_id": "pytest-device-1",
                "changes": [],
            },
        )
        assert push.status_code == 403

        read = client.get(
            "/api/v1/read/ledgers",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert read.status_code == 200
    finally:
        app.dependency_overrides.clear()
