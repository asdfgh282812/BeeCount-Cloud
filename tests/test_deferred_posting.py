"""延後入帳(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5,對帳模式必要前置)——
`ReadTxProjection.deferred_posting_at` 契约:

- mobile `/sync/push` 的 `transaction` merge 契约:partial update 缺键保留
  既有 `deferredPostingAt` 标记(跟 `refundOfId`/`installmentPlanId` 同款)。
- `services.deferred_posting.attribution_date_expr()` 的 COALESCE 套用在
  信用卡帳單週期彙總(`credit_card_billing.compute_group_billing`)—— 消費日
  在週期窗口之外,但延後入帳日落在窗口內的交易應該被算進該期帳單,反之亦然。
- 舊資料(`deferred_posting_at` 全部是 NULL)行为不变(COALESCE 退化成
  `happened_at`)——用一个「完全不带 deferredPostingAt」的既有 billing 測試
  场景验证零回归。

對帳模式本身(§2.10 主功能)的測試見 `tests/test_reconciliation.py`。
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
from src.services import credit_card


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


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": device_id,
            "client_type": client_type, "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _dt(d, hour=12):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_deferred_posting_at_partial_update_keeps_value():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "dp1@example.com", device_id="d-app", client_type="app")
        hdr = {"Authorization": f"Bearer {app_tok}"}
        _push(client, hdr, "L1", "ledger", "L1", {"syncId": "L1", "ledgerName": "L1", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        deferred = now + timedelta(days=5)
        sync_id = "tx_deferred1"
        _push(client, hdr, "L1", "transaction", sync_id, {
            "syncId": sync_id, "type": "expense", "amount": 100.0,
            "happenedAt": _iso(now), "deferredPostingAt": _iso(deferred),
        }, device_id="d-app")

        # 只改 note,不带 deferredPostingAt,应保留旧值
        _push(client, hdr, "L1", "transaction", sync_id, {
            "syncId": sync_id, "note": "备注",
        }, device_id="d-app")

        db = TS()
        try:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
            assert row is not None
            assert row.note == "备注"
            assert row.deferred_posting_at is not None
            got = row.deferred_posting_at
            if got.tzinfo is None:
                got = got.replace(tzinfo=timezone.utc)
            assert abs((got - deferred).total_seconds()) < 2
        finally:
            db.close()

    finally:
        client.close()


# ---------------------------------------------------------------------------
# 信用卡帳單彙總套用 COALESCE(deferred_posting_at, happened_at)
# ---------------------------------------------------------------------------


def _setup_billing_ledger(client, hdr_app):
    now = datetime.now(timezone.utc)
    yesterday = now.date() - timedelta(days=1)
    billing_day = yesterday.day
    payment_due_day = 20
    cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
    assert cycle_end == yesterday

    _push(client, hdr_app, "lgdp1", "ledger", "lgdp1", {"syncId": "lgdp1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, "lgdp1", "account", "acc-card",
          {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY",
           "billingDay": billing_day, "paymentDueDay": payment_due_day}, device_id="d-app")
    return cycle_start, cycle_end, payment_due_day


def test_deferred_posting_pulls_transaction_into_earlier_billing_cycle():
    """消費日落在「下一期」(超出本期窗口),但延後入帳日落回本期窗口內
    ——應該被算進本期帳單的 statement_amount,不是消費日那期。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "dp2@example.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "dp2@example.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        cycle_start, cycle_end, _due = _setup_billing_ledger(client, hdr_app)

        # 消費日在本期結束後 3 天(下一期),但商店延遲請款,實際入帳日落在
        # 本期結帳日當天。
        _push(client, hdr_app, "lgdp1", "transaction", "tx-deferred",
              {"syncId": "tx-deferred", "type": "expense", "amount": 88.0,
               "happenedAt": _dt(cycle_end + timedelta(days=3)),
               "deferredPostingAt": _dt(cycle_end),
               "accountId": "acc-card", "accountName": "信用卡"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lgdp1/accounts/acc-card/billing-summary", headers=hdr_web)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["statement_amount"] == 88.0
        assert data["remaining_due"] == 88.0
    finally:
        client.close()


def test_deferred_posting_pushes_transaction_out_of_current_cycle():
    """消費日本來落在本期窗口內,但使用者標記延後入帳到下一期 —— 本期帳單
    不該算這筆,應該被推到下一期。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "dp3@example.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "dp3@example.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        cycle_start, cycle_end, _due = _setup_billing_ledger(client, hdr_app)

        _push(client, hdr_app, "lgdp1", "transaction", "tx-pushed-out",
              {"syncId": "tx-pushed-out", "type": "expense", "amount": 66.0,
               "happenedAt": _dt(cycle_end),
               "deferredPostingAt": _dt(cycle_end + timedelta(days=5)),
               "accountId": "acc-card", "accountName": "信用卡"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lgdp1/accounts/acc-card/billing-summary", headers=hdr_web)
        assert r.status_code == 200, r.text
        data = r.json()
        # `remaining_due` 是「截至本期結帳日為止」的終身消費減終身已繳(見
        # `compute_group_billing` docstring),不是真正無視窗口的無限期加總
        # ——這筆被延後到下一期結帳日之後才入帳的交易,兩個欄位都不該算進
        # 本期,會出現在下一期的帳單裡。
        assert data["statement_amount"] == 0.0
        assert data["remaining_due"] == 0.0
    finally:
        client.close()


def test_billing_without_deferred_posting_unaffected_regression_check():
    """零回归檢查:完全不帶 deferredPostingAt 的既有場景,COALESCE 退化成
    `happened_at`,行為跟改動前一致。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "dp4@example.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "dp4@example.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        cycle_start, cycle_end, _due = _setup_billing_ledger(client, hdr_app)

        _push(client, hdr_app, "lgdp1", "transaction", "tx-plain",
              {"syncId": "tx-plain", "type": "expense", "amount": 42.0,
               "happenedAt": _dt(cycle_start + timedelta(days=1)),
               "accountId": "acc-card", "accountName": "信用卡"}, device_id="d-app")

        r = client.get("/api/v1/read/ledgers/lgdp1/accounts/acc-card/billing-summary", headers=hdr_web)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["statement_amount"] == 42.0
        assert data["remaining_due"] == 42.0
    finally:
        client.close()
