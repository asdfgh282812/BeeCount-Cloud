"""`/sync/pull?include_own=true`:

預設 pull 會排除「該裝置自己推上來的 change」(它本地已有)。但本機實體若因別的
原因消失(例如被非追蹤路徑刪掉),自己的 change 永遠拉不回來 → App 的 replay /
差異自動修復要帶 include_own=true 才補得回。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    return TestClient(app)


def _login(client: TestClient, email: str) -> tuple[dict, str]:
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d1",
            "client_type": "app",
            "device_name": "pytest",
            "platform": "test",
        },
    )
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["device_id"]


def _push_account(client: TestClient, hdr: dict, device_id: str) -> None:
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": "lg1",
                    "entity_type": "account",
                    "entity_sync_id": "acc_own1",
                    "action": "upsert",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "syncId": "acc_own1",
                        "name": "111",
                        "type": "cash",
                        "currency": "TWD",
                    },
                }
            ],
        },
    )
    assert r.status_code == 200, r.text


def _account_ids(client: TestClient, hdr: dict, query: str) -> list[str]:
    r = client.get(f"/api/v1/sync/pull?since=0&{query}", headers=hdr)
    assert r.status_code == 200, r.text
    return [c["entity_sync_id"] for c in r.json()["changes"] if c["entity_type"] == "account"]


def test_pull_excludes_own_device_changes_by_default() -> None:
    client = _make_client()
    try:
        hdr, device_id = _login(client, "own1@t.com")
        _push_account(client, hdr, device_id)
        assert _account_ids(client, hdr, f"device_id={device_id}") == []
    finally:
        app.dependency_overrides.clear()


def test_pull_include_own_returns_own_device_changes() -> None:
    client = _make_client()
    try:
        hdr, device_id = _login(client, "own2@t.com")
        _push_account(client, hdr, device_id)
        assert _account_ids(client, hdr, f"device_id={device_id}&include_own=true") == [
            "acc_own1"
        ]
    finally:
        app.dependency_overrides.clear()
