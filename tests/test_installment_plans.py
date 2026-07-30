"""分期付款(§2.3 MOZE_FEATURE_GAP_SD.md Phase 1)—— installment_plan entity 契约:

- web `/write/ledgers/{id}/installment-plans` 创建计画时同事务生成第一期交易
  (`paid_periods=1`),交易带 `installment_plan_id` 反查
- mobile `/sync/push` 的 `installment_plan` merge 契约(partial update 保留旧值)
- `services.recurring_materializer.materialize_due_installment_plans`:
  到期后续期生成交易 + 推进 paid_periods/next_period_at + 付满后 status='settled'
  + 落一条通知

============================================================================
手动检查清单(跟 test_recurring_rules.py 同款,pytest 覆盖不到的运行时行为):

1. `POST /api/v1/internal/tasks/materialize-recurring`(admin token)手动触发
   一次物化,确认返回体 `installment_transactions` 计数符合预期。
2. `sqlite3 beecount.db` 查
   `SELECT * FROM read_installment_plan_projection;` 确认
   `paid_periods`/`next_period_at`/`status` 都正确推进 / 结清。
3. `SELECT * FROM read_tx_projection WHERE installment_plan_sync_id = '<id>';`
   应该能看到跟 periods 数一致的交易笔数(结清前是 paid_periods 笔)。
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
from src.models import Notification, ReadInstallmentPlanProjection, ReadTxProjection
from src.services.recurring_materializer import materialize_due_installment_plans


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
# Web write:建计画同时生成第一期交易
# ---------------------------------------------------------------------------


def test_create_installment_plan_generates_first_period_tx():
    client, _TS = _make_client()
    try:
        owner = _register(client, "ins1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INS1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ins1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc)
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

        # 计画本身:paid_periods=1,period_amount=100
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
        )
        assert res.status_code == 200, res.text
        plans = res.json()
        assert len(plans) == 1
        assert plans[0]["id"] == plan_id
        assert plans[0]["total_amount"] == 1200.0
        assert plans[0]["periods"] == 12
        assert plans[0]["period_amount"] == 100.0
        assert plans[0]["paid_periods"] == 1
        assert plans[0]["status"] == "active"

        # 第一期交易已经生成,带 installment_plan_id 反查
        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/transactions",
            headers=hdr,
        )
        assert res.status_code == 200, res.text
        txs = res.json()
        assert len(txs) == 1
        assert txs[0]["amount"] == 100.0
        assert txs[0]["installment_plan_id"] == plan_id
        assert txs[0]["tx_type"] == "expense"
    finally:
        app.dependency_overrides.clear()


def test_update_installment_plan_can_settle_early():
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
                "first_period_at": datetime.now(timezone.utc).isoformat(),
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

        res = client.get(f"/api/v1/read/ledgers/{ledger_id}/installment-plans", headers=hdr)
        plans = res.json()
        assert plans[0]["status"] == "settled"
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
            assert row.paid_periods == 1
            assert row.status == "active"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 到期物化:后续各期
# ---------------------------------------------------------------------------


def test_materialize_due_installment_generates_next_period_and_settles():
    client, TS = _make_client()
    try:
        owner = _register(client, "insmat1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSMAT1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insmat1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # 2 期计画,首期建计画时就生成;把 next_period_at 直接改到过去,
        # 让 materializer 一跑就能补第二期并结清。
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 200.0,
                "periods": 2,
                "first_period_at": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        with TS() as db:
            plan_row = db.scalar(
                select(ReadInstallmentPlanProjection).where(
                    ReadInstallmentPlanProjection.sync_id == plan_id,
                )
            )
            assert plan_row.paid_periods == 1
            ledger_internal_id = plan_row.ledger_id
            user_id = plan_row.user_id
            # 强制过期,不依赖真实时间推进
            plan_row.next_period_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()

            generated = materialize_due_installment_plans(db)
            db.commit()
            assert generated == 1

            txs = db.scalars(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                )
            ).all()
            assert len(txs) == 2, "首期(建计画时)+ 第二期(materializer)共 2 笔"
            assert all(t.installment_plan_sync_id == plan_id for t in txs)

            refreshed_plan = db.scalar(
                select(ReadInstallmentPlanProjection).where(
                    ReadInstallmentPlanProjection.sync_id == plan_id,
                )
            )
            assert refreshed_plan.paid_periods == 2
            assert refreshed_plan.status == "settled"

            notif = db.scalar(select(Notification).where(Notification.user_id == user_id))
            assert notif is not None
            assert "结清" in notif.title

        # 已结清,再跑一次不该继续生成
        with TS() as db:
            generated_again = materialize_due_installment_plans(db)
            db.commit()
            assert generated_again == 0
    finally:
        app.dependency_overrides.clear()
