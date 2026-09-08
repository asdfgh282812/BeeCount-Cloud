from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Device


def _make_client() -> tuple[TestClient, sessionmaker]:
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
    return TestClient(app), testing_session


def _register_app(client: TestClient, email: str, *, device_id: str, app_version: str) -> dict:
    # conftest 全局把 ALLOW_APP_RW_SCOPES 设成 false,app-only token 拿不到
    # SCOPE_OPS_WRITE,/devices 写端点会 403 —— 这里用 client_type=web 让 token
    # 带上 SCOPE_OPS_WRITE,跟 test_admin_users_and_devices_api.py 的写法一致。
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_id": device_id,
            "device_name": "pytest-device",
            "platform": "ios",
            "app_version": app_version,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_report_version_updates_device_app_version() -> None:
    client, testing_session = _make_client()
    try:
        auth = _register_app(client, "u1@example.com", device_id="dev-1", app_version="1.0.0+1")
        token = auth["access_token"]

        res = client.post(
            "/api/v1/devices/dev-1/report-version",
            headers={"Authorization": f"Bearer {token}"},
            json={"app_version": "3.1.0+1"},
        )
        assert res.status_code == 200, res.text
        assert res.json() == {"ok": True, "device_id": "dev-1", "app_version": "3.1.0+1"}

        db = testing_session()
        try:
            device = db.scalar(select(Device).where(Device.id == "dev-1"))
            assert device is not None
            assert device.app_version == "3.1.0+1"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_report_version_rejects_other_users_device() -> None:
    client, testing_session = _make_client()
    try:
        _register_app(client, "owner@example.com", device_id="dev-owner", app_version="1.0.0+1")
        other_auth = _register_app(client, "other@example.com", device_id="dev-other", app_version="1.0.0+1")
        other_token = other_auth["access_token"]

        res = client.post(
            "/api/v1/devices/dev-owner/report-version",
            headers={"Authorization": f"Bearer {other_token}"},
            json={"app_version": "9.9.9+9"},
        )
        assert res.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_report_version_requires_nonempty_body() -> None:
    client, testing_session = _make_client()
    try:
        auth = _register_app(client, "u2@example.com", device_id="dev-2", app_version="1.0.0+1")
        token = auth["access_token"]

        res = client.post(
            "/api/v1/devices/dev-2/report-version",
            headers={"Authorization": f"Bearer {token}"},
            json={"app_version": ""},
        )
        assert res.status_code == 422
    finally:
        app.dependency_overrides.clear()
