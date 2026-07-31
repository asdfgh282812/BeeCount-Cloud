"""拆帳(§2.4 MOZE_FEATURE_GAP_SD.md Phase 2)—— 一笔交易拆成多个分类明细:

- `WriteTransactionCreateRequest/UpdateRequest.splits` 校验:tx_type 只能
  expense/income、至少 2 笔、金额加总须等于交易 amount、每笔金额 > 0 且带
  category
- has_splits=True 时父行 category_sync_id/category_name 清空,明细落
  `read_tx_split_projection`(整批 delete-then-insert)
- 退款互斥:拆帳交易不能作为 refund_of 目标(整笔退款),也不能同时是
  splits + refund_of_id
- Web PATCH fast path(`update_transaction`)跟 mobile `/sync/push` merge
  路径都要满足"没传 splits key 时保留旧值,传空列表清空"的 LWW 语义
- `read/workspace.py::workspace_analytics` 分类排行按 split 明细展开分别
  累加,不是整笔归到(此时已是 NULL 的)父行 category
- 分类预算用量(`GET /ledgers/{id}/budgets/usage`)要把 split 明细计入对应
  分类,不能因为父行 category_sync_id 是 NULL 而漏算

============================================================================
手动检查清单(server 端契约已有上面的自动化测试覆盖,这份清单留给要在
Web UI 肉眼过一遍完整流程的场景使用,见 docs/PH2_SPLIT_WEB_UI_MANUAL_TEST_PLAN.md):
============================================================================
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection, ReadTxSplitProjection


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


def _create_category(client, hdr, ledger_id, token, name, kind="expense"):
    base = _latest_change_id(client, token, ledger_id)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/categories",
        headers=hdr,
        json={"base_change_id": base, "name": name, "kind": kind, "level": 1},
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/categories", headers=hdr)
    assert r.status_code == 200
    return next(c["id"] for c in r.json() if c["name"] == name)


def _setup(email, ledger_id="L_SPLIT1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    cat_food = _create_category(client, hdr, ledger_id, token, "餐饮")
    cat_transport = _create_category(client, hdr, ledger_id, token, "交通")
    return client, TS, token, hdr, ledger_id, cat_food, cat_transport


def _create_tx(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    now = datetime.now(timezone.utc)
    payload = {
        "base_change_id": base,
        "tx_type": "expense",
        "amount": 200.0,
        "happened_at": now.isoformat(),
    }
    payload.update(overrides)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json=payload,
    )


# ---------------------------------------------------------------------------
# 建交易带 splits:落库 + 读接口透传
# ---------------------------------------------------------------------------


def test_create_tx_with_splits_persists_and_reads_back():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split1@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0, "note": "午饭"},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row is not None
            assert row.has_splits is True
            assert row.category_sync_id is None
            assert row.category_name is None
            split_rows = db.scalars(
                select(ReadTxSplitProjection)
                .where(ReadTxSplitProjection.tx_sync_id == tx_id)
                .order_by(ReadTxSplitProjection.sort_order)
            ).all()
            assert [s.category_sync_id for s in split_rows] == [cat_food, cat_transport]
            assert [s.amount for s in split_rows] == [150.0, 50.0]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        tx = next(t for t in r.json() if t["id"] == tx_id)
        assert tx["has_splits"] is True
        assert tx["category_id"] is None
        assert len(tx["splits"]) == 2
        assert {s["category_id"] for s in tx["splits"]} == {cat_food, cat_transport}
    finally:
        app.dependency_overrides.clear()


def test_create_tx_splits_amount_mismatch_rejected():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split2@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 40.0},
            ],
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_create_tx_splits_needs_at_least_two():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split3@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[{"category_id": cat_food, "amount": 200.0}],
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_create_tx_splits_rejects_transfer_type():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split4@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            tx_type="transfer",
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 100.0},
                {"category_id": cat_transport, "amount": 100.0},
            ],
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_create_tx_splits_and_refund_of_id_together_rejected():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split5@example.com")
    try:
        res = _create_tx(client, hdr, ledger_id, token, amount=100.0, category_name="餐饮")
        assert res.status_code == 200, res.text
        expense_id = res.json()["entity_id"]

        res = _create_tx(
            client, hdr, ledger_id, token,
            tx_type="income",
            amount=100.0,
            refund_of_id=expense_id,
            splits=[
                {"category_id": cat_food, "amount": 60.0},
                {"category_id": cat_transport, "amount": 40.0},
            ],
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_refund_of_split_transaction_rejected():
    """§2.12.3:多分類（拆帳）交易不能整筆退款。"""
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split6@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        split_tx_id = res.json()["entity_id"]

        res = _create_tx(
            client, hdr, ledger_id, token,
            tx_type="income",
            amount=200.0,
            refund_of_id=split_tx_id,
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# PATCH:LWW 语义 —— 没传 splits 保留旧值,传 [] 清空,merge 后重新校验金额
# ---------------------------------------------------------------------------


def test_patch_add_splits_to_plain_tx():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split7@example.com")
    try:
        res = _create_tx(client, hdr, ledger_id, token, amount=200.0, category_name="其它")
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={
                "base_change_id": base,
                "splits": [
                    {"category_id": cat_food, "amount": 120.0},
                    {"category_id": cat_transport, "amount": 80.0},
                ],
            },
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.has_splits is True
            assert row.category_sync_id is None
            count = db.scalar(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == tx_id)
            )
            assert count is not None
    finally:
        app.dependency_overrides.clear()


def test_patch_other_field_keeps_existing_splits():
    """fast path 只改 note,不传 splits key,原有 splits 必须原样保留。"""
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split8@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "note": "改个备注"},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.note == "改个备注"
            assert row.has_splits is True
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == tx_id)
            ).all()
            assert len(split_rows) == 2
    finally:
        app.dependency_overrides.clear()


def test_patch_empty_splits_clears_back_to_plain_tx():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split9@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "splits": [], "category_id": cat_food},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.has_splits is False
            assert row.category_sync_id == cat_food
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == tx_id)
            ).all()
            assert split_rows == []
    finally:
        app.dependency_overrides.clear()


def test_patch_amount_only_must_still_match_existing_splits_sum():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split10@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "amount": 300.0},
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_delete_split_tx_cleans_up_split_rows():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split11@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == tx_id)
            ).all()
            assert split_rows == []
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约:partial update 不带 splits 时保留旧值,
# 显式空列表清空
# ---------------------------------------------------------------------------


def test_mobile_push_partial_update_keeps_existing_splits():
    client, TS = _make_client()
    try:
        tok = _register(client, "splitmerge@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        now = _iso()

        _push(client, hdr, "lg1", "transaction", "tx-split-1", {
            "syncId": "tx-split-1",
            "type": "expense",
            "amount": 200.0,
            "happenedAt": now,
            "splits": [
                {"categoryId": "cat-a", "categoryName": "餐饮", "amount": 150.0, "sortOrder": 0},
                {"categoryId": "cat-b", "categoryName": "交通", "amount": 50.0, "sortOrder": 1},
            ],
        })
        # partial update:只改 note,不带 splits
        _push(client, hdr, "lg1", "transaction", "tx-split-1", {
            "syncId": "tx-split-1",
            "note": "备注",
        })

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-split-1")
            )
            assert row is not None
            assert row.note == "备注"
            assert row.has_splits is True, "partial update 不带 splits key 时必须保留旧 splits"
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == "tx-split-1")
            ).all()
            assert len(split_rows) == 2

        # 显式传空列表 → 清空
        _push(client, hdr, "lg1", "transaction", "tx-split-1", {
            "syncId": "tx-split-1",
            "splits": [],
        })
        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-split-1")
            )
            assert row.has_splits is False
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == "tx-split-1")
            ).all()
            assert split_rows == []
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 统计口径:分类排行按 split 明细展开
# ---------------------------------------------------------------------------


def test_workspace_analytics_explodes_split_categories():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split12@example.com")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "category_name": "餐饮", "amount": 150.0},
                {"category_id": cat_transport, "category_name": "交通", "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text

        # 一笔普通(非拆帳)支出,验证不受影响
        res = _create_tx(client, hdr, ledger_id, token, amount=30.0, category_name="餐饮")
        assert res.status_code == 200, res.text

        r = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "all", "metric": "expense", "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["expense_total"] == 230.0

        ranks = {row["category_name"]: row["total"] for row in body["category_ranks"]}
        assert ranks["餐饮"] == 150.0 + 30.0
        assert ranks["交通"] == 50.0
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 分类预算用量:split 明细要计入对应分类
# ---------------------------------------------------------------------------


def test_category_budget_usage_counts_split_legs():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("split13@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers=hdr,
            json={
                "base_change_id": base,
                "type": "category",
                "category_id": cat_food,
                "amount": 500,
            },
        )
        assert res.status_code == 200, res.text
        budget_id = res.json()["entity_id"]

        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=200.0,
            splits=[
                {"category_id": cat_food, "amount": 150.0},
                {"category_id": cat_transport, "amount": 50.0},
            ],
        )
        assert res.status_code == 200, res.text

        # 一笔普通(非拆帳)支出也算进同一分类
        res = _create_tx(client, hdr, ledger_id, token, amount=30.0, category_id=cat_food)
        assert res.status_code == 200, res.text

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/budgets/usage", headers=hdr)
        assert r.status_code == 200, r.text
        usage = {item["budget_id"]: item["used"] for item in r.json()["items"]}
        assert usage[budget_id] == 150.0 + 30.0
    finally:
        app.dependency_overrides.clear()
