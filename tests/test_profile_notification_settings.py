"""profile.notification_settings 往返契约:整體替換 / 部分更新不清空 / {} 清空。"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
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


def _login(client, email):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": "d1",
            "client_type": "app", "device_name": "pytest", "platform": "test",
        },
    )
    return r.json()["access_token"]


def test_notification_settings_roundtrip_and_partial_update():
    client, _ = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'ns1@t.com')}"}
        assert client.get("/api/v1/profile/me", headers=hdr).json()["notification_settings"] is None
        payload = {"reminder_enabled": True, "reminder_hour": 20, "reminder_minute": 30}
        r = client.patch("/api/v1/profile/me", headers=hdr, json={"notification_settings": payload})
        assert r.status_code == 200, r.text
        assert r.json()["notification_settings"] == payload
        # 改其他欄位不應清掉通知設定
        client.patch("/api/v1/profile/me", headers=hdr, json={"display_name": "nick"})
        assert client.get("/api/v1/profile/me", headers=hdr).json()["notification_settings"] == payload
        # {} 視為清空
        client.patch("/api/v1/profile/me", headers=hdr, json={"notification_settings": {}})
        assert client.get("/api/v1/profile/me", headers=hdr).json()["notification_settings"] is None
    finally:
        app.dependency_overrides.clear()
