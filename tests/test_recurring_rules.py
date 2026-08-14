"""週期性收支(§2.2 / Phase 1.5 修正版 §2.12.2 MOZE_FEATURE_GAP_SD.md)——
recurring_rule entity 契约:

- web `/write/ledgers/{id}/recurring-rules` **建立當下就批次生成 occurrence
  交易**(不再等排程),有 `end_at` 全部生成并在完全生成时 `enabled=False`;
  沒有 `end_at` 只生成默認視窗(12 個月/200 筆取先到者)。
- mobile `/sync/push` 的 `recurring_rule` merge 契约(partial update 保留旧值,
  含 Phase 1.5 新增的 `generatedUntilAt`/`advancedRuleJson`)。
- 差異化編輯:`PATCH .../occurrences/{tx_id}` 單獨編輯某一期(標記
  overridden)、`POST .../update-from/{tx_id}` 連同未來但跳過 overridden、
  `POST .../terminate-future` 刪除未發生交易並停用規則。
- `services.recurring_materializer.refill_recurring_windows`:沒有 end_at
  的長期規則,視窗快用完時續產生下一段。

============================================================================
手动检查清单(pytest 测不到的运行时行为):

1. `POST /api/v1/internal/tasks/materialize-recurring`(admin token)手动触发
   一次視窗續產生,确认返回体 `recurring_transactions` 计数符合预期。
2. `sqlite3 beecount.db` 查 `SELECT * FROM read_recurring_rule_projection;`
   确认 `generated_until_at`/`enabled` 符合预期。
3. `GET /api/v1/notifications?category=reminder` 应该能看到"週期性收支已
   续期"的通知(仅在续产生真的生成了新交易时才有)。
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
from src.models import ReadRecurringRuleProjection, ReadTxProjection
from src.services.recurring_materializer import refill_recurring_windows


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


def _rules(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/recurring-rules", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_category(client, hdr, ledger_id, sync_id="cat-1", kind="expense", name="測試分類"):
    """需求 #14(Phase 12)分類必填後,大多數建立週期性收支規則的測試都要先有
    一個分類可以帶。跟 mobile push 一筆 user-global category 一样简单。"""
    _push(client, hdr, ledger_id, "category", sync_id, {"syncId": sync_id, "name": name, "kind": kind})
    return sync_id


# ---------------------------------------------------------------------------
# 建立當下批次生成
# ---------------------------------------------------------------------------


def test_create_recurring_rule_with_end_at_generates_all_occurrences_and_disables():
    client, _TS = _make_client()
    try:
        owner = _register(client, "rec1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REC1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "rec1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=61)  # 涵盖 next_run/+1mo/+2mo 三次
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 99.5,
                "note": "房租",
                "category_id": "cat-1",
                "frequency": "monthly",
                "interval": 1,
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        rules = _rules(client, hdr, ledger_id)
        assert len(rules) == 1
        assert rules[0]["id"] == rule_id
        assert rules[0]["amount"] == 99.5
        # 建立當下就把 [next_run_at, end_at] 全部生成完 → 视为完全生成,disable
        assert rules[0]["enabled"] is False
        assert rules[0]["generated_until_at"] is not None

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 3, "next_run/+1mo/+2mo 三次落在 end_at 之前"
        assert all(t["amount"] == 99.5 for t in txs)
        assert all(t["recurring_rule_id"] == rule_id for t in txs)
    finally:
        app.dependency_overrides.clear()


def test_create_recurring_rule_without_end_at_generates_default_window():
    client, _TS = _make_client()
    try:
        owner = _register(client, "rec2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REC2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "rec2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) - timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 50.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["enabled"] is True, "没有 end_at 的长期规则不会在建立当下就 disable"
        assert rules[0]["generated_until_at"] is not None

        txs = _transactions(client, hdr, ledger_id)
        # 默认视窗:12 个月或 200 笔取先到者,monthly 频率下应该有一批而不是 1 笔
        assert len(txs) >= 12
        assert all(t["recurring_rule_id"] == rule_id for t in txs)
    finally:
        app.dependency_overrides.clear()


def test_web_update_and_delete_recurring_rule():
    client, _TS = _make_client()
    try:
        owner = _register(client, "rec3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REC3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "rec3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        base = _latest_change_id(client, token, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=1)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 50,
                "category_id": "cat-1",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base, "amount": 80},
        )
        assert res.status_code == 200, res.text

        rules = _rules(client, hdr, ledger_id)
        assert len(rules) == 1
        assert rules[0]["amount"] == 80.0

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        assert _rules(client, hdr, ledger_id) == []
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mobile push merge 契约(CLAUDE.md 硬门槛)
# ---------------------------------------------------------------------------


def test_mobile_push_recurring_rule_partial_update_keeps_existing_fields():
    """先 push 一条完整规则(含 Phase 1.5 新字段),再 push 一条只带 amount 的
    partial update —— note/frequency/category/generatedUntilAt/
    advancedRuleJson 等字段必须保留旧值,不能被冲成 None/默认值。"""
    client, TS = _make_client()
    try:
        tok = _register(client, "recmerge@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) + timedelta(days=5)
        generated_until = next_run + timedelta(days=365)

        _push(client, hdr, "lg1", "recurring_rule", "rec-1", {
            "syncId": "rec-1",
            "txType": "expense",
            "amount": 100.0,
            "note": "健身房月费",
            "categoryId": "cat-fitness",
            "frequency": "monthly",
            "interval": 1,
            "nextRunAt": next_run.isoformat(),
            "enabled": True,
            "generatedUntilAt": generated_until.isoformat(),
            "advancedRuleJson": {"type": "monthly_day", "day": 10},
        })
        # partial update:只改 amount
        _push(client, hdr, "lg1", "recurring_rule", "rec-1", {
            "syncId": "rec-1",
            "amount": 150.0,
        })

        with TS() as db:
            row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-1",
                )
            )
            assert row is not None
            assert row.amount == 150.0
            assert row.note == "健身房月费", "partial update 不该冲掉 note"
            assert row.category_sync_id == "cat-fitness", "partial update 不该冲掉 category"
            assert row.frequency == "monthly"
            assert row.enabled is True
            assert row.generated_until_at is not None, "partial update 不该冲掉 generated_until_at"
            assert row.advanced_rule_json is not None
            import json as _json
            assert _json.loads(row.advanced_rule_json) == {"type": "monthly_day", "day": 10}
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_recurring_rule_delete():
    client, TS = _make_client()
    try:
        tok = _register(client, "recdel@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        _push(client, hdr, "lg1", "recurring_rule", "rec-del", {
            "syncId": "rec-del", "amount": 10.0, "nextRunAt": next_run.isoformat(),
        })
        _push(client, hdr, "lg1", "recurring_rule", "rec-del", {}, action="delete")

        with TS() as db:
            row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-del",
                )
            )
            assert row is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 差異化編輯:單獨編輯 / 連同未來 / 終止未來
# ---------------------------------------------------------------------------


def test_recurring_occurrence_update_overridden_skipped_by_update_from():
    client, _TS = _make_client()
    try:
        owner = _register(client, "recocc1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECOCC1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recocc1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=91)  # next_run/+1/+2/+3 月 四次
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 100.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        txs = sorted(_transactions(client, hdr, ledger_id), key=lambda t: t["happened_at"])
        assert len(txs) == 4
        occ0, occ1, occ2, occ3 = [t["id"] for t in txs]

        # 单独编辑 occ1(index 1),金额改成 999,标记 overridden
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{occ1}",
            headers=hdr,
            json={"base_change_id": base, "amount": 999.0},
        )
        assert res.status_code == 200, res.text

        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[occ1]["amount"] == 999.0
        assert txs[occ1]["recurring_occurrence_overridden"] is True

        # 连同未来:从 occ0 起改金额成 777,occ1(overridden)应该被跳过
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/update-from/{occ0}",
            headers=hdr,
            json={"base_change_id": base, "amount": 777.0},
        )
        assert res.status_code == 200, res.text

        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[occ0]["amount"] == 777.0
        assert txs[occ1]["amount"] == 999.0, "overridden 的期数不该被 update-from 覆盖"
        assert txs[occ2]["amount"] == 777.0
        assert txs[occ3]["amount"] == 777.0

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["amount"] == 777.0, "update-from 也要更新规则本身的字段"
    finally:
        app.dependency_overrides.clear()


def test_recurring_occurrence_delete():
    client, _TS = _make_client()
    try:
        owner = _register(client, "recocc2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECOCC2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recocc2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=32)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 20.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 2
        target = txs[0]["id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{target}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        remaining = _transactions(client, hdr, ledger_id)
        assert len(remaining) == 1
        assert remaining[0]["id"] != target
    finally:
        app.dependency_overrides.clear()


def test_recurring_terminate_future_deletes_unhappened_keeps_past():
    client, _TS = _make_client()
    try:
        owner = _register(client, "recterm1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTERM1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recterm1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # daily 频率,从 -3 天到 +3 天,7 次 occurrence;调用 terminate-future
        # 时的 now 一定晚于建规则时的 now,所以 offset=0 那笔也会落在"过去"。
        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        start = datetime.now(timezone.utc) - timedelta(days=3)
        end_at = datetime.now(timezone.utc) + timedelta(days=3)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 5.0,
                "category_id": "cat-1",
                "frequency": "daily",
                "next_run_at": start.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]
        txs_before = _transactions(client, hdr, ledger_id)
        assert len(txs_before) == 7

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/terminate-future",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        txs_after = _transactions(client, hdr, ledger_id)
        assert len(txs_after) == 4, "day -3..0 四笔已发生,保留;+1..+3 三笔未发生,删除"

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["enabled"] is False
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 視窗續產生(services.recurring_materializer.refill_recurring_windows)
# ---------------------------------------------------------------------------


def test_refill_recurring_windows_extends_generated_until_at():
    client, TS = _make_client()
    try:
        owner = _register(client, "recrefill1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECREFILL1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recrefill1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) - timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 30.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
            ledger_internal_id = rule_row.ledger_id
            old_generated_until = rule_row.generated_until_at
            tx_count_before = len(
                db.scalars(
                    select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger_internal_id)
                ).all()
            )

            # 模拟视窗快用完:把 generated_until_at 拨到临界值内(< 30 天)。
            rule_row.generated_until_at = datetime.now(timezone.utc) + timedelta(days=10)
            db.commit()

            generated = refill_recurring_windows(db)
            db.commit()
            assert generated > 0

            refreshed = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
            assert refreshed.generated_until_at > old_generated_until - timedelta(days=400), (
                "确保比较的是新推进后的值,不是误用了旧变量"
            )
            assert refreshed.enabled is True, "没有 end_at,续产生后仍然是长期启用状态"

            tx_count_after = len(
                db.scalars(
                    select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger_internal_id)
                ).all()
            )
            assert tx_count_after > tx_count_before

        # 已经推得够远,紧接着再跑一次不该继续生成(距 30 天阈值还远)
        with TS() as db:
            generated_again = refill_recurring_windows(db)
            db.commit()
            assert generated_again == 0
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 自動扣繳(tx_type=="transfer"):到期才逐筆生成 + 檢查來源帳戶餘額
# (2026-08-02 補,MOZE_FEATURE_GAP_SD.md §2.2 / 使用者反饋)
# ---------------------------------------------------------------------------


def test_transfer_recurring_rule_not_bulk_generated_at_creation():
    """跟 expense/income 規則不同:建立時只有原始那一筆(inline recurring
    起點,或這裡走独立 POST /recurring-rules 沒有起點交易),不會像批次視窗
    那樣一次生出未來好幾個月的 occurrence——因為到時候來源帳戶餘額夠不夠
    現在根本不知道。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR1"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 1000.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "rectr1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=5)
        end_at = next_run + timedelta(days=180)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 200.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        assert txs == [], "transfer 規則不该在建立当下就预生成任何 occurrence"

        with TS() as db:
            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is None
            assert rule_row.enabled is True
    finally:
        app.dependency_overrides.clear()


