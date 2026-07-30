"""退款(§2.6 MOZE_FEATURE_GAP_SD.md Phase 1)—— refund_of_sync_id 契约:

- web `/write/ledgers/{id}/transactions` 建交易可带 `refund_of_id`,落
  `read_tx_projection.refund_of_sync_id`,读接口原样透传
- mobile `/sync/push` 的 transaction merge 契约:partial update 不带
  `refundOfId` 时保留旧值
- 统计口径:退款(tx_type=income 且 refund_of_id 非空)**不计入当期收入**,
  改冲抵当期支出净额 —— `/summary`(_projection_totals 共享给 list_ledgers)
  与 `/workspace/analytics`(workspace_analytics)两处都要验证新旧口径

============================================================================
手动检查清单(Web UI 目前还没有"退款"入口,§2.6 只做了 server 端契约,
UI 排期见 MOZE_FEATURE_GAP_SD.md;真要在 Web 上肉眼验证只能先用 API 调试
面板或 curl 直接打 `/write/ledgers/{id}/transactions` 带上 `refund_of_id`):

1. 建一笔支出(如 500 元「购物」),再建一笔收入(如 200 元)并带
   `"refund_of_id": "<那笔支出的 id>"`。
2. 打开 web 首页 / 统计页(`GET /api/v1/read/workspace/analytics`)确认:
   - 支出总额 = 500 - 200 = 300(不是 500)
   - 收入总额里**不含**那 200(工资等其它正常收入不受影响)
   - 分类排行里"购物"分类的支出显示 300
3. `GET /api/v1/read/ledgers/{id}/transactions` 确认那笔退款交易的
   `refund_of_id` 字段回显正确,原支出交易的 `refund_of_id` 是 null。
============================================================================
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection


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


def _seed_ledger(client, token, device_id, ledger_id):
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    r = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [{
                "ledger_id": ledger_id,
                "entity_type": "ledger_snapshot",
                "entity_sync_id": ledger_id,
                "action": "upsert",
                "payload": {"content": content},
                "updated_at": _iso(),
            }],
        },
    )
    assert r.status_code == 200, r.text


def _latest_change_id(client, token, ledger_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


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


# ---------------------------------------------------------------------------
# Web write:refund_of_id 落库 + 读接口透传
# ---------------------------------------------------------------------------


def test_create_refund_tx_persists_refund_of_id():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ref1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REF1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ref1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        now = datetime.now(timezone.utc)

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 300.0,
                "happened_at": now.isoformat(),
                "category_name": "购物",
            },
        )
        assert res.status_code == 200, res.text
        expense_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "income",
                "amount": 300.0,
                "happened_at": (now + timedelta(hours=1)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        res = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        txs = {t["id"]: t for t in res.json()}
        assert txs[refund_id]["refund_of_id"] == expense_id
        assert txs[expense_id]["refund_of_id"] is None
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_transaction_partial_update_keeps_refund_of_id():
    client, TS = _make_client()
    try:
        tok = _register(client, "refmerge@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        now = _iso()

        _push(client, hdr, "lg1", "transaction", "tx-refund", {
            "syncId": "tx-refund",
            "type": "income",
            "amount": 88.0,
            "happenedAt": now,
            "refundOfId": "tx-expense-1",
        })
        # partial update:只改 amount,不带 refundOfId
        _push(client, hdr, "lg1", "transaction", "tx-refund", {
            "syncId": "tx-refund",
            "amount": 120.0,
        })

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-refund")
            )
            assert row is not None
            assert row.amount == 120.0
            assert row.refund_of_sync_id == "tx-expense-1", (
                "partial update 不带 refundOfId 时必须保留旧关联"
            )
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 统计口径:退款冲抵支出净额,不计入收入
# ---------------------------------------------------------------------------


def _setup_expense_and_refund(client, hdr, ledger_id, token):
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": base,
            "tx_type": "expense",
            "amount": 500.0,
            "happened_at": now.isoformat(),
            "category_name": "电子产品",
        },
    )
    assert res.status_code == 200, res.text
    expense_id = res.json()["entity_id"]

    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": base,
            "tx_type": "income",
            "amount": 200.0,
            "happened_at": (now + timedelta(hours=2)).isoformat(),
            "category_name": "电子产品",
            "refund_of_id": expense_id,
        },
    )
    assert res.status_code == 200, res.text

    # 一笔普通收入,不应受退款 netting 影响
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": base,
            "tx_type": "income",
            "amount": 1000.0,
            "happened_at": now.isoformat(),
            "category_name": "工资",
        },
    )
    assert res.status_code == 200, res.text
    return expense_id


def test_summary_nets_refund_against_expense():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ref2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REF2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ref2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _setup_expense_and_refund(client, hdr, ledger_id, token)

        res = client.get(f"/api/v1/read/summary?ledger_id={ledger_id}", headers=hdr)
        assert res.status_code == 200, res.text
        body = res.json()
        # expense: 500 - 200(退款净额) = 300;income: 只有工资 1000(退款不计入)
        assert body["expense_total"] == 300.0
        assert body["income_total"] == 1000.0
        # balance 口径不受影响:1000 + 200(退款仍按 income 记正号) - 500 = 700
        assert body["balance"] == 700.0
    finally:
        app.dependency_overrides.clear()


def test_workspace_analytics_nets_refund_against_expense():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ref3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REF3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ref3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _setup_expense_and_refund(client, hdr, ledger_id, token)

        res = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "month", "metric": "expense"},
        )
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        assert summary["expense_total"] == 300.0
        assert summary["income_total"] == 1000.0

        # 分类排行:「电子产品」净额 500-200=300,不应该出现在 income 榜里
        res_income = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "month", "metric": "expense"},
        )
        ranks = {r["category_name"]: r["total"] for r in res_income.json()["category_ranks"]}
        assert ranks.get("电子产品") == 300.0
    finally:
        app.dependency_overrides.clear()
