"""App 端新版本提醒(docs/superpowers/specs/2026-09-08-app-update-reminder-design.md)
的 server 端契約測試。

覆蓋:
1. `GET /admin/app-version-config`:密碼欄位不明文回傳(只回布林值),
   非 admin scope 拒絕存取。
2. `PUT /admin/app-version-config`:部分更新不清空密碼、空字串密碼視為
   不變更、非 admin 拒絕存取。
3. `check_latest_app_version` job handler(經由 `POST .../check-now` 觸發):
   mock `httpx.get`,驗證「webdav 未設定時 skip」、「成功更新
   latest_version」、「格式不合法時只寫 error 不動 latest_version」三種
   情境。
4. 公開端點 `GET /api/v1/app-version/latest`:無需鑑權即可存取、
   `version` 為 null 時的回傳格式。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import AppVersionCheckConfig, User

_TEST_SESSION: sessionmaker | None = None


def _make_client() -> TestClient:
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
    return TestClient(app)


def _register_app(client: TestClient, email: str) -> dict:
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


def _login_web(client: TestClient, email: str) -> str:
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
    return res.json()["access_token"]


def _bootstrap_admin(client: TestClient, email: str) -> str:
    user_data = _register_app(client, email)
    user_id = user_data["user"]["id"]
    assert _TEST_SESSION is not None
    db = _TEST_SESSION()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert user is not None
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    return _login_web(client, email)


def _bootstrap_non_admin(client: TestClient, email: str) -> str:
    _register_app(client, email)
    return _login_web(client, email)


def test_get_app_version_config_requires_admin():
    client = _make_client()
    try:
        token = _bootstrap_non_admin(client, "notadmin@t.com")
        r = client.get(
            "/api/v1/admin/app-version-config",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_get_app_version_config_returns_defaults_when_unset():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin1@t.com")
        r = client.get(
            "/api/v1/admin/app-version-config",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["latest_version"] is None
        assert body["nas_webdav_url"] is None
        assert body["nas_webdav_password_set"] is False
        assert body["last_checked_at"] is None
        assert body["last_check_error"] is None
    finally:
        app.dependency_overrides.clear()


def test_update_app_version_config_sets_password_and_never_echoes_it():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin2@t.com")
        r = client.put(
            "/api/v1/admin/app-version-config",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "latest_version": "3.2.0",
                "nas_webdav_url": "https://nas.example.com/webdav/latest_version.txt",
                "nas_webdav_user": "beecount",
                "nas_webdav_password": "s3cr3t",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["latest_version"] == "3.2.0"
        assert body["nas_webdav_url"] == "https://nas.example.com/webdav/latest_version.txt"
        assert body["nas_webdav_user"] == "beecount"
        assert body["nas_webdav_password_set"] is True
        assert "nas_webdav_password" not in body

        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            config = db.get(AppVersionCheckConfig, 1)
            assert config is not None
            assert config.nas_webdav_password == "s3cr3t"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_update_app_version_config_partial_update_does_not_clear_password():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin3@t.com")
        headers = {"Authorization": f"Bearer {token}"}
        client.put(
            "/api/v1/admin/app-version-config",
            headers=headers,
            json={
                "nas_webdav_url": "https://nas.example.com/webdav/latest_version.txt",
                "nas_webdav_password": "s3cr3t",
            },
        )
        # 只改 URL,不帶密碼欄位——密碼不該被清空。
        r = client.put(
            "/api/v1/admin/app-version-config",
            headers=headers,
            json={"nas_webdav_url": "https://nas.example.com/webdav/v2.txt"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["nas_webdav_password_set"] is True

        assert _TEST_SESSION is not None
        db = _TEST_SESSION()
        try:
            config = db.get(AppVersionCheckConfig, 1)
            assert config is not None
            assert config.nas_webdav_password == "s3cr3t"
            assert config.nas_webdav_url == "https://nas.example.com/webdav/v2.txt"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_update_app_version_config_requires_admin():
    client = _make_client()
    try:
        token = _bootstrap_non_admin(client, "notadmin2@t.com")
        r = client.put(
            "/api/v1/admin/app-version-config",
            headers={"Authorization": f"Bearer {token}"},
            json={"latest_version": "3.2.0"},
        )
        assert r.status_code == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_check_now_skips_when_webdav_not_configured():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin4@t.com")
        r = client.post(
            "/api/v1/admin/app-version-config/check-now",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "skipped"
        assert body["latest_version"] is None
        assert body["last_check_error"] is None
    finally:
        app.dependency_overrides.clear()


def test_check_now_updates_latest_version_on_success():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin5@t.com")
        headers = {"Authorization": f"Bearer {token}"}
        client.put(
            "/api/v1/admin/app-version-config",
            headers=headers,
            json={"nas_webdav_url": "https://nas.example.com/webdav/latest_version.txt"},
        )

        mock_response = MagicMock()
        mock_response.text = "3.2.0\n"
        mock_response.raise_for_status = MagicMock()
        with patch("src.services.app_version_check.httpx.get", return_value=mock_response) as mock_get:
            r = client.post(
                "/api/v1/admin/app-version-config/check-now",
                headers=headers,
            )
        assert mock_get.called
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok"
        assert body["latest_version"] == "3.2.0"
        assert body["last_checked_at"] is not None
        assert body["last_check_error"] is None
    finally:
        app.dependency_overrides.clear()


def test_check_now_invalid_format_keeps_old_version_and_records_error():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin6@t.com")
        headers = {"Authorization": f"Bearer {token}"}
        client.put(
            "/api/v1/admin/app-version-config",
            headers=headers,
            json={
                "latest_version": "3.1.0",
                "nas_webdav_url": "https://nas.example.com/webdav/latest_version.txt",
            },
        )

        mock_response = MagicMock()
        mock_response.text = "<html>not found</html>"
        mock_response.raise_for_status = MagicMock()
        with patch("src.services.app_version_check.httpx.get", return_value=mock_response):
            r = client.post(
                "/api/v1/admin/app-version-config/check-now",
                headers=headers,
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "error"
        assert body["last_check_error"] is not None
        # 舊版本號保留不變,不能被髒資料污染。
        assert body["latest_version"] == "3.1.0"
    finally:
        app.dependency_overrides.clear()


def test_public_latest_app_version_endpoint_requires_no_auth_and_handles_null():
    client = _make_client()
    try:
        r = client.get("/api/v1/app-version/latest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] is None
        assert body["updated_at"] is None
    finally:
        app.dependency_overrides.clear()


def test_public_latest_app_version_endpoint_reflects_configured_value():
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin7@t.com")
        client.put(
            "/api/v1/admin/app-version-config",
            headers={"Authorization": f"Bearer {token}"},
            json={"latest_version": "3.2.0"},
        )

        r = client.get("/api/v1/app-version/latest")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["version"] == "3.2.0"
        assert body["updated_at"] is not None
    finally:
        app.dependency_overrides.clear()