def test_transfer_recurring_rule_materializes_when_due_and_balance_sufficient():
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR2"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 1000.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "rectr2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) - timedelta(days=1)  # 已到期
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 200.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            from src.services.recurring_materializer import materialize_due_transfer_rules
            result = materialize_due_transfer_rules(db)
            db.commit()
            assert result["materialized"] == 1
            assert result["skipped_insufficient"] == 0

            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is not None

            txs = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.recurring_rule_sync_id == rule_id)
            ).all()
            assert len(txs) == 1
            assert txs[0].amount == 200.0
            assert txs[0].from_account_sync_id == "acc-bank"
            assert txs[0].to_account_sync_id == "acc-card"
    finally:
        app.dependency_overrides.clear()


def test_transfer_recurring_rule_skips_and_notifies_when_balance_insufficient():
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR3"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        # 只有 50 元,規則要扣 200 元 —— 餘額不足。
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 50.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "rectr3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) - timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 200.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            from src.models import Notification
            from src.services.recurring_materializer import materialize_due_transfer_rules

            result = materialize_due_transfer_rules(db)
            db.commit()
            assert result["materialized"] == 0
            assert result["skipped_insufficient"] == 1

            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is None, "不足額不推進,下次重試同一筆"

            txs = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.recurring_rule_sync_id == rule_id)
            ).all()
            assert txs == []

            notifications = db.scalars(
                select(Notification).where(Notification.category == "reminder")
            ).all()
            insufficient = [n for n in notifications if (n.payload_json or {}).get("kind") == "insufficient_funds"]
            assert len(insufficient) == 1

            # 重跑一次:同一期不该重复通知。
            result2 = materialize_due_transfer_rules(db)
            db.commit()
            assert result2["skipped_insufficient"] == 0
            notifications_again = db.scalars(
                select(Notification).where(Notification.category == "reminder")
            ).all()
            insufficient_again = [
                n for n in notifications_again if (n.payload_json or {}).get("kind") == "insufficient_funds"
            ]
            assert len(insufficient_again) == 1, "去重:同一期不该发第二次通知"
    finally:
        app.dependency_overrides.clear()


