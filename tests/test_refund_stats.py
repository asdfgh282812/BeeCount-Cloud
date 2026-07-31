"""退款(§2.6/§2.12.3 MOZE_FEATURE_GAP_SD.md Phase 1/1.5)—— refund_of_sync_id
契约:

- web `/write/ledgers/{id}/transactions` 建交易可带 `refund_of_id`,落
  `read_tx_projection.refund_of_sync_id`,读接口原样透传
- mobile `/sync/push` 的 transaction merge 契约:partial update 不带
  `refundOfId` 时保留旧值
- 统计口径:退款不计入自己那个分项,改冲抵对方分项净额 —— income 型退款
  (退一笔支出)冲抵 expense,expense 型退款(退一笔收入)冲抵 income。
  `/summary`(_projection_totals 共享给 list_ledgers)与 `/workspace/
  analytics`(workspace_analytics)两处都要验证
- Phase 1.5:一笔交易已经被退过款就不能再发起第二笔退款(create/update
  两条路径都要挡),命中回 400 `TX_ALREADY_REFUNDED`

Web UI 入口(2026-07-30 起)在交易详情弹窗的「退款」按钮
(`TransactionDetailDialog.tsx`),本文件只覆盖 server 端契约;UI 层的
按钮 disabled / 双向勾稽跳转是纯前端逻辑,没有对应的 pytest。

============================================================================
手动检查清单(server 端契约已有上面的自动化测试覆盖,这份清单留给要在
Web UI 肉眼过一遍完整流程的场景使用):

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


def test_workspace_analytics_nets_refund_of_split_transaction():
    """2026-07-31 起,拆帳(§2.4)交易也能整笔退款 —— 退款只冲抵全局 expense
    净额,不会拆回原交易「餐饮/交通」两个分类明细各自的金额(跟普通退款
    "不追溯回被退那笔交易原本的分类"是同一套简化口径,见 workspace.py
    is_income_refund 那段注释)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "ref9@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REF9"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "ref9@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/categories",
            headers=hdr,
            json={"base_change_id": base, "name": "餐饮", "kind": "expense", "level": 1},
        )
        assert res.status_code == 200, res.text
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/categories",
            headers=hdr,
            json={"base_change_id": base, "name": "交通", "kind": "expense", "level": 1},
        )
        assert res.status_code == 200, res.text
        cats = client.get(f"/api/v1/read/ledgers/{ledger_id}/categories", headers=hdr).json()
        cat_food = next(c["id"] for c in cats if c["name"] == "餐饮")
        cat_transport = next(c["id"] for c in cats if c["name"] == "交通")

        now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 200.0,
                "happened_at": now.isoformat(),
                "splits": [
                    {"category_id": cat_food, "category_name": "餐饮", "amount": 150.0},
                    {"category_id": cat_transport, "category_name": "交通", "amount": 50.0},
                ],
            },
        )
        assert res.status_code == 200, res.text
        split_tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "income",
                "amount": 80.0,
                "happened_at": (now + timedelta(hours=2)).isoformat(),
                "category_name": "退款",
                "refund_of_id": split_tx_id,
            },
        )
        assert res.status_code == 200, res.text

        res = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "month", "metric": "expense"},
        )
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        # 200(拆帳原交易) - 80(退款) = 120
        assert summary["expense_total"] == 120.0

        ranks = {r["category_name"]: r["total"] for r in res.json()["category_ranks"]}
        # 拆帳明细金额本身不受退款影响 —— 退款净额只冲抵全局 expense_total,
        # 不会按比例拆回「餐饮」「交通」两个分类各自扣减。
        assert ranks.get("餐饮") == 150.0
        assert ranks.get("交通") == 50.0
        # 退款自己那个分类("退款")因为是净负值,不会出现在 expense 排行里。
        assert "退款" not in ranks
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 1.5:禁止重复退款(一笔交易只能被退一次)
# ---------------------------------------------------------------------------


def test_create_second_refund_rejected_with_already_refunded_code():
    """expense 已经被一笔 income 退款过 → 再建第二笔指向同一笔 expense 的
    退款交易必须被拒绝(400 TX_ALREADY_REFUNDED),不是静默允许多笔退款。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "refdup1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFDUP1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refdup1@example.com")
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
                "amount": 500.0,
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
                "amount": 200.0,
                "happened_at": (now + timedelta(hours=1)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 200, res.text

        # 第二笔退款(哪怕是不同金额的部分退款)必须被拒绝。
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "income",
                "amount": 100.0,
                "happened_at": (now + timedelta(hours=2)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 400, res.text
        assert res.json()["error"]["code"] == "TX_ALREADY_REFUNDED"
    finally:
        app.dependency_overrides.clear()


def test_update_tx_rejects_pointing_refund_of_id_to_already_refunded_target():
    """PATCH 一笔既有交易,把 refund_of_id 指向一笔已经被退过款的交易 ——
    跟 create 路径同一道防呆,update 路径(_commit_write_fast_tx)也要挡。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "refdup2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFDUP2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refdup2@example.com")
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
                "amount": 500.0,
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
                "amount": 200.0,
                "happened_at": (now + timedelta(hours=1)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 200, res.text

        # 建一笔不相干的普通收入,之后想改成第二笔退款。
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "income",
                "amount": 300.0,
                "happened_at": (now + timedelta(hours=2)).isoformat(),
                "category_name": "工资",
            },
        )
        assert res.status_code == 200, res.text
        other_income_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{other_income_id}",
            headers=hdr,
            json={"base_change_id": base, "refund_of_id": expense_id},
        )
        assert res.status_code == 400, res.text
        assert res.json()["error"]["code"] == "TX_ALREADY_REFUNDED"
    finally:
        app.dependency_overrides.clear()


