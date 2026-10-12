"""refresh token 輪替寬限 + 裝置刪除即登出(alembic 0071,routers/auth.py::refresh)。

App 掛後台時 refresh 的回應可能遺失,用戶端只剩已被輪替的舊 token;只要後代
從沒被用戶端用過就要讓它再換一次。登出 / 撤銷裝置 / 刪掉裝置則一定要失效。
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Device, RefreshToken
from src.services import scheduled_jobs


def _make_client():
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


def _register(client: TestClient, email: str = "grace@example.com") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_id": "dev-grace-1",
            "device_name": "iPhone",
            "platform": "ios",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _refresh(client: TestClient, token: str):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": token})


def test_lost_refresh_response_can_retry_with_old_token() -> None:
    client, _ = _make_client()
    try:
        r0 = _register(client)["refresh_token"]
        # 第一次 refresh 的回應「遺失」:server 已輪替,用戶端仍只有 r0。
        assert _refresh(client, r0).status_code == 200
        retry = _refresh(client, r0)
        assert retry.status_code == 200
        r2 = retry.json()["refresh_token"]
        # 可以一直這樣重試(回應又遺失)
        assert _refresh(client, r0).status_code == 200
        # 拿到的新 token 正常使用
        latest = _refresh(client, r0).json()["refresh_token"]
        assert _refresh(client, latest).status_code == 200
        # 用戶端已經用過後代 → 舊的與被跳過的那代都不能再用
        assert _refresh(client, r0).status_code == 401
        assert _refresh(client, r2).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_concurrent_refresh_with_same_token_both_succeed() -> None:
    client, _ = _make_client()
    try:
        r0 = _register(client)["refresh_token"]
        a = _refresh(client, r0)
        b = _refresh(client, r0)  # 背景 isolate 同時用同一把
        assert a.status_code == 200 and b.status_code == 200
        # 不論最後落盤的是哪一把都要能繼續用
        ra = a.json()["refresh_token"]
        rb = b.json()["refresh_token"]
        assert _refresh(client, rb).status_code == 200 or _refresh(client, ra).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_logout_still_kills_rotated_ancestors() -> None:
    client, _ = _make_client()
    try:
        body = _register(client)
        r0 = body["refresh_token"]
        refreshed = _refresh(client, r0).json()
        logout = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refreshed["refresh_token"]},
            headers={"Authorization": f"Bearer {refreshed['access_token']}"},
        )
        assert logout.status_code == 200
        assert _refresh(client, refreshed["refresh_token"]).status_code == 401
        assert _refresh(client, r0).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_revoked_device_kills_rotated_ancestors() -> None:
    client, _ = _make_client()
    try:
        body = _register(client)
        r0 = body["refresh_token"]
        assert _refresh(client, r0).status_code == 200
        # 撤銷裝置是 Web 面板的操作(需要 web scope)
        web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "grace@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "web",
                "platform": "web",
            },
        ).json()
        res = client.post(
            f"/api/v1/devices/{body['device_id']}/revoke",
            headers={"Authorization": f"Bearer {web['access_token']}"},
        )
        assert res.status_code == 200, res.text
        assert _refresh(client, r0).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_deleted_device_row_logs_out() -> None:
    client, session_factory = _make_client()
    try:
        body = _register(client)
        db = session_factory()
        try:
            db.delete(db.get(Device, body["device_id"]))
            db.commit()
        finally:
            db.close()
        res = _refresh(client, body["refresh_token"])
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_retention_keeps_rotated_token_until_successor_used() -> None:
    client, session_factory = _make_client()
    try:
        r0 = _register(client)["refresh_token"]
        r1 = _refresh(client, r0).json()["refresh_token"]  # 回應「遺失」
        db = session_factory()
        try:
            old = datetime.now(timezone.utc) - timedelta(days=10)
            for row in db.scalars(select(RefreshToken)).all():
                if row.revoked_at is not None:
                    row.revoked_at = old
            db.commit()
            scheduled_jobs._run_refresh_token_retention(db)
            db.commit()
        finally:
            db.close()
        # r1 從沒被用過 → r0 必須還在,寬限才有效
        assert _refresh(client, r0).status_code == 200

        # 後代被用過之後,舊列就該被清掉
        latest = _refresh(client, r0).json()["refresh_token"]
        assert _refresh(client, latest).status_code == 200
        db = session_factory()
        try:
            old = datetime.now(timezone.utc) - timedelta(days=10)
            for row in db.scalars(select(RefreshToken)).all():
                if row.revoked_at is not None:
                    row.revoked_at = old
            db.commit()
            scheduled_jobs._run_refresh_token_retention(db)
            db.commit()
            remaining = db.scalars(select(RefreshToken)).all()
            # 只剩目前這把,和「剛拿它換新、下一代還沒被用過」的上一把
            # (那次的回應也可能遺失,要留著給寬限)。
            assert len(remaining) == 2
            assert sum(1 for row in remaining if row.revoked_at is None) == 1
        finally:
            db.close()
        assert r1  # silence unused
    finally:
        app.dependency_overrides.clear()