def test_transfer_recurring_rule_retries_successfully_after_balance_topped_up():
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr4@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR4"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 50.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "rectr4@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) - timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 200.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            from src.services.recurring_materializer import materialize_due_transfer_rules
            result = materialize_due_transfer_rules(db)
            db.commit()
            assert result["skipped_insufficient"] == 1

        # 帳戶入帳到夠付了。
        _push(client, hdr_app, ledger_id, "transaction", "tx-topup",
              {"syncId": "tx-topup", "type": "income", "amount": 500.0,
               "happenedAt": _iso(), "accountId": "acc-bank", "accountName": "銀行"})

        with TS() as db:
            from src.services.recurring_materializer import materialize_due_transfer_rules
            result2 = materialize_due_transfer_rules(db)
            db.commit()
            assert result2["materialized"] == 1
            assert result2["skipped_insufficient"] == 0

            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is not None

            txs = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.recurring_rule_sync_id == rule_id)
            ).all()
            assert len(txs) == 1
    finally:
        app.dependency_overrides.clear()


def test_inline_recurring_transfer_creates_origin_only_not_bulk():
    """`transactions.py` 的 inline `recurring` 分支(建交易當下順便設成
    週期起點):tx_type=="transfer" 時,這筆交易本身(使用者當下的真實操作)
    照常立刻建立,但不會像 expense/income 那樣連未來好幾期都一起批次生成。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr5@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR5"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 1000.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "rectr5@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        happened_at = datetime.now(timezone.utc)
        end_at = happened_at + timedelta(days=180)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 150.0,
                "happened_at": happened_at.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
                "recurring": {
                    "frequency": "monthly",
                    "interval": 1,
                    "end_at": end_at.isoformat(),
                },
            },
        )
        assert res.status_code == 200, res.text
        origin_tx_id = res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1, "只有这笔起点交易,不该预生成未来的期数"
        assert txs[0]["id"] == origin_tx_id

        with TS() as db:
            tx_row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == origin_tx_id))
            rule_id = tx_row.recurring_rule_sync_id
            assert rule_id
            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is not None
            assert rule_row.enabled is True
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 分類必填(需求 #14,2026-08 使用者回饋改善 SD Phase 12)
# ---------------------------------------------------------------------------


def test_create_recurring_rule_without_category_rejected():
    client, _TS = _make_client()
    try:
        owner = _register(client, "reccat1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECCAT1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "reccat1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 50.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 400, res.text
        assert _rules(client, hdr, ledger_id) == []
    finally:
        app.dependency_overrides.clear()


def test_create_transfer_recurring_rule_without_category_allowed():
    """轉帳規則沒有分類語意,不受分類必填限制。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "reccat2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECCAT2"
        _seed_ledger(client, app_token, device, ledger_id)
        _push(client, {"Authorization": f"Bearer {app_token}"}, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "bank_card", "currency": "CNY"})
        _push(client, {"Authorization": f"Bearer {app_token}"}, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})

        web = _login_web(client, "reccat2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 50.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-bank",
                "to_account_id": "acc-card",
            },
        )
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()