def test_create_refund_of_refund_tx_rejected():
    """退款交易本身(refund_of_id 已经指向别的交易)不能再被当成退款目标 ——
    不允许链式退款(A 退 B,再有 C 想退 B 那笔退款交易)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "refchain1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFCHAIN1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refchain1@example.com")
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
                "amount": 500.0,
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
                "amount": 200.0,
                "happened_at": (now + timedelta(hours=1)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # 想对那笔「退款交易」本身再建一笔退款 —— 必须被拒绝。
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 50.0,
                "happened_at": (now + timedelta(hours=2)).isoformat(),
                "category_name": "购物",
                "refund_of_id": refund_id,
            },
        )
        assert res.status_code == 400, res.text
        assert res.json()["error"]["code"] == "TX_REFUND_CHAIN_FORBIDDEN"
    finally:
        app.dependency_overrides.clear()


def test_update_existing_refund_tx_can_resave_other_fields():
    """编辑一笔已经是退款的交易(只改 note,不碰 refund_of_id)不应该被
    "已经退过款"防呆误伤 —— exclude_unset 下 refund_of_id 压根不在 payload
    里,不用重新校验;这条测试锁住这个不回归。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "refdup3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFDUP3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refdup3@example.com")
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
                "amount": 500.0,
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
                "amount": 200.0,
                "happened_at": (now + timedelta(hours=1)).isoformat(),
                "category_name": "退款",
                "refund_of_id": expense_id,
            },
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # 场景一:PATCH 不带 refund_of_id(exclude_unset)—— 必须放行。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{refund_id}",
            headers=hdr,
            json={"base_change_id": base, "note": "已核对"},
        )
        assert res.status_code == 200, res.text

        # 场景二:PATCH 显式带回同一个 refund_of_id(前端编辑表单的真实行为,
        # 见 GlobalEditDialogs.tsx)—— 排除自己后也必须放行,不能自己挡自己。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{refund_id}",
            headers=hdr,
            json={"base_change_id": base, "refund_of_id": expense_id, "note": "再次核对"},
        )
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# income 也能被退款(§2.6 Phase 1.5 项 3):netting 方向对称
# ---------------------------------------------------------------------------


def _setup_income_and_refund(client, hdr, ledger_id, token):
    """salary(income,1000)被一笔 expense 型退款(200)冲抵;另有一笔不相干
    的普通支出(500)不应受影响 —— 跟 _setup_expense_and_refund 方向相反。"""
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
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
    income_id = res.json()["entity_id"]

    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": base,
            "tx_type": "expense",
            "amount": 200.0,
            "happened_at": (now + timedelta(hours=2)).isoformat(),
            "category_name": "工资",
            "refund_of_id": income_id,
        },
    )
    assert res.status_code == 200, res.text

    # 一笔普通支出,不应受退款 netting 影响
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
    return income_id


def test_summary_nets_refund_of_income_against_income():
    client, _TS = _make_client()
    try:
        owner = _register(client, "refinc1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFINC1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refinc1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _setup_income_and_refund(client, hdr, ledger_id, token)

        res = client.get(f"/api/v1/read/summary?ledger_id={ledger_id}", headers=hdr)
        assert res.status_code == 200, res.text
        body = res.json()
        # income: 1000 - 200(退款净额) = 800;expense: 只有普通支出 500(退款不计入)
        assert body["income_total"] == 800.0
        assert body["expense_total"] == 500.0
        # balance 口径不受影响:1000 - 500 - 200(退款仍按 expense 记负号) = 300
        assert body["balance"] == 300.0
    finally:
        app.dependency_overrides.clear()


def test_workspace_analytics_nets_refund_of_income_against_income():
    client, _TS = _make_client()
    try:
        owner = _register(client, "refinc2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REFINC2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "refinc2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _setup_income_and_refund(client, hdr, ledger_id, token)

        res = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "month", "metric": "income"},
        )
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        assert summary["income_total"] == 800.0
        assert summary["expense_total"] == 500.0
    finally:
        app.dependency_overrides.clear()
