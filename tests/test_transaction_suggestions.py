"""Phase 21(docs/PH17_USER_FEEDBACK_2026-08_SD.md):分類/帳戶智慧推薦。

`GET /ledgers/{id}/category-suggestions` 依「整體頻率＋同時段＋同帳戶」加權
排序;`GET /ledgers/{id}/account-suggestions` 依「該分類最近/最常使用的
帳戶」排序。兩者都是純唯讀彙總查詢,不寫入任何資料。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _register_and_token(client: TestClient, email: str, *, device_id: str, client_type: str) -> str:
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _push(client: TestClient, hdr: dict, device_id: str, ledger_id: str, changes: list[dict]) -> None:
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": device_id, "changes": changes},
    )
    assert r.status_code == 200, r.text


def _push_tx(
    client: TestClient,
    hdr: dict,
    device_id: str,
    ledger_id: str,
    *,
    tx_sync_id: str,
    category_id: str,
    account_id: str,
    happened_at: datetime,
    tx_type: str = "expense",
) -> None:
    _push(client, hdr, device_id, ledger_id, [
        {
            "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": tx_sync_id,
            "action": "upsert", "updated_at": _iso(datetime.now(timezone.utc)),
            "payload": {
                "syncId": tx_sync_id, "type": tx_type, "amount": 10,
                "happenedAt": _iso(happened_at),
                "categoryId": category_id, "categoryName": category_id,
                "accountId": account_id, "accountName": account_id,
            },
        },
    ])


def _setup(email: str) -> tuple[TestClient, dict, dict, str, str]:
    """回傳 (client, app_hdr, web_hdr, ledger_id, device_id) —— push 用 app
    token(SCOPE_APP_WRITE),讀 suggestion 端點用 web token(SCOPE_WEB_READ),
    比照 test_tx_read_id_resolution.py 既有慣例。"""
    client = _make_client()
    app_token = _register_and_token(client, email, device_id="m1", client_type="app")
    web_token = _register_and_token(client, email, device_id="w1", client_type="web")
    app_hdr = {"Authorization": f"Bearer {app_token}"}
    web_hdr = {"Authorization": f"Bearer {web_token}"}
    ledger_id = "lg_1"
    return client, app_hdr, web_hdr, ledger_id, "m1"


def test_category_suggestions_same_hour_weighting_flips_rank():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("hour@test.com")
    try:
        today = datetime.now(timezone.utc).date()
        off_hour_dt = datetime(today.year, today.month, today.day, 3, 0, tzinfo=timezone.utc)
        matching_hour_dt = datetime(today.year, today.month, today.day, 15, 0, tzinfo=timezone.utc)

        # Coffee 被记两次,但时段完全不匹配查询的 hour=15。
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-1", category_id="cat-coffee",
                 account_id="acc-cash", happened_at=off_hour_dt)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-2", category_id="cat-coffee",
                 account_id="acc-cash", happened_at=off_hour_dt)
        # Taxi 只记一次,但时段命中 hour=15,时段加权应该让它反超频率更高的 Coffee。
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-3", category_id="cat-taxi",
                 account_id="acc-cash", happened_at=matching_hour_dt)

        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/category-suggestions",
            headers=web_hdr,
            params={"tx_type": "expense", "hour": 15, "tz_offset_minutes": 0},
        )
        assert r.status_code == 200, r.text
        ids = r.json()["category_ids"]
        assert ids[0] == "cat-taxi", f"expected cat-taxi first, got {ids}"
        assert "cat-coffee" in ids
    finally:
        app.dependency_overrides.clear()


def test_category_suggestions_same_account_weighting_flips_rank():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("account@test.com")
    try:
        now = datetime.now(timezone.utc)

        # Grocery 记两次(用另一个帐户),Online 只记一次但帐户命中查询的 account_id。
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-1", category_id="cat-grocery",
                 account_id="acc-cash", happened_at=now)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-2", category_id="cat-grocery",
                 account_id="acc-cash", happened_at=now)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-3", category_id="cat-online",
                 account_id="acc-card", happened_at=now)

        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/category-suggestions",
            headers=web_hdr,
            params={"tx_type": "expense", "account_id": "acc-card"},
        )
        assert r.status_code == 200, r.text
        ids = r.json()["category_ids"]
        assert ids[0] == "cat-online", f"expected cat-online first, got {ids}"
        assert "cat-grocery" in ids
    finally:
        app.dependency_overrides.clear()


def test_category_suggestions_no_history_returns_empty_not_error():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("empty@test.com")
    try:
        # 账本存在(推一条 ledger 变更建立账本行 —— category/account 是
        # user-global entity,推它们不会触发 Ledger 自动建行),但完全没有
        # 交易历史,不该 500,应该回传空阵列。
        _push(client, app_hdr, device_id, ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "ledger", "entity_sync_id": ledger_id,
                "action": "upsert", "updated_at": _iso(datetime.now(timezone.utc)),
                "payload": {"ledgerName": "Empty Ledger"},
            },
        ])
        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/category-suggestions",
            headers=web_hdr,
            params={"tx_type": "expense"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["category_ids"] == []
    finally:
        app.dependency_overrides.clear()


def test_account_suggestions_ranks_by_frequency_for_category():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("acctrank@test.com")
    try:
        now = datetime.now(timezone.utc)

        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-1", category_id="cat-x",
                 account_id="acc-p", happened_at=now)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-2", category_id="cat-x",
                 account_id="acc-p", happened_at=now)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-3", category_id="cat-x",
                 account_id="acc-p", happened_at=now)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-4", category_id="cat-x",
                 account_id="acc-q", happened_at=now)

        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/account-suggestions",
            headers=web_hdr,
            params={"category_id": "cat-x"},
        )
        assert r.status_code == 200, r.text
        ids = r.json()["account_ids"]
        assert ids[0] == "acc-p", f"expected acc-p first, got {ids}"
        assert "acc-q" in ids
    finally:
        app.dependency_overrides.clear()


def test_account_suggestions_excludes_transactions_outside_lookback_window():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("lookback@test.com")
    try:
        now = datetime.now(timezone.utc)
        stale = now - timedelta(days=200)  # 超过 180 天 lookback window

        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-1", category_id="cat-y",
                 account_id="acc-stale", happened_at=stale)
        _push_tx(client, app_hdr, device_id, ledger_id, tx_sync_id="tx-2", category_id="cat-y",
                 account_id="acc-fresh", happened_at=now)

        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/account-suggestions",
            headers=web_hdr,
            params={"category_id": "cat-y"},
        )
        assert r.status_code == 200, r.text
        ids = r.json()["account_ids"]
        assert ids == ["acc-fresh"], f"expected only acc-fresh, got {ids}"
    finally:
        app.dependency_overrides.clear()


def test_account_suggestions_empty_history_returns_empty_not_error():
    client, app_hdr, web_hdr, ledger_id, device_id = _setup("acctempty@test.com")
    try:
        # 账本存在(推一条 ledger 变更建立账本行 —— category/account 是
        # user-global entity,推它们不会触发 Ledger 自动建行),但完全没有
        # 交易历史,不该 500,应该回传空阵列。
        _push(client, app_hdr, device_id, ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "ledger", "entity_sync_id": ledger_id,
                "action": "upsert", "updated_at": _iso(datetime.now(timezone.utc)),
                "payload": {"ledgerName": "Empty Ledger"},
            },
        ])
        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/account-suggestions",
            headers=web_hdr,
            params={"category_id": "cat-never-used"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["account_ids"] == []
    finally:
        app.dependency_overrides.clear()