def test_update_recurring_rule_cannot_clear_category():
    """PATCH 顯式把非轉帳規則的分類清空(傳 category_id=null)應該被擋,
    維持既有分類不變。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "reccat3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECCAT3"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "reccat3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _seed_category(client, {"Authorization": f"Bearer {app_token}"}, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 50.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base, "category_id": None},
        )
        assert res.status_code == 400, res.text

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["category_id"] == "cat-1", "被拒绝的更新不该动到既有分类"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Phase 24(docs/PH17_USER_FEEDBACK_2026-08_SD.md 問題 A/B):RecurringRule
# 擴充 merchant/project_id/tag_ids + update-from 補齊 from_account_id/
# to_account_id/merchant/project_id/tag_ids 轉發
# ---------------------------------------------------------------------------


def test_create_recurring_rule_forwards_merchant_project_tags_to_occurrences():
    client, _TS = _make_client()
    try:
        owner = _register(client, "recext1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECEXT1"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        _push(client, hdr_app, ledger_id, "project", "proj-1",
              {"syncId": "proj-1", "name": "旅遊基金"})
        _push(client, hdr_app, ledger_id, "tag", "tag-1", {"syncId": "tag-1", "name": "固定支出"})

        web = _login_web(client, "recext1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 50.0,
                "category_id": "cat-1",
                "merchant": "全聯",
                "project_id": "proj-1",
                "tag_ids": ["tag-1"],
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["merchant"] == "全聯"
        assert rules[0]["project_id"] == "proj-1"
        assert rules[0]["tag_ids"] == ["tag-1"]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1
        assert txs[0]["merchant"] == "全聯"
        assert txs[0]["project_id"] == "proj-1"
        assert txs[0]["tag_ids"] == ["tag-1"]
        assert txs[0]["tags_list"] == ["固定支出"]
    finally:
        app.dependency_overrides.clear()


def test_recurring_update_from_forwards_merchant_project_tags_and_account():
    """Phase 24 問題 A/B:update-from 原本漏轉發新增的 merchant/project_id/
    tag_ids,這裡用一條會批次預生成的 expense 規則驗證「連同未來」正確帶到
    規則本身跟未來的 occurrence 交易,而 overridden 的那期依然被跳過。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recext2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECEXT2"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        _push(client, hdr_app, ledger_id, "account", "acc-a",
              {"syncId": "acc-a", "name": "帳戶A", "type": "cash", "currency": "CNY"})
        _push(client, hdr_app, ledger_id, "account", "acc-b",
              {"syncId": "acc-b", "name": "帳戶B", "type": "cash", "currency": "CNY"})
        _push(client, hdr_app, ledger_id, "project", "proj-2", {"syncId": "proj-2", "name": "專案二"})
        _push(client, hdr_app, ledger_id, "tag", "tag-2", {"syncId": "tag-2", "name": "標籤二"})

        web = _login_web(client, "recext2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=32)  # next_run/+1mo 两次
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 100.0,
                "category_id": "cat-1",
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
                "account_id": "acc-a",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        txs = sorted(_transactions(client, hdr, ledger_id), key=lambda t: t["happened_at"])
        assert len(txs) == 2
        occ0, occ1 = [t["id"] for t in txs]

        # 单独覆盖 occ1,之后不该被 update-from 动到。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{occ1}",
            headers=hdr,
            json={"base_change_id": base, "amount": 999.0},
        )
        assert res.status_code == 200, res.text

        # 連同未來:改帳戶 + merchant/project_id/tag_ids。
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/update-from/{occ0}",
            headers=hdr,
            json={
                "base_change_id": base,
                "account_id": "acc-b",
                "merchant": "新商家",
                "project_id": "proj-2",
                "tag_ids": ["tag-2"],
            },
        )
        assert res.status_code == 200, res.text

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["account_id"] == "acc-b", "update-from 也要更新规则本身的 account_id"
        assert rules[0]["merchant"] == "新商家"
        assert rules[0]["project_id"] == "proj-2"
        assert rules[0]["tag_ids"] == ["tag-2"]

        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[occ0]["account_id"] == "acc-b"
        assert txs[occ0]["merchant"] == "新商家"
        assert txs[occ0]["project_id"] == "proj-2"
        assert txs[occ0]["tag_ids"] == ["tag-2"]
        assert txs[occ0]["tags_list"] == ["標籤二"]

        assert txs[occ1]["account_id"] == "acc-a", "overridden 的期数不该被 update-from 覆盖"
        assert txs[occ1]["amount"] == 999.0
    finally:
        app.dependency_overrides.clear()


