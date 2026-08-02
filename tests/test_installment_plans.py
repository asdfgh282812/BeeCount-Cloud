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


def test_create_installment_plan_with_offset_existing_balance_zeroes_out_card_debt():
    """§2.3 補強(2026-08-02 第三輪,對齊 Moze「Bill Installment」設計 +
    使用者反饋 #4):把信用卡已經欠下的帳單轉成分期時,`offset_existing_
    balance=true` 應該把原本那筆消費算進去的應繳金額「清空」——但**不**
    產生任何真實交易(沖銷款不該出現在交易明細,是純內部記帳調整),透過
    billing-summary 的 `remaining_due` 驗證沖銷生效。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "insoffset1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSOFFSET1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insoffset1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        hdr_app = {"Authorization": f"Bearer {app_token}"}

        # 先建一张信用卡 + 一笔既有消费(模拟已经欠下的帳單,日期特意选在
        # 40 天前,确保不管测试跑在当月哪一天,都稳稳落在"已结束的帐单周期"
        # 里,不受 billing_day 对齐影响)—— sync push 走 app scope。
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 25, "paymentDueDay": 10}, device_id=device)
        old_charge_at = datetime.now(timezone.utc) - timedelta(days=40)
        _push(client, hdr_app, ledger_id, "transaction", "tx-existing",
              {"syncId": "tx-existing", "type": "expense", "amount": 1200.0,
               "happenedAt": _iso(old_charge_at), "accountId": "acc-card", "accountName": "卡"},
              device_id=device)

        summary_before = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/accounts/acc-card/billing-summary",
            headers=hdr,
        )
        assert summary_before.status_code == 200, summary_before.text
        assert summary_before.json()["remaining_due"] == 1200.0

        first_period_at = datetime.now(timezone.utc) + timedelta(days=30)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
                "account_id": "acc-card",
                "offset_existing_balance": True,
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        # 既有消费 1 笔 + 分期 3 期 expense = 4 笔 —— 沖銷不落地成交易。
        assert len(txs) == 4
        assert all(t["tx_type"] != "income" for t in txs)
        expense_txs = [t for t in txs if t["tx_type"] == "expense"]
        assert len(expense_txs) == 4
        installment_expense_txs = [t for t in expense_txs if t["installment_plan_id"] == plan_id]
        assert len(installment_expense_txs) == 3

        # 沖銷生效:原本 1200 應繳,建完分期後應繳归零(分期各期都排到未来,
        # 还没到期不计入"已发生"金额)。
        summary_after = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/accounts/acc-card/billing-summary",
            headers=hdr,
        )
        assert summary_after.status_code == 200, summary_after.text
        assert summary_after.json()["remaining_due"] == 0.0

        # 需求 #3(2026-08-02):删除整个分期计画后,沖銷連带失效,帳單变回
        # 原本尚未缴费的状态。
        base2 = _latest_change_id(client, token, ledger_id)
        del_res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}",
            headers=hdr,
            json={"base_change_id": base2},
        )
        assert del_res.status_code == 200, del_res.text
        summary_after_delete = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/accounts/acc-card/billing-summary",
            headers=hdr,
        )
        assert summary_after_delete.status_code == 200, summary_after_delete.text
        assert summary_after_delete.json()["remaining_due"] == 1200.0
    finally:
        app.dependency_overrides.clear()


def test_create_installment_plan_rejects_when_no_outstanding_balance():
    """需求 #1(2026-08-02 使用者反饋):已經繳清、沒有欠款的信用卡帳單不能
    再轉成分期,沒有東西可以沖銷。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "insoffset3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSOFFSET3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insoffset3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        hdr_app = {"Authorization": f"Bearer {app_token}"}

        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 25, "paymentDueDay": 10}, device_id=device)
        # 没有任何消费,remaining_due 恒为 0。

        first_period_at = datetime.now(timezone.utc) + timedelta(days=30)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 500.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
                "account_id": "acc-card",
                "offset_existing_balance": True,
            },
        )
        assert res.status_code == 400, res.text
        assert res.json()["error"]["code"] == "INSTALLMENT_NO_OUTSTANDING_BALANCE"
    finally:
        app.dependency_overrides.clear()


