"""專案(Phase 13,docs/PH13_PROJECT_SD.md)—— project entity 契约:

- `POST/PATCH/DELETE /write/ledgers/{id}/projects`:name/icon/budget_amount/
  period_type(fixed/monthly/yearly)/period_start/period_end/carryover_enabled/
  visible_on_home/enabled/sort_order,owner-only 寫入。
- `spent`/`remaining`/`progress_pct`/`status` 不落库,`GET /read/ledgers/{id}
  /projects` 從 `read_tx_projection.project_sync_id` 反查交易依 period_type
  當期窗口即時彙總算出(見 `ReadProjectProjection` docstring)。
- 一筆交易關聯 `project_id`(建/改交易時傳入)只支援 expense/income,
  `project_id` 必須指向該帳本下已存在的專案,否則 400;transfer/adjustment
  帶這個欄位會被拒絕。
- DELETE:專案底下已有交易掛著時軟刪除(`enabled=false`),沒有交易時物理刪除。
- mobile `/sync/push` 的 `project` merge 契約(partial update 保留舊值)+
  `transaction` entity 的 `projectId` 反查字段同款保留語義。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadProjectProjection, ReadTxProjection


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


def _setup(email, ledger_id="L_PROJ1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, token, hdr, ledger_id


def _create_project(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": "日本旅行",
        "period_type": "monthly",
    }
    payload.update(overrides)
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/projects", headers=hdr, json=payload)


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
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr, json=payload)


def _projects(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/projects", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_project_and_list_defaults():
    client, _TS, token, hdr, ledger_id = _setup("proj1@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token, icon="✈️", budget_amount=5000.0)
        assert res.status_code == 200, res.text
        project_id = res.json()["entity_id"]

        projects = _projects(client, hdr, ledger_id)
        assert len(projects) == 1
        p = projects[0]
        assert p["id"] == project_id
        assert p["name"] == "日本旅行"
        assert p["icon"] == "✈️"
        assert p["budget_amount"] == 5000.0
        assert p["period_type"] == "monthly"
        assert p["carryover_enabled"] is False
        assert p["visible_on_home"] is True
        assert p["enabled"] is True
        assert p["spent"] == 0.0
        assert p["remaining"] == 5000.0
        assert p["progress_pct"] == 0.0
        assert p["status"] == "ok"
    finally:
        client.close()


def test_create_project_without_budget_has_no_remaining_or_progress():
    client, _TS, token, hdr, ledger_id = _setup("proj2@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text
        p = _projects(client, hdr, ledger_id)[0]
        assert p["budget_amount"] is None
        assert p["remaining"] is None
        assert p["progress_pct"] is None
        assert p["status"] == "ok"
    finally:
        client.close()


def test_create_fixed_project_requires_period_start_and_end():
    client, _TS, token, hdr, ledger_id = _setup("proj3@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token, period_type="fixed")
        assert res.status_code == 400, res.text

        res2 = _create_project(
            client, hdr, ledger_id, token, period_type="fixed",
            period_start="2026-08-01", period_end="2026-08-15",
        )
        assert res2.status_code == 200, res2.text
        p = _projects(client, hdr, ledger_id)[0]
        assert p["period_start"] == "2026-08-01"
        assert p["period_end"] == "2026-08-15"
    finally:
        client.close()


def test_update_project_name_icon_and_budget():
    client, _TS, token, hdr, ledger_id = _setup("proj4@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token)
        project_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        upd = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}",
            headers=hdr,
            json={"base_change_id": base, "name": "京都行", "icon": "🏯", "budget_amount": 3000.0},
        )
        assert upd.status_code == 200, upd.text

        p = _projects(client, hdr, ledger_id)[0]
        assert p["name"] == "京都行"
        assert p["icon"] == "🏯"
        assert p["budget_amount"] == 3000.0
    finally:
        client.close()


def test_update_project_can_clear_budget_amount():
    client, _TS, token, hdr, ledger_id = _setup("proj5@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token, budget_amount=1000.0)
        project_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        upd = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}",
            headers=hdr,
            json={"base_change_id": base, "budget_amount": None},
        )
        assert upd.status_code == 200, upd.text
        p = _projects(client, hdr, ledger_id)[0]
        assert p["budget_amount"] is None
        assert p["remaining"] is None
    finally:
        client.close()


def test_delete_project_without_transactions_is_hard_deleted():
    client, _TS, token, hdr, ledger_id = _setup("proj6@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token)
        project_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        ok = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert ok.status_code == 200, ok.text
        assert _projects(client, hdr, ledger_id) == []
    finally:
        client.close()


def test_delete_project_with_transactions_is_soft_deleted():
    client, _TS, token, hdr, ledger_id = _setup("proj7@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token)
        project_id = res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, project_id=project_id)
        assert tx_res.status_code == 200, tx_res.text

        base = _latest_change_id(client, token, ledger_id)
        deleted = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/projects/{project_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert deleted.status_code == 200, deleted.text

        projects = _projects(client, hdr, ledger_id)
        assert len(projects) == 1
        assert projects[0]["id"] == project_id
        assert projects[0]["enabled"] is False
    finally:
        client.close()


def test_project_scoped_to_own_ledger_only():
    client, _TS = _make_client()
    try:
        owner = _register(client, "proj8@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, app_token, device, "L_A")
        _seed_ledger(client, app_token, device, "L_B")
        web = _login_web(client, "proj8@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _create_project(client, hdr, "L_A", token, name="A的專案")
        _create_project(client, hdr, "L_B", token, name="B的專案")

        projects_a = _projects(client, hdr, "L_A")
        projects_b = _projects(client, hdr, "L_B")
        assert len(projects_a) == 1 and projects_a[0]["name"] == "A的專案"
        assert len(projects_b) == 1 and projects_b[0]["name"] == "B的專案"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 交易掛專案(存在性校驗 + tx_type 限制)
# ---------------------------------------------------------------------------


def test_tx_with_unknown_project_id_rejected():
    client, _TS, token, hdr, ledger_id = _setup("proj9@example.com")
    try:
        res = _create_tx(client, hdr, ledger_id, token, project_id="project_does_not_exist")
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_transfer_tx_with_project_id_rejected():
    client, _TS, token, hdr, ledger_id = _setup("proj10@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token)
        project_id = proj_res.json()["entity_id"]
        res = _create_tx(
            client, hdr, ledger_id, token, tx_type="transfer", project_id=project_id,
            from_account_id="acc_x", to_account_id="acc_y",
        )
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_adjustment_tx_with_project_id_rejected():
    client, _TS, token, hdr, ledger_id = _setup("proj11@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token)
        project_id = proj_res.json()["entity_id"]
        res = _create_tx(
            client, hdr, ledger_id, token, tx_type="adjustment", project_id=project_id,
            account_id="acc_x",
        )
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_expense_and_income_tx_with_project_id_accepted():
    client, _TS, token, hdr, ledger_id = _setup("proj12@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token)
        project_id = proj_res.json()["entity_id"]

        expense_res = _create_tx(client, hdr, ledger_id, token, tx_type="expense", amount=100.0, project_id=project_id)
        assert expense_res.status_code == 200, expense_res.text
        income_res = _create_tx(client, hdr, ledger_id, token, tx_type="income", amount=50.0, project_id=project_id)
        assert income_res.status_code == 200, income_res.text

        p = _projects(client, hdr, ledger_id)[0]
        assert p["spent"] == 150.0
    finally:
        client.close()


def test_update_tx_can_attach_and_detach_project_id():
    client, _TS, token, hdr, ledger_id = _setup("proj13@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token)
        project_id = proj_res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, amount=100.0)
        tx_id = tx_res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        attach = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "project_id": project_id},
        )
        assert attach.status_code == 200, attach.text
        assert _projects(client, hdr, ledger_id)[0]["spent"] == 100.0

        base2 = _latest_change_id(client, token, ledger_id)
        detach = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base2, "project_id": ""},
        )
        assert detach.status_code == 200, detach.text
        assert _projects(client, hdr, ledger_id)[0]["spent"] == 0.0
    finally:
        client.close()


def test_update_tx_without_project_id_key_keeps_existing_association():
    client, _TS, token, hdr, ledger_id = _setup("proj14@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token)
        project_id = proj_res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, amount=100.0, project_id=project_id)
        tx_id = tx_res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        patch_note_only = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base, "note": "備註"},
        )
        assert patch_note_only.status_code == 200, patch_note_only.text

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        row = {row["id"]: row for row in r.json()}[tx_id]
        assert row["project_id"] == project_id
        assert row["note"] == "備註"
    finally:
        client.close()


def test_list_transactions_includes_project_fields():
    client, _TS, token, hdr, ledger_id = _setup("proj15@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token, name="裝修基金")
        project_id = proj_res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, amount=200.0, project_id=project_id)
        tx_id = tx_res.json()["entity_id"]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        assert r.status_code == 200, r.text
        row = {row["id"]: row for row in r.json()}[tx_id]
        assert row["project_id"] == project_id
        assert row["project_name"] == "裝修基金"
    finally:
        client.close()


def test_workspace_transactions_includes_project_fields():
    client, _TS, token, hdr, ledger_id = _setup("proj16@example.com")
    try:
        proj_res = _create_project(client, hdr, ledger_id, token, name="裝修基金")
        project_id = proj_res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, amount=200.0, project_id=project_id)
        tx_id = tx_res.json()["entity_id"]

        r = client.get(
            "/api/v1/read/workspace/transactions",
            headers=hdr,
            params={"ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        row = {row["id"]: row for row in r.json()["items"]}[tx_id]
        assert row["project_id"] == project_id
        assert row["project_name"] == "裝修基金"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 花費彙總正確性(週期邊界)
# ---------------------------------------------------------------------------


def test_fixed_period_only_sums_transactions_inside_window():
    client, _TS, token, hdr, ledger_id = _setup("proj17@example.com")
    try:
        res = _create_project(
            client, hdr, ledger_id, token, period_type="fixed",
            period_start="2026-08-01", period_end="2026-08-10",
        )
        project_id = res.json()["entity_id"]

        inside = _create_tx(
            client, hdr, ledger_id, token, amount=300.0, project_id=project_id,
            happened_at=datetime(2026, 8, 5, tzinfo=timezone.utc).isoformat(),
        )
        assert inside.status_code == 200, inside.text
        before = _create_tx(
            client, hdr, ledger_id, token, amount=999.0, project_id=project_id,
            happened_at=datetime(2026, 7, 31, tzinfo=timezone.utc).isoformat(),
        )
        assert before.status_code == 200, before.text
        after = _create_tx(
            client, hdr, ledger_id, token, amount=999.0, project_id=project_id,
            happened_at=datetime(2026, 8, 11, tzinfo=timezone.utc).isoformat(),
        )
        assert after.status_code == 200, after.text
        boundary = _create_tx(
            client, hdr, ledger_id, token, amount=50.0, project_id=project_id,
            happened_at=datetime(2026, 8, 10, 23, 59, 0, tzinfo=timezone.utc).isoformat(),
        )
        assert boundary.status_code == 200, boundary.text

        p = _projects(client, hdr, ledger_id)[0]
        assert p["spent"] == 350.0
    finally:
        client.close()


def test_yearly_period_sums_whole_calendar_year():
    client, _TS, token, hdr, ledger_id = _setup("proj18@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token, period_type="yearly")
        project_id = res.json()["entity_id"]

        now = datetime.now(timezone.utc)
        this_year_tx = _create_tx(
            client, hdr, ledger_id, token, amount=400.0, project_id=project_id,
            happened_at=now.replace(month=1, day=2).isoformat(),
        )
        assert this_year_tx.status_code == 200, this_year_tx.text
        last_year_tx = _create_tx(
            client, hdr, ledger_id, token, amount=999.0, project_id=project_id,
            happened_at=now.replace(year=now.year - 1).isoformat(),
        )
        assert last_year_tx.status_code == 200, last_year_tx.text

        p = _projects(client, hdr, ledger_id)[0]
        assert p["spent"] == 400.0
    finally:
        client.close()


def test_project_status_thresholds():
    client, _TS, token, hdr, ledger_id = _setup("proj19@example.com")
    try:
        res = _create_project(client, hdr, ledger_id, token, budget_amount=1000.0)
        project_id = res.json()["entity_id"]

        _create_tx(client, hdr, ledger_id, token, amount=500.0, project_id=project_id)
        p = _projects(client, hdr, ledger_id)[0]
        assert p["status"] == "ok"

        _create_tx(client, hdr, ledger_id, token, amount=350.0, project_id=project_id)
        p = _projects(client, hdr, ledger_id)[0]
        assert p["status"] == "warning"  # 850/1000 = 85% >= 80%

        _create_tx(client, hdr, ledger_id, token, amount=200.0, project_id=project_id)
        p = _projects(client, hdr, ledger_id)[0]
        assert p["status"] == "over"  # 1050/1000 > 100%
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_project_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        owner = _register(client, "proj20@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_PROJ20"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "proj_manual1"
        _push(client, hdr, ledger_id, "project", sync_id, {
            "syncId": sync_id,
            "name": "手動建立的專案",
            "icon": "🎪",
            "budgetAmount": 2000.0,
            "periodType": "monthly",
            "enabled": True,
        }, device_id=device)

        # 只带 name,其它字段应保留
        _push(client, hdr, ledger_id, "project", sync_id, {
            "syncId": sync_id,
            "name": "改名後的專案",
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadProjectProjection).where(ReadProjectProjection.sync_id == sync_id)
            )
            assert row is not None
            assert row.name == "改名後的專案"
            assert row.icon == "🎪"
            assert row.budget_amount == 2000.0
            assert row.period_type == "monthly"
            assert row.enabled is True
        finally:
            db.close()
    finally:
        client.close()


def test_mobile_push_transaction_project_id_roundtrip():
    """`projectId` 是 transaction merge spec 的一个反查字段,partial update
    缺键要保留旧值(跟 debtId/refundOfId/installmentPlanId 同款契约)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "proj21@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_PROJ21"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        _push(client, hdr, ledger_id, "project", "proj_x1", {
            "syncId": "proj_x1", "name": "露營裝備", "periodType": "monthly",
        }, device_id=device)

        tx_id = "tx_proj1"
        _push(client, hdr, ledger_id, "transaction", tx_id, {
            "syncId": tx_id, "type": "expense", "amount": 100.0,
            "happenedAt": _iso(), "projectId": "proj_x1",
        }, device_id=device)

        # 只改 note,不带 projectId,应保留原关联
        _push(client, hdr, ledger_id, "transaction", tx_id, {
            "syncId": tx_id, "note": "帳篷",
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row is not None
            assert row.project_sync_id == "proj_x1"
            assert row.note == "帳篷"
        finally:
            db.close()
    finally:
        client.close()


def test_mobile_push_project_delete_removes_projection_row():
    client, TS = _make_client()
    try:
        owner = _register(client, "proj22@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_PROJ22"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        _push(client, hdr, ledger_id, "project", "proj_del1", {
            "syncId": "proj_del1", "name": "待刪除的專案",
        }, device_id=device)
        _push(client, hdr, ledger_id, "project", "proj_del1", {
            "syncId": "proj_del1",
        }, device_id=device, action="delete")

        db = TS()
        try:
            row = db.scalar(
                select(ReadProjectProjection).where(ReadProjectProjection.sync_id == "proj_del1")
            )
            assert row is None
        finally:
            db.close()
    finally:
        client.close()