def test_recurring_update_from_forwards_transfer_from_to_account():
    """Phase 24 問題 A:update-from 原本完全漏轉發 from_account_id/
    to_account_id(即使 RecurringRule entity 本身早就有這兩個欄位)。轉帳規
    則建立當下不預生成 occurrence(見
    test_transfer_recurring_rule_not_bulk_generated_at_creation),這裡先用
    `materialize_due_transfer_rules` 生一筆起點交易當 update-from 的 anchor,
    驗證「連同未來」能正確改到轉出/轉入帳戶。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "recext3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECEXT3"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        for acc_id, name in (("acc-a", "帳戶A"), ("acc-b", "帳戶B"), ("acc-c", "帳戶C")):
            _push(client, hdr_app, ledger_id, "account", acc_id,
                  {"syncId": acc_id, "name": name, "type": "cash", "currency": "CNY",
                   "initialBalance": 1000.0})

        web = _login_web(client, "recext3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) - timedelta(days=1)  # 已到期
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 100.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-a",
                "to_account_id": "acc-b",
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            from src.services.recurring_materializer import materialize_due_transfer_rules
            result = materialize_due_transfer_rules(db)
            db.commit()
            assert result["materialized"] == 1

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1
        occ0 = txs[0]["id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/update-from/{occ0}",
            headers=hdr,
            json={
                "base_change_id": base,
                "from_account_id": "acc-a",
                "to_account_id": "acc-c",
            },
        )
        assert res.status_code == 200, res.text

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["to_account_id"] == "acc-c", "update-from 也要更新规则本身的 to_account_id"

        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[occ0]["to_account_id"] == "acc-c"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_recurring_rule_merchant_project_tags_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        tok = _register(client, "recmerge2@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) + timedelta(days=5)

        _push(client, hdr, "lg1", "recurring_rule", "rec-2", {
            "syncId": "rec-2",
            "amount": 100.0,
            "nextRunAt": next_run.isoformat(),
            "merchant": "星巴克",
            "projectId": "proj-x",
            "tagIds": ["tag-x", "tag-y"],
        })
        # partial update:只改 amount
        _push(client, hdr, "lg1", "recurring_rule", "rec-2", {
            "syncId": "rec-2",
            "amount": 150.0,
        })

        with TS() as db:
            row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-2",
                )
            )
            assert row is not None
            assert row.amount == 150.0
            assert row.merchant == "星巴克", "partial update 不该冲掉 merchant"
            assert row.project_sync_id == "proj-x", "partial update 不该冲掉 project_sync_id"
            import json as _json
            assert _json.loads(row.tag_sync_ids_json) == ["tag-x", "tag-y"]
    finally:
        app.dependency_overrides.clear()
