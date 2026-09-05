"""專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
design.md §2.2/§5/§7.2)—— `project_category_budget` entity 契约:

- `POST/PATCH/DELETE /write/ledgers/{id}/projects/{project_id}/category-budgets`:
  category_id(必填,建立後不可改)/mode(fixed/percentage)/fixed_amount/
  percentage/carryover_enabled/sort_order,owner-only 寫入。
- `category_id` 必須指向該帳本下已存在、且 `level == 1` 的一級分類,否則 400。
- `project_id`(來自 URL path)必須指向該帳本下已存在的專案,否則 400。
- 同一專案下同一分類只能有一筆分配,重複建立回 400。
- mode='fixed' 需要 fixed_amount>0;mode='percentage' 需要 0<percentage<=100。
- DELETE:物理刪除(這個實體不被交易反查引用,沒有軟刪除必要)。
- mobile `/sync/push` 的 `project_category_budget` merge 契約(partial update
  保留舊值)+ web write 路徑(`_shared.py` 的 `_LEDGER_PROJECTION_UPSERTERS`/
  `_emit_entity_diffs` 第二套登記表)必須各自驗證,兩條路徑分開註冊。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadProjectCategoryBudgetProjection


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


def _setup(email, ledger_id="L_PCB1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, token, hdr, ledger_id


def _create_category(client, hdr, ledger_id, token, *, name, level=1, parent_name=None):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": name,
        "kind": "expense",
        "level": level,
    }
    if parent_name is not None:
        payload["parent_name"] = parent_name
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
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets",
        headers=hdr, json=payload,
    )


def _category_budgets(client, hdr, ledger_id, project_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/projects/{project_id}/category-budgets",
        headers=hdr,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_fixed_mode_category_budget_and_list():
    client, _TS, token, hdr, ledger_id = _setup("pcb1@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="餐飲")

        res = _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=cat_id, mode="fixed", fixed_amount=2000.0,
        )
        assert res.status_code == 200, res.text
        budget_id = res.json()["entity_id"]

        rows = _category_budgets(client, hdr, ledger_id, project_id)
        assert len(rows) == 1
        r = rows[0]
        assert r["id"] == budget_id
        assert r["project_id"] == project_id
        assert r["category_id"] == cat_id
        assert r["mode"] == "fixed"
        assert r["fixed_amount"] == 2000.0
        assert r["percentage"] is None
        assert r["carryover_enabled"] is False
        assert r["sort_order"] == 0
    finally:
        client.close()


def test_create_percentage_mode_category_budget_and_list():
    client, _TS, token, hdr, ledger_id = _setup("pcb2@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="交通")

        res = _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=cat_id, mode="percentage", fixed_amount=None, percentage=25.0,
        )
        assert res.status_code == 200, res.text

        r = _category_budgets(client, hdr, ledger_id, project_id)[0]
        assert r["mode"] == "percentage"
        assert r["fixed_amount"] is None
        assert r["percentage"] == 25.0
    finally:
        client.close()


def test_create_duplicate_category_budget_blocked():
    client, _TS, token, hdr, ledger_id = _setup("pcb3@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="娛樂")

        res = _create_category_budget(client, hdr, ledger_id, token, project_id, category_id=cat_id)
        assert res.status_code == 200, res.text

        res = _create_category_budget(client, hdr, ledger_id, token, project_id, category_id=cat_id)
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_create_category_budget_requires_existing_project():
    client, _TS, token, hdr, ledger_id = _setup("pcb4@example.com")
    try:
        cat_id = _create_category(client, hdr, ledger_id, token, name="購物")
        res = _create_category_budget(
            client, hdr, ledger_id, token, "proj_does_not_exist", category_id=cat_id,
        )
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_create_category_budget_requires_top_level_category():
    client, _TS, token, hdr, ledger_id = _setup("pcb5@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        _create_category(client, hdr, ledger_id, token, name="吃喝", level=1)
        sub_id = _create_category(client, hdr, ledger_id, token, name="吃", level=2, parent_name="吃喝")

        res = _create_category_budget(client, hdr, ledger_id, token, project_id, category_id=sub_id)
        assert res.status_code == 400, res.text

        res = _create_category_budget(client, hdr, ledger_id, token, project_id, category_id="cat_missing")
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_update_project_category_budget_switch_mode():
    client, _TS, token, hdr, ledger_id = _setup("pcb6@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="醫療")

        res = _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=cat_id, mode="fixed", fixed_amount=500.0,
        )
        budget_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
            headers=hdr,
            json={"base_change_id": base, "mode": "percentage", "percentage": 10.0},
        )
        assert res.status_code == 200, res.text
        r = _category_budgets(client, hdr, ledger_id, project_id)[0]
        assert r["mode"] == "percentage"
        assert r["percentage"] == 10.0
        # fixedAmount 沒被顯式清空,但 mode 已切換,read 端仍會回傳舊值
        # (契約只保證 mode 對應欄位必填,不強制清掉另一模式殘留值)。
        assert r["fixed_amount"] == 500.0

        # 切回 fixed 但沒帶 fixed_amount(仍保留舊的 500.0)应该成功。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
            headers=hdr,
            json={"base_change_id": base, "mode": "fixed"},
        )
        assert res.status_code == 200, res.text
    finally:
        client.close()


def test_delete_project_category_budget():
    client, _TS, token, hdr, ledger_id = _setup("pcb7@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="訂閱")
        res = _create_category_budget(client, hdr, ledger_id, token, project_id, category_id=cat_id)
        budget_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        assert _category_budgets(client, hdr, ledger_id, project_id) == []
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_project_category_budget_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        owner = _register(client, "pcb20@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_PCB20"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "pcb_manual1"
        _push(client, hdr, ledger_id, "project_category_budget", sync_id, {
            "syncId": sync_id,
            "projectId": "proj_manual1",
            "categoryId": "cat_manual1",
            "mode": "fixed",
            "fixedAmount": 500.0,
            "carryoverEnabled": True,
            "sortOrder": 3,
        }, device_id=device)

        # 只带 sortOrder,其它字段应保留
        _push(client, hdr, ledger_id, "project_category_budget", sync_id, {
            "syncId": sync_id,
            "sortOrder": 9,
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadProjectCategoryBudgetProjection).where(
                    ReadProjectCategoryBudgetProjection.sync_id == sync_id
                )
            )
            assert row is not None
            assert row.sort_order == 9
            assert row.project_sync_id == "proj_manual1"
            assert row.category_sync_id == "cat_manual1"
            assert row.mode == "fixed"
            assert row.fixed_amount == 500.0
            assert row.carryover_enabled is True
        finally:
            db.close()
    finally:
        client.close()


def test_web_write_project_category_budget_updates_projection():
    """驗證 web write 路徑(`_shared.py` 的 `_LEDGER_PROJECTION_UPSERTERS` +
    `_emit_entity_diffs` 第二套登記表)確實把資料落到 projection 表 ——
    不能只測 mobile push 那條就假設 web 也對(這兩條路徑各自獨立註冊)。"""
    client, TS, token, hdr, ledger_id = _setup("pcb21@example.com")
    try:
        project_id = _create_project(client, hdr, ledger_id, token)
        cat_id = _create_category(client, hdr, ledger_id, token, name="日用品")
        res = _create_category_budget(
            client, hdr, ledger_id, token, project_id,
            category_id=cat_id, mode="fixed", fixed_amount=1234.0,
        )
        budget_id = res.json()["entity_id"]

        db = TS()
        try:
            row = db.scalar(
                select(ReadProjectCategoryBudgetProjection).where(
                    ReadProjectCategoryBudgetProjection.sync_id == budget_id
                )
            )
            assert row is not None
            assert row.project_sync_id == project_id
            assert row.category_sync_id == cat_id
            assert row.fixed_amount == 1234.0
        finally:
            db.close()

        # 再 PATCH 一次,確認第二次讀基線不是空的(snapshot_builder 遺漏會導致
        # 這裡被誤判成新建)。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
            headers=hdr,
            json={"base_change_id": base, "sort_order": 5},
        )
        assert res.status_code == 200, res.text
        r = _category_budgets(client, hdr, ledger_id, project_id)[0]
        assert r["sort_order"] == 5
        assert r["fixed_amount"] == 1234.0
    finally:
        client.close()
