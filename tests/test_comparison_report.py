"""比較報表(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5)—— `GET /workspace/comparison`:

- `scope=month`/`offset=1` 環比上個月;`offset=12` 同比去年同月。
- `scope=year`/`offset=1` 跟去年比。
- income/expense/balance 三個指標的 current/previous/diff/diff_pct。
- 分類排行 diff(依 |diff| 降序)。
- 退款 netting / 拆帳展開沿用 `workspace_analytics` 同款口徑。
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
    return TestClient(app), TS


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _register(client, email, client_type="app", device_id="d1"):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
            "device_id": device_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _login_web(client, email):
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


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


def _setup(email, ledger_id="L_CMP1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    hdr_app = {"Authorization": f"Bearer {app_token}"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": ledger_id, "currency": "CNY"}, device_id=device)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, hdr_app, device, token, hdr, ledger_id


def _tx(hour_month, day, amount, tx_type, category, year=2026):
    return {
        "type": tx_type,
        "amount": amount,
        "happenedAt": datetime(year, hour_month, day, 12, tzinfo=timezone.utc).isoformat(),
        "categoryName": category,
    }


def test_comparison_month_over_month_income_expense_diff():
    client, _TS, hdr_app, device, token, hdr, ledger_id = _setup("cmp1@example.com")
    try:
        # 6月:餐飲 300 支出,薪水 5000 收入
        _push(client, hdr_app, ledger_id, "transaction", "tx1",
              {"syncId": "tx1", **_tx(6, 10, 300.0, "expense", "餐飲")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "tx2",
              {"syncId": "tx2", **_tx(6, 5, 5000.0, "income", "薪水")}, device_id=device)
        # 7月(當期):餐飲 500 支出,薪水 5000 收入
        _push(client, hdr_app, ledger_id, "transaction", "tx3",
              {"syncId": "tx3", **_tx(7, 10, 500.0, "expense", "餐飲")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "tx4",
              {"syncId": "tx4", **_tx(7, 5, 5000.0, "income", "薪水")}, device_id=device)

        r = client.get(
            "/api/v1/read/workspace/comparison", headers=hdr,
            params={"scope": "month", "period": "2026-07", "offset": 1, "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scope"] == "month"
        assert data["offset"] == 1
        assert data["expense"]["current"] == 500.0
        assert data["expense"]["previous"] == 300.0
        assert data["expense"]["diff"] == 200.0
        assert data["income"]["current"] == 5000.0
        assert data["income"]["previous"] == 5000.0
        assert data["income"]["diff"] == 0.0
        assert data["balance"]["current"] == 4500.0
        assert data["balance"]["previous"] == 4700.0

        cat_by_name = {c["category_name"]: c for c in data["category_breakdown"]}
        assert cat_by_name["餐飲"]["current"] == 500.0
        assert cat_by_name["餐飲"]["previous"] == 300.0
        assert cat_by_name["餐飲"]["diff"] == 200.0
    finally:
        client.close()


def test_comparison_year_over_year_same_month_offset_12():
    client, _TS, hdr_app, device, token, hdr, ledger_id = _setup("cmp2@example.com")
    try:
        # 去年 7 月
        _push(client, hdr_app, ledger_id, "transaction", "tx1",
              {"syncId": "tx1", **_tx(7, 10, 100.0, "expense", "娛樂", year=2025)}, device_id=device)
        # 今年 7 月
        _push(client, hdr_app, ledger_id, "transaction", "tx2",
              {"syncId": "tx2", **_tx(7, 10, 400.0, "expense", "娛樂", year=2026)}, device_id=device)

        r = client.get(
            "/api/v1/read/workspace/comparison", headers=hdr,
            params={"scope": "month", "period": "2026-07", "offset": 12, "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["expense"]["current"] == 400.0
        assert data["expense"]["previous"] == 100.0
        assert data["expense"]["diff_pct"] == 300.0
    finally:
        client.close()


def test_comparison_year_scope():
    client, _TS, hdr_app, device, token, hdr, ledger_id = _setup("cmp3@example.com")
    try:
        _push(client, hdr_app, ledger_id, "transaction", "tx1",
              {"syncId": "tx1", **_tx(3, 1, 1000.0, "expense", "housing", year=2025)}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "tx2",
              {"syncId": "tx2", **_tx(3, 1, 1500.0, "expense", "housing", year=2026)}, device_id=device)

        r = client.get(
            "/api/v1/read/workspace/comparison", headers=hdr,
            params={"scope": "year", "period": "2026", "offset": 1, "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["scope"] == "year"
        assert data["expense"]["current"] == 1500.0
        assert data["expense"]["previous"] == 1000.0
    finally:
        client.close()


def test_comparison_refund_nets_against_original_direction():
    client, _TS, hdr_app, device, token, hdr, ledger_id = _setup("cmp4@example.com")
    try:
        _push(client, hdr_app, ledger_id, "transaction", "tx1",
              {"syncId": "tx1", **_tx(7, 5, 200.0, "expense", "購物")}, device_id=device)
        # income 退款冲抵 expense_total,不计入自己的 income 分项
        _push(client, hdr_app, ledger_id, "transaction", "tx2",
              {"syncId": "tx2", **_tx(7, 6, 200.0, "income", "購物"), "refundOfId": "tx1"}, device_id=device)

        r = client.get(
            "/api/v1/read/workspace/comparison", headers=hdr,
            params={"scope": "month", "period": "2026-07", "offset": 1, "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["expense"]["current"] == 0.0
        assert data["income"]["current"] == 0.0
    finally:
        client.close()


def test_comparison_empty_periods_return_zero_without_crash():
    client, _TS, _hdr_app, _device, token, hdr, ledger_id = _setup("cmp5@example.com")
    try:
        r = client.get(
            "/api/v1/read/workspace/comparison", headers=hdr,
            params={"scope": "month", "period": "2026-07", "offset": 1, "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["income"]["current"] == 0.0
        assert data["income"]["diff_pct"] is None
        assert data["category_breakdown"] == []
    finally:
        client.close()