def test_create_installment_plan_on_account_group_distributes_offset_to_children():
    """需求 #2(2026-08-02 使用者反饋):如果信用卡掛靠了主帳戶(群組),分期
    應該以主帳戶為單位建立,而不是要求使用者手動選一張子卡 —— `account_id`
    直接傳群組自己的 id,server 端自動把沖銷金額分攤到各個子帳戶身上。子卡
    直接被拿来当 `account_id` 应该被拒绝(必须透過群组)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "insoffset4@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSOFFSET4"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insoffset4@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}
        hdr_app = {"Authorization": f"Bearer {app_token}"}

        _push(client, hdr_app, ledger_id, "account", "acc-group",
              {"syncId": "acc-group", "name": "X 銀行", "type": "account_group", "currency": "CNY",
               "billingDay": 25, "paymentDueDay": 10}, device_id=device)
        _push(client, hdr_app, ledger_id, "account", "acc-cube",
              {"syncId": "acc-cube", "name": "cube卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id=device)
        _push(client, hdr_app, ledger_id, "account", "acc-shopee",
              {"syncId": "acc-shopee", "name": "蝦皮聯名卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id=device)
        old_charge_at = datetime.now(timezone.utc) - timedelta(days=40)
        _push(client, hdr_app, ledger_id, "transaction", "tx-cube",
              {"syncId": "tx-cube", "type": "expense", "amount": 800.0,
               "happenedAt": _iso(old_charge_at), "accountId": "acc-cube", "accountName": "cube卡"},
              device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "tx-shopee",
              {"syncId": "tx-shopee", "type": "expense", "amount": 400.0,
               "happenedAt": _iso(old_charge_at), "accountId": "acc-shopee", "accountName": "蝦皮聯名卡"},
              device_id=device)

        # 直接選子卡应该被拒绝——必须透過群组。
        first_period_at = datetime.now(timezone.utc) + timedelta(days=30)
        base = _latest_change_id(client, token, ledger_id)
        rejected = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 800.0,
                "periods": 2,
                "first_period_at": first_period_at.isoformat(),
                "account_id": "acc-cube",
                "offset_existing_balance": True,
            },
        )
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["error"]["code"] == "INSTALLMENT_ACCOUNT_IS_GROUP_MEMBER"

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 1200.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
                "account_id": "acc-group",
                "offset_existing_balance": True,
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]

        # 分期各期的交易应该挂在主帳戶(群组)自己身上(2026-08-03 第四轮改版,
        # 对齐使用者反馈「每期分期金额应该附属主帳戶,而非个别卡片」),不再
        # 任选一张子卡。
        txs = _transactions(client, hdr, ledger_id)
        installment_txs = [t for t in txs if t.get("installment_plan_id") == plan_id]
        assert len(installment_txs) == 3
        assert all(t["account_id"] == "acc-group" for t in installment_txs)

        summary = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/accounts/acc-group/billing-summary",
            headers=hdr,
        )
        assert summary.status_code == 200, summary.text
        # 两张子卡的欠款都被沖銷:800 + 400 = 1200 应繳归零。
        assert summary.json()["remaining_due"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_create_installment_plan_offset_requires_account_id():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insoffset2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSOFFSET2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insoffset2@example.com")
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
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
                "offset_existing_balance": True,
            },
        )
        assert res.status_code == 400
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
        # 第 6/7/9-12 期被重算(利率大幅调高,合计金额应该变了)
        # 注意:不比较 interest_amount 本身——round_amounts 取整到整数金额后,
        # 利率调整前后的利息raw值可能凑巧四舍五入到同一个整数(小额、小利率差
        # 时很容易发生),total_amount 变动幅度更大,不会有这种巧合碰撞。
        assert periods_after[6]["total_amount"] != periods_before[5]["total_amount"]
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


# ---------------------------------------------------------------------------
# 单期退款(§2.6/§2.12.1):跟"整笔退款"(直接 DELETE 整个计划)是互斥的
# 两个前端选项 —— 这里只测单期退款那条路径,整笔删除已有
# test_installment_plan_delete_cascades_periods_and_transactions 覆盖。
# ---------------------------------------------------------------------------


def test_installment_refund_period_marks_refunded_and_creates_income_tx():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrefund1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREFUND1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrefund1@example.com")
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

        periods_before = _periods(client, hdr, ledger_id, plan_id)
        target = periods_before[0]
        original_tx_id = target["tx_id"]
        assert target["status"] == "generated"
        assert target["refund_tx_id"] is None

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
            headers=hdr,
            json={"base_change_id": base, "tx_id": original_tx_id},
        )
        assert res.status_code == 200, res.text
        refund_tx_id = res.json()["entity_id"]
        assert refund_tx_id != original_tx_id

        periods_after = _periods(client, hdr, ledger_id, plan_id)
        refunded = next(p for p in periods_after if p["tx_id"] == original_tx_id)
        assert refunded["status"] == "refunded"
        assert refunded["refund_tx_id"] == refund_tx_id
        assert refunded["refund_amount"] == 100.0
        assert refunded["refunded_at"] is not None
        # 其它 11 期不受影响
        others = [p for p in periods_after if p["tx_id"] != original_tx_id]
        assert all(p["status"] == "generated" for p in others)

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 13, "原 12 期 expense + 1 笔退款 income,原交易不删除"
        original_tx = next(t for t in txs if t["id"] == original_tx_id)
        assert original_tx["tx_type"] == "expense"
        assert original_tx["amount"] == 100.0, "原交易金额/内容不受退款影响"
        refund_tx = next(t for t in txs if t["id"] == refund_tx_id)
        assert refund_tx["tx_type"] == "income"
        assert refund_tx["amount"] == 100.0
        assert refund_tx["refund_of_id"] == original_tx_id
        assert refund_tx["installment_plan_id"] is None, (
            "退款交易本身不算这个计划管理的一期,不打 installmentPlanId,"
            "否则会被 fast-path 的「分期交易不能直接删」防呆挡住用户改/删这笔退款"
        )
    finally:
        app.dependency_overrides.clear()


def test_installment_refund_period_custom_amount_and_note():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrefund2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREFUND2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrefund2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 300.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]
        original_tx_id = _periods(client, hdr, ledger_id, plan_id)[0]["tx_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_id": original_tx_id,
                "amount": 50.0,
                "note": "只退一半",
            },
        )
        assert res.status_code == 200, res.text
        refund_tx_id = res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        refund_tx = next(t for t in txs if t["id"] == refund_tx_id)
        assert refund_tx["amount"] == 50.0
        assert refund_tx["note"] == "只退一半"
    finally:
        app.dependency_overrides.clear()


def test_installment_refund_period_rejects_double_refund():
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrefund3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREFUND3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrefund3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 300.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]
        original_tx_id = _periods(client, hdr, ledger_id, plan_id)[0]["tx_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
            headers=hdr,
            json={"base_change_id": base, "tx_id": original_tx_id},
        )
        assert res.status_code == 200, res.text

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
            headers=hdr,
            json={"base_change_id": base, "tx_id": original_tx_id},
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_installment_plan_delete_cascades_periods_and_transactions_after_partial_refund():
    """整笔退款(前端第二个选项)复用既有的 DELETE 整个计划端点 —— 这里确认
    即使某一期已经单独退过款,整笔删除仍然能正常级联清掉所有期数 + 交易
    (含那笔单独退款生成的 income 交易本身不受影响,只是跟计划无关的普通交易,
    删计划不会牵连到它)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "insrefund4@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_INSREFUND4"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "insrefund4@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        first_period_at = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans",
            headers=hdr,
            json={
                "base_change_id": base,
                "total_amount": 300.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]
        original_tx_id = _periods(client, hdr, ledger_id, plan_id)[0]["tx_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
            headers=hdr,
            json={"base_change_id": base, "tx_id": original_tx_id},
        )
        assert res.status_code == 200, res.text
        refund_tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/installment-plans/{plan_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        assert _plans(client, hdr, ledger_id) == []
        assert _periods(client, hdr, ledger_id, plan_id) == []
        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1, "3 期 expense + 退款生成的 income 都要被删,只剩退款交易本身"
        assert txs[0]["id"] == refund_tx_id
    finally:
        app.dependency_overrides.clear()


def test_installment_period_tx_amount_date_account_cannot_be_edited_directly():
    """2026-08-03 使用者反饋 #4:分期產生的交易雖然可以編輯,但不能單獨改
    金額/日期/帳戶(會讓 read_installment_period_projection 跟這筆 tx 脫鉤),
    要走專門的 installment 端點(單期編輯/rebalance-from/提前還本/提前結清)。
    note 不影響 period 排程,不受此限制。"""
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
                "total_amount": 300.0,
                "periods": 3,
                "first_period_at": first_period_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        plan_id = res.json()["entity_id"]
        period = _periods(client, hdr, ledger_id, plan_id)[0]
        tx_id = period["tx_id"]

        for field, value in (
            ("amount", 999.0),
            ("happened_at", (first_period_at + timedelta(days=5)).isoformat()),
        ):
            base = _latest_change_id(client, token, ledger_id)
            res = client.patch(
                f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
                headers=hdr,
                json={"base_change_id": base, field: value},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "TX_UPDATE_INSTALLMENT_LINKED"

        # note 不受影响,可以直接编辑。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "note": "第一期备注"},
        )
        assert res.status_code == 200, res.text
        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[tx_id]["note"] == "第一期备注"
        assert txs[tx_id]["amount"] == 100.0, "上面两次被拒绝的 amount/happened_at 编辑不应该生效"
    finally:
        app.dependency_overrides.clear()
