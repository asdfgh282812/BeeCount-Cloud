"""專案詳情頁:期間切換 + 分類花費拆解(docs/2026-09-06-project-category-
budget-period-switch-design.md §4)—— `GET /read/ledgers/{id}/projects/{id}
/breakdown` 契约:

- 統計條(出帳/入帳)只看 `exclude_from_stats`;預算用量(`spent`)/收入併入
  預算(`income_included_in_budget`)只看 `exclude_from_budget`,兩者獨立。
- `income_included_in_budget=True` 時 `effective_budget = budget_amount +
  當期收入`,`remaining`/`progress_pct`/`status` 都改用 `effective_budget`。
- 分類拆解三組:本期有交易+有分配 → `allocated_categories`;本期有交易+無
  分配 → `unallocated_categories`;本期零交易(不論有無分配) →
  `unset_categories`。
- `period_offset` 往回推算monthly/yearly 期數;`fixed` 週期忽略 offset。
- `GET /read/ledgers/{id}/transactions` 新增 `project_id`/`category_id`
  篩選(供詳情頁點分類列鑽取交易明細)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _setup(email, ledger_id="L_PBD1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, token, hdr, ledger_id


def _create_category(client, hdr, ledger_id, token, *, name, kind="expense"):
    base = _latest_change_id(client, token, ledger_id)
    payload = {"base_change_id": base, "name": name, "kind": kind, "level": 1}
    res = client.post(f"/api/v1/write/ledgers/{ledger_id}/categories", headers=hdr, json=payload)
    assert res.status_code == 200, res.text
    cats = client.get(f"/api/v1/read/ledgers/{ledger_id}/categories", headers=hdr).json()
    return next(c["id"] for c in cats if c["name"] == name)


def _create_project(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": "日本旅行",
        "period_type": "monthly",
        "budget_amount": 10000.0,
    }
    payload.update(overrides)
    res = client.post(f"/api/v1/write/ledgers/{ledger_id}/projects", headers=hdr, json=payload)
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _create_category_budget(client, hdr, ledger_id, token, project_id, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {"base_change_id": base, "mode": "fixed", "fixed_amount": 1000.0}
    payload.update(overrides)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets",
        headers=hdr, json=payload,
    )
    assert res.status_code == 200, res.text
    return res.json()


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
    res = client.post(f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr, json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def _breakdown(client, hdr, ledger_id, project_id, **params):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/projects/{project_id}/breakdown",
        headers=hdr, params=params,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _this_month_mid():
    now = datetime.now(timezone.utc)
    return now.replace(day=min(now.day, 15), hour=12, minute=0, second=0, microsecond=0)


def _last_month_mid():
    this = _this_month_mid()
    prev_end = this.replace(day=1) - timedelta(days=1)
    return prev_end.replace(day=min(prev_end.day, 15))


def test_breakdown_stats_and_category_grouping():
    client, _TS, token, hdr, ledger_id = _setup("pbd1@example.com")
    try:
        food_id = _create_category(client, hdr, ledger_id, token, name="餐飲")
        shopping_id = _create_category(client, hdr, ledger_id, token, name="購物")
        untouched_id = _create_category(client, hdr, ledger_id, token, name="娛樂")
        project_id = _create_project(client, hdr, ledger_id, token, budget_amount=10000.0)

        _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=food_id, mode="fixed", fixed_amount=3000.0,
        )
        _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=shopping_id, mode="percentage", percentage=10.0,
        )
        # untouched_id 分類沒有配置分配、也沒有交易 → 應該落在 unset_categories。

        now = _this_month_mid()
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=1200.0, category_id=food_id,
            project_id=project_id, happened_at=_iso(now),
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=500.0, category_id=None,
            project_id=project_id, happened_at=_iso(now),
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="income", amount=300.0, category_id=None,
            project_id=project_id, happened_at=_iso(now),
        )

        out = _breakdown(client, hdr, ledger_id, project_id)
        assert out["expense_total"] == 1700.0
        assert out["expense_count"] == 2
        assert out["income_total"] == 300.0
        assert out["income_count"] == 1
        assert out["spent"] == 1700.0
        assert out["budget_amount"] == 10000.0
        # income_included_in_budget 預設 False → effective_budget 就是原始 budget_amount。
        assert out["effective_budget"] == 10000.0
        assert out["remaining"] == 10000.0 - 1700.0

        allocated = {c["category_id"]: c for c in out["allocated_categories"]}
        assert food_id in allocated
        assert allocated[food_id]["spent"] == 1200.0
        assert allocated[food_id]["budget_target"] == 3000.0
        # 購物有配置(percentage 10% of 10000 = 1000)但本期沒有交易 → 應該落在
        # unset_categories,不是 allocated_categories(判斷順序:先看有無交易)。
        assert shopping_id not in allocated
        unset_ids = {c["category_id"] for c in out["unset_categories"]}
        assert shopping_id in unset_ids
        assert untouched_id in unset_ids

        unallocated_ids = {c["category_id"] for c in out["unallocated_categories"]}
        # 沒有分類的那筆 500 元支出:category_sync_id 是 None,group by 跳過,
        # 不出現在任何分類分組裡,但仍計入 expense_total/spent。
        assert unallocated_ids == set()

        assert out["allocated_total"] == 3000.0 + 1000.0
        assert out["unallocated_amount"] == 10000.0 - (3000.0 + 1000.0)
    finally:
        client.app.dependency_overrides.clear()


def test_breakdown_income_included_in_budget_raises_effective_budget():
    client, _TS, token, hdr, ledger_id = _setup("pbd2@example.com")
    try:
        project_id = _create_project(
            client, hdr, ledger_id, token,
            budget_amount=1000.0, income_included_in_budget=True,
        )
        now = _this_month_mid()
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=900.0, project_id=project_id, happened_at=_iso(now),
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="income", amount=500.0, project_id=project_id, happened_at=_iso(now),
        )

        out = _breakdown(client, hdr, ledger_id, project_id)
        # effective_budget = 1000 + 500 = 1500,spent=900 < 1500 → status ok。
        assert out["effective_budget"] == 1500.0
        assert out["remaining"] == 600.0
        assert out["status"] == "ok"
        assert out["progress_pct"] == round(900.0 / 1500.0 * 100.0, 2)
    finally:
        client.app.dependency_overrides.clear()


def test_breakdown_period_offset_switches_to_previous_month():
    client, _TS, token, hdr, ledger_id = _setup("pbd3@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token, budget_amount=None)

        this_month = _this_month_mid()
        last_month = _last_month_mid()
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=100.0, project_id=project_id, happened_at=_iso(this_month),
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=250.0, project_id=project_id, happened_at=_iso(last_month),
        )

        current = _breakdown(client, hdr, ledger_id, project_id)
        assert current["expense_total"] == 100.0
        assert current["period_offset"] == 0
        assert current["period_has_newer"] is False

        previous = _breakdown(client, hdr, ledger_id, project_id, period_offset=1)
        assert previous["expense_total"] == 250.0
        assert previous["period_offset"] == 1
        assert previous["period_has_newer"] is True
    finally:
        client.app.dependency_overrides.clear()


def test_breakdown_no_budget_project_has_no_budget_fields():
    client, _TS, token, hdr, ledger_id = _setup("pbd4@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token, budget_amount=None)
        out = _breakdown(client, hdr, ledger_id, project_id)
        assert out["budget_amount"] is None
        assert out["effective_budget"] is None
        assert out["remaining"] is None
        assert out["progress_pct"] is None
        assert out["unallocated_amount"] is None
        assert out["status"] == "ok"
    finally:
        client.app.dependency_overrides.clear()


def test_list_transactions_filters_by_project_and_category():
    client, _TS, token, hdr, ledger_id = _setup("pbd5@example.com")
    try:
        food_id = _create_category(client, hdr, ledger_id, token, name="餐飲")
        project_a = _create_project(client, hdr, ledger_id, token, name="A")
        project_b = _create_project(client, hdr, ledger_id, token, name="B")
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=10.0, category_id=food_id, project_id=project_a,
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=20.0, category_id=food_id, project_id=project_b,
        )
        _create_tx(
            client, hdr, ledger_id, token,
            tx_type="expense", amount=30.0, category_id=None, project_id=project_a,
        )

        r = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/transactions",
            headers=hdr, params={"project_id": project_a, "category_id": food_id},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["amount"] == 10.0
    finally:
        client.app.dependency_overrides.clear()
