"""分期付款(§2.3 / Phase 1.5 修正版 §2.12.1 MOZE_FEATURE_GAP_SD.md)——
installment_plan / installment_period entity 契约:

- web `/write/ledgers/{id}/installment-plans` **建立當下依攤還算法一次算出
  全部期数**,同事务为每期各写一笔 `read_tx_projection`(带
  `installment_plan_id` 反查)+ 一笔 `read_installment_period_projection`,
  不再依赖排程逐期生成(旧版
  `recurring_materializer.materialize_due_installment_plans` 已整段删除)。
- mobile `/sync/push` 的 `installment_plan` merge 契约(partial update 保留
  旧值,含 Phase 1.5 新增的 6 个攤還参数字段)。
- 差異化編輯:`PATCH .../periods/{n}` 單獨編輯(overridden)、
  `POST .../rebalance-from/{n}` 調利率連同未來(跳過 overridden)、
  `POST .../early-repay-principal` 部分還本、`POST .../payoff` 提前結清、
  `POST .../terminate-future` 終止未來分期(不生成結清交易)。

============================================================================
手动检查清单(pytest 测不到的运行时行为):

1. `sqlite3 beecount.db` 查
   `SELECT * FROM read_installment_period_projection WHERE plan_sync_id='<id>';`
   确认本金/利息明细跟 `services/installment_amortization.py` 的算法一致。
2. `GET /api/v1/notifications?category=reminder` 在 payoff / 早偿全额结清后
   应该能看到"分期付款已结清"的通知。
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
from src.models import ReadInstallmentPlanProjection


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


def _transactions(client, hdr, ledger_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/transactions",
        headers=hdr,
        params={"limit": 500},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _plans(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/installment-plans", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def _periods(client, hdr, ledger_id, plan_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/installment-plans/{plan_id}/periods",
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Web write:建计画当下一次生成全部期数
# ---------------------------------------------------------------------------


def test_create_installment_plan_generates_all_periods():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ins1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INS1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ins1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
                "note": "笔记本电脑",
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        plans = _plans(client, hdr, ledger_id)
        assert len(plans) == 1
        assert plans[0]["id"] == plan_id
        assert plans[0]["total_amount"] == 1200.0
        assert plans[0]["periods"] == 12
        assert plans[0]["period_amount"] == 100.0
        assert plans[0]["status"] == "active"
        assert plans[0]["repayment_method"] == "equal_principal"
        assert plans[0]["paid_periods"] == 0, "首期还没到,全部都还没算发生"

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 12, "建计画当下就应该生成全部 12 期,不是只有第一期"
        assert all(t["amount"] == 100.0 for t in txs)
        assert all(t["installment_plan_id"] == plan_id for t in txs)
        assert all(t["tx_type"] == "expense" for t in txs)

        periods = _periods(client, hdr, ledger_id, plan_id)
        assert len(periods) == 12
        assert [p["period_no"] for p in periods] == list(range(1, 13))
        assert all(p["principal_amount"] == 100.0 for p in periods)
        assert all(p["interest_amount"] == 0.0 for p in periods)
        assert all(p["status"] == "generated" for p in periods)
        assert all(p["tx_id"] is not None for p in periods)
    finally:
        app.dependency_overrides.clear()


def test_installment_plan_plain_patch_can_settle():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ins2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INS2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ins2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 300.0,
                "periods": 3,
                "first_period_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}",
            headers=hdr,
            json={"base_change_id": base, "status": "settled"},
        )
        assert res.status_code == 200, res.text
        assert _plans(client, hdr, ledger_id)[0]["status"] == "settled"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mobile push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_installment_plan_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        tok = _register(client, "insmerge@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        first = datetime.now(timezone.utc)
        next_p = first + timedelta(days=30)

        _push(client, hdr, "lg1", "installment_plan", "ins-1", {
            "syncId": "ins-1",
            "totalAmount": 600.0,
            "periods": 6,
            "periodAmount": 100.0,
            "firstPeriodAt": first.isoformat(),
            "nextPeriodAt": next_p.isoformat(),
            "paidPeriods": 1,
            "categoryId": "cat-electronics",
            "note": "手机",
            "status": "active",
            "repaymentMethod": "equal_installment",
            "interestPeriod": "daily",
            "interestRate": 0.08,
            "roundAmounts": False,
            "remainderPosition": "first",
            "gracePeriodMonths": 1,
        })
        # partial update:只改 note
        _push(client, hdr, "lg1", "installment_plan", "ins-1", {
            "syncId": "ins-1",
            "note": "手机(备注更新)",
        })

        with TS() as db:
            row = db.scalar(
                select(ReadInstallmentPlanProjection).where(
                    ReadInstallmentPlanProjection.sync_id == "ins-1",
                )
            )
            assert row is not None
            assert row.note == "手机(备注更新)"
            assert row.total_amount == 600.0, "partial update 不该冲掉 total_amount"
            assert row.periods == 6
            assert row.category_sync_id == "cat-electronics"
            assert row.status == "active"
            assert row.repayment_method == "equal_installment"
            assert row.interest_period == "daily"
            assert row.interest_rate == 0.08
            assert row.round_amounts is False
            assert row.remainder_position == "first"
            assert row.grace_period_months == 1
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 差異化編輯
# ---------------------------------------------------------------------------


def test_installment_period_patch_marks_overridden_and_skipped_by_rebalance():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insedit1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSEDIT1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insedit1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
                "repayment_method": "equal_installment",
                "interest_period": "monthly",
                "interest_rate": 0.12,
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        periods_before = _periods(client, hdr, ledger_id, plan_id)
        period_8_no = 8

        # 单独编辑第 8 期,金额改成 500
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/periods/{period_8_no}",
            headers=hdr,
            json={"base_change_id": base, "amount": 500.0},
        )
        assert res.status_code == 200, res.text

        periods = {p["period_no"]: p for p in _periods(client, hdr, ledger_id, plan_id)}
        assert periods[8]["status"] == "overridden"
        assert periods[8]["total_amount"] == 500.0

        # 从第 6 期起调利率(连同未来),第 8 期(overridden)应该被跳过
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/rebalance-from/6",
            headers=hdr,
            json={"base_change_id": base, "interest_rate": 0.36},
        )
        assert res.status_code == 200, res.text

        periods_after = {p["period_no"]: p for p in _periods(client, hdr, ledger_id, plan_id)}
        # 第 1-5 期不受影响
        for no in range(1, 6):
            before = next(p for p in periods_before if p["period_no"] == no)
            assert periods_after[no]["principal_amount"] == before["principal_amount"]
            assert periods_after[no]["interest_amount"] == before["interest_amount"]
        # 第 8 期(overridden)维持编辑后的值,不被 rebalance 覆盖
        assert periods_after[8]["total_amount"] == 500.0
        assert periods_after[8]["status"] == "overridden"
        # 第 6/7/9-12 期被重算(利率大幅调高,利息应该变了)
        assert periods_after[6]["interest_amount"] != periods_before[5]["interest_amount"]
        # 未 overridden 的期数(6,7,9,10,11,12,共 7 期)本金加总应约等于剩余本金
        remaining_targets = [periods_after[n] for n in (6, 7, 9, 10, 11, 12)]
        recalculated_principal_sum = sum(p["principal_amount"] for p in remaining_targets)
        prior_principal_sum = sum(
            p["principal_amount"] for p in periods_before if p["period_no"] < 6
        )
        # 第 8 期(overridden)本金不算进"剩余待攤還本金"的重新分配里,单独核算
        expected_remaining = 1200.0 - prior_principal_sum - periods_after[8]["principal_amount"]
        assert abs(recalculated_principal_sum - expected_remaining) < 1.0
    finally:
        app.dependency_overrides.clear()


def test_installment_early_repay_principal_reduces_future_periods():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrepay1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREPAY1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrepay1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/early-repay-principal",
            headers=hdr,
            json={"base_change_id": base, "payment_amount": 600.0},
        )
        assert res.status_code == 200, res.text

        periods = _periods(client, hdr, ledger_id, plan_id)
        assert len(periods) == 12, "部分还本不改变期数结构"
        assert abs(sum(p["principal_amount"] for p in periods) - 600.0) < 1.0

        txs = _transactions(client, hdr, ledger_id)
        # 12 期分期交易 + 1 笔部分还本交易
        assert len(txs) == 13
        repay_tx = next(t for t in txs if t["note"] == "分期部分还本")
        assert repay_tx["amount"] == 600.0

        plans = _plans(client, hdr, ledger_id)
        assert plans[0]["status"] == "active", "还没还清,计画维持 active"
    finally:
        app.dependency_overrides.clear()


def test_installment_early_repay_principal_full_amount_settles_plan():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrepay2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREPAY2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrepay2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/early-repay-principal",
            headers=hdr,
            json={"base_change_id": base, "payment_amount": 1200.0},
        )
        assert res.status_code == 200, res.text

        assert _periods(client, hdr, ledger_id, plan_id) == []
        plans = _plans(client, hdr, ledger_id)
        assert plans[0]["status"] == "settled"

        txs = _transactions(client, hdr, ledger_id)
        # 原 12 期交易全部删除,只留一笔还本交易
        assert len(txs) == 1
        assert txs[0]["note"] == "分期部分还本"
        assert txs[0]["amount"] == 1200.0
    finally:
        app.dependency_overrides.clear()


def test_installment_payoff_deletes_future_periods_and_generates_settlement_tx():
    client, _TS = _make_client()
    try:
        owner = _register(client, "inspayoff1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSPAYOFF1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "inspayoff1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/payoff",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        assert _periods(client, hdr, ledger_id, plan_id) == []
        plans = _plans(client, hdr, ledger_id)
        assert plans[0]["status"] == "settled"

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1, "原 12 期交易全被提前结清删除,只留一笔结清交易"
        assert txs[0]["note"] == "分期提前结清"
        assert txs[0]["amount"] == 1200.0
    finally:
        app.dependency_overrides.clear()


def test_installment_terminate_future_deletes_without_settlement_tx():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insterm1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSTERM1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insterm1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 12,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/terminate-future",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        assert _periods(client, hdr, ledger_id, plan_id) == []
        plans = _plans(client, hdr, ledger_id)
        assert plans[0]["status"] == "terminated"

        txs = _transactions(client, hdr, ledger_id)
        assert txs == [], "终止未来分期不生成结清交易,全部未到期期直接删除"
    finally:
        app.dependency_overrides.clear()
