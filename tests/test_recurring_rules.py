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

import json
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


def test_delete_recurring_rule_keeps_all_occurrences_by_default():
    """2026-08-16 補:預設(delete_future_occurrences 不帶或 False)只刪規則
    本身,已產生的交易(含過去、未來)一律保留——維持刪規則端點原本的行為,
    不因為新增這個旗標而改變既有語意。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recdel1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECDEL1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recdel1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

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
        assert len(_transactions(client, hdr, ledger_id)) == 7

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        assert len(_transactions(client, hdr, ledger_id)) == 7, "未帶旗標 = 一律保留既有行為"
        assert _rules(client, hdr, ledger_id) == []
    finally:
        app.dependency_overrides.clear()


def test_delete_recurring_rule_with_delete_future_occurrences_removes_unhappened_keeps_past():
    """`delete_future_occurrences=True` 時,連同刪除尚未發生的已生成交易
    (跟 terminate-future 同一套篩選條件),已發生的交易保留,規則本身也真的
    從清單移除(跟只停用不刪除的 terminate-future 不同)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recdel2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECDEL2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recdel2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

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
        assert len(_transactions(client, hdr, ledger_id)) == 7

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base, "delete_future_occurrences": True},
        )
        assert res.status_code == 200, res.text

        txs_after = _transactions(client, hdr, ledger_id)
        assert len(txs_after) == 4, "day -3..0 四笔已发生,保留;+1..+3 三笔未发生,删除"
        assert _rules(client, hdr, ledger_id) == [], "規則本身要被刪除,不是只停用"
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


def test_transfer_recurring_rule_materializes_with_merchant_and_tags():
    """2026-08-17 使用者回饋:`materialize_due_transfer_rules`(自動扣繳,到期
    才逐筆生成的路徑)原本沒有轉發 merchant/tag_ids/reward_rule_ids 等欄位,
    導致每期自動生成的交易這些欄位都是空的——跟 `refill_recurring_windows`
    (一般收支的批次續窗路徑,同檔案)已經有的欄位補齊行為不一致。這裡驗證
    transfer 規則到期生成時 merchant/tag_ids 也會正確帶到交易上(project_id
    不能測:`_assert_project_exists` 拒絕 transfer 規則帶這個欄位;
    reward_rule_ids 也不能測:`_assert_reward_rules_valid` 要求
    `account_id`,transfer 規則只有 from/to account,建立當下就會被擋)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "rectr2b@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECTR2B"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "銀行", "type": "cash", "currency": "CNY",
               "initialBalance": 1000.0})
        _push(client, hdr_app, ledger_id, "account", "acc-card",
              {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"})
        _push(client, hdr_app, ledger_id, "tag", "tag-1", {"syncId": "tag-1", "name": "固定支出"})

        web = _login_web(client, "rectr2b@example.com")
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
                "merchant": "信用卡自動扣繳",
                "tag_ids": ["tag-1"],
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            from src.services.recurring_materializer import materialize_due_transfer_rules
            result = materialize_due_transfer_rules(db)
            db.commit()
            assert result["materialized"] == 1

            txs = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.recurring_rule_sync_id == rule_id)
            ).all()
            assert len(txs) == 1
            assert txs[0].merchant == "信用卡自動扣繳"
            assert json.loads(txs[0].tag_sync_ids_json) == ["tag-1"]
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


# ---------------------------------------------------------------------------
# 手續費/折扣/信用卡回饋(2026-08 使用者回饋:自動產生的第 2 期起遺失
# 「使用回饋」與「手續費/折扣明細」)
# ---------------------------------------------------------------------------


def _seed_reward_account(client, hdr_app, ledger_id, account_id="acc-card", rule_id="crr-1"):
    """信用卡帳戶 + 一條掛在它名下的回饋規則,供 reward_rule_ids 測試共用。"""
    _push(client, hdr_app, ledger_id, "account", account_id,
          {"syncId": account_id, "name": "信用卡", "type": "credit_card", "currency": "CNY"})
    _push(client, hdr_app, ledger_id, "card_reward_rule", rule_id,
          {"syncId": rule_id, "accountId": account_id, "label": "網購回饋",
           "rateType": "percentage", "rateValue": 2.0, "rounding": "round",
           "calcBasis": "transaction_date", "interval": "billing_cycle"})
    return account_id, rule_id


def test_create_recurring_rule_forwards_fee_discount_reward_to_all_occurrences():
    """獨立「週期性收支」建立端點:手續費/折扣/回饋規則要當成規則固定屬性,
    連第一期在內的每一期 occurrence 都要正確帶到,且 amount 要是伺服器依
    base/fee/discount 重算後的權威值,不是前端送來的原始 amount(這裡故意送
    一個算不對的原始 amount 驗證)。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recfee1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECFEE1"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        account_id, reward_id = _seed_reward_account(client, hdr_app, ledger_id)

        web = _login_web(client, "recfee1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = next_run + timedelta(days=61)  # next_run/+1mo/+2mo 三次
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 999.0,  # 故意送错,验证 server 端会用 base+fee-discount 重算覆盖
                "category_id": "cat-1",
                "account_id": account_id,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
                "base_amount": 100.0,
                "fee_amount": 20.0,
                "fee_label": "手續費",
                "discount_amount": 5.0,
                "discount_label": "折扣",
                "reward_rule_ids": [reward_id],
            },
        )
        assert res.status_code == 200, res.text
        expected_amount = 100.0 + 20.0 - 5.0

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["amount"] == expected_amount
        assert rules[0]["base_amount"] == 100.0
        assert rules[0]["fee_amount"] == 20.0
        assert rules[0]["fee_label"] == "手續費"
        assert rules[0]["discount_amount"] == 5.0
        assert rules[0]["discount_label"] == "折扣"
        assert rules[0]["reward_rule_ids"] == [reward_id]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 3, "next_run/+1mo/+2mo 三次落在 end_at 之前"
        for t in txs:
            assert t["amount"] == expected_amount
            assert t["base_amount"] == 100.0
            assert t["fee_amount"] == 20.0
            assert t["fee_label"] == "手續費"
            assert t["discount_amount"] == 5.0
            assert t["discount_label"] == "折扣"
            assert t["reward_rule_ids"] == [reward_id]
    finally:
        app.dependency_overrides.clear()


def test_recurring_occurrence_single_edit_account_change_drops_orphaned_reward_rules():
    """2026-08-16 bug 修正:「修改此記錄」(PATCH .../occurrences/{tx_id})
    的 schema 沒有 reward_rule_ids 欄位,單筆換帳戶時舊帳戶勾選的回饋規則
    會原封不動留在 merge 後的最終狀態、但已經對不上新帳戶,過去會被
    `_assert_reward_rules_valid` 硬擋成 400(使用者看到的「操作失敗，請稍後
    重試」)。現在應該靜默過濾掉,操作成功,且顯示名稱一併同步更新。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recocc2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECOCC2"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        card_id, reward_id = _seed_reward_account(client, hdr_app, ledger_id)
        _push(client, hdr_app, ledger_id, "account", "acc-cash",
              {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"})

        web = _login_web(client, "recocc2@example.com")
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
                "tx_type": "expense",
                "amount": 50.0,
                "category_id": "cat-1",
                "account_id": card_id,
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
                "reward_rule_ids": [reward_id],
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]
        occ = _transactions(client, hdr, ledger_id)[0]
        assert occ["reward_rule_ids"] == [reward_id]

        # 前端「修改此記錄」的實際 payload:只送 account_id,不會也不能送
        # reward_rule_ids(WriteRecurringOccurrenceUpdateRequest 沒這個欄位)。
        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{occ['id']}",
            headers=hdr,
            json={"base_change_id": base, "account_id": "acc-cash"},
        )
        assert res.status_code == 200, res.text

        updated = _transactions(client, hdr, ledger_id)[0]
        assert updated["account_id"] == "acc-cash"
        assert updated["account_name"] == "現金", "帳戶顯示名稱要跟著新 account_id 同步,不能停在舊值"
        assert not updated.get("reward_rule_ids"), "舊帳戶的回饋規則歸屬對不上新帳戶,要被靜默過濾掉"
    finally:
        app.dependency_overrides.clear()


def test_create_tx_with_recurring_inline_forwards_fee_discount_reward_to_all_occurrences():
    """`transactions.py` 建交易當下順便設為週期性收支起點:第一筆(使用者
    當下的真實操作)跟之後批次生成的每一期都要一致帶有手續費/折扣/回饋規則
    —— 這是使用者回報的原始 bug 場景:第一筆對、第二筆起遺失。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recfee2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECFEE2"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        account_id, reward_id = _seed_reward_account(client, hdr_app, ledger_id)

        web = _login_web(client, "recfee2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        happened_at = datetime.now(timezone.utc) + timedelta(days=1)
        end_at = happened_at + timedelta(days=61)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 999.0,
                "happened_at": happened_at.isoformat(),
                "category_id": "cat-1",
                "account_id": account_id,
                "base_amount": 200.0,
                "fee_amount": 10.0,
                "fee_label": "手續費",
                "discount_amount": 30.0,
                "discount_label": "折扣",
                "reward_rule_ids": [reward_id],
                "recurring": {
                    "frequency": "monthly",
                    "interval": 1,
                    "end_at": end_at.isoformat(),
                },
            },
        )
        assert res.status_code == 200, res.text
        expected_amount = 200.0 + 10.0 - 30.0

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["amount"] == expected_amount
        assert rules[0]["base_amount"] == 200.0
        assert rules[0]["reward_rule_ids"] == [reward_id]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 3, "next_run/+1mo/+2mo 三次落在 end_at 之前"
        for t in txs:
            assert t["amount"] == expected_amount
            assert t["base_amount"] == 200.0
            assert t["fee_amount"] == 10.0
            assert t["discount_amount"] == 30.0
            assert t["reward_rule_ids"] == [reward_id]
    finally:
        app.dependency_overrides.clear()


def test_refill_recurring_windows_forwards_fee_discount_reward_and_merchant_project_tags():
    """沒有 end_at 的長期規則,靠 `refill_recurring_windows` 續產生下一段
    視窗——這條路徑原本完全沒轉發 merchant/project/tags(既有 bug),手續費/
    折扣/回饋規則自然也是新加的。這裡驗證續產生的新交易兩類欄位都帶到。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "recfee3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECFEE3"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        account_id, reward_id = _seed_reward_account(client, hdr_app, ledger_id)
        _push(client, hdr_app, ledger_id, "project", "proj-fee", {"syncId": "proj-fee", "name": "專案"})
        _push(client, hdr_app, ledger_id, "tag", "tag-fee", {"syncId": "tag-fee", "name": "標籤"})

        web = _login_web(client, "recfee3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        next_run = datetime.now(timezone.utc) - timedelta(days=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 100.0,
                "category_id": "cat-1",
                "account_id": account_id,
                "merchant": "全聯",
                "project_id": "proj-fee",
                "tag_ids": ["tag-fee"],
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "base_amount": 80.0,
                "fee_amount": 20.0,
                "reward_rule_ids": [reward_id],
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
            tx_count_before = len(
                db.scalars(
                    select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger_internal_id)
                ).all()
            )
            # 模拟视窗快用完,逼一次续产生。
            rule_row.generated_until_at = datetime.now(timezone.utc) + timedelta(days=10)
            db.commit()

            generated = refill_recurring_windows(db)
            db.commit()
            assert generated > 0

        txs = sorted(_transactions(client, hdr, ledger_id), key=lambda t: t["happened_at"])
        assert len(txs) > tx_count_before
        new_tx = txs[-1]
        assert new_tx["merchant"] == "全聯"
        assert new_tx["project_id"] == "proj-fee"
        assert new_tx["tag_ids"] == ["tag-fee"]
        assert new_tx["base_amount"] == 80.0
        assert new_tx["fee_amount"] == 20.0
        assert new_tx["reward_rule_ids"] == [reward_id]
        assert new_tx["amount"] == 100.0
    finally:
        app.dependency_overrides.clear()


def test_recurring_rule_partial_update_keeps_fee_discount_reward_fields():
    """SOP 要求的 merge 契約測試:mobile push partial update 只帶不相關欄位
    時,手續費/折扣/回饋規則不能被冲掉;呼叫 `refill_recurring_windows` 前後
    也要保持不變(涵蓋 `_emit_recurring_rule_update` payload 曾經漏欄位、
    靠 `_upsert` 整列覆蓋語意把 merchant/project/tags 静默清空同一類 bug)。"""
    client, TS = _make_client()
    try:
        tok = _register(client, "recfee4@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) - timedelta(days=1)

        _push(client, hdr, "lg1", "recurring_rule", "rec-fee", {
            "syncId": "rec-fee",
            "txType": "expense",
            "amount": 115.0,
            "nextRunAt": next_run.isoformat(),
            "baseAmount": 100.0,
            "feeAmount": 20.0,
            "feeLabel": "手續費",
            "discountAmount": 5.0,
            "discountLabel": "折扣",
            "rewardRuleIds": ["crr-1"],
            "merchant": "星巴克",
        })
        # partial update:只改 note,手續費/折扣/回饋/merchant 都不该被冲掉。
        _push(client, hdr, "lg1", "recurring_rule", "rec-fee", {
            "syncId": "rec-fee",
            "note": "更新備註",
        })

        with TS() as db:
            row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-fee",
                )
            )
            assert row.note == "更新備註"
            assert row.base_amount == 100.0, "partial update 不该冲掉 base_amount"
            assert row.fee_amount == 20.0
            assert row.fee_label == "手續費"
            assert row.discount_amount == 5.0
            assert row.discount_label == "折扣"
            assert row.merchant == "星巴克", "partial update 不该冲掉 merchant"
            import json as _json
            assert _json.loads(row.reward_rule_sync_ids_json) == ["crr-1"]

            # 逼一次 refill(規則已經到期且沒有 end_at),驗證
            # _emit_recurring_rule_update 的欄位補齊沒有反過來把資料冲空。
            refill_recurring_windows(db)
            db.commit()

            refreshed = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-fee",
                )
            )
            assert refreshed.base_amount == 100.0, "refill 之後手續費/折扣不该被冲掉"
            assert refreshed.merchant == "星巴克", "refill 之後 merchant 不该被冲掉"
            assert _json.loads(refreshed.reward_rule_sync_ids_json) == ["crr-1"]
    finally:
        app.dependency_overrides.clear()


def test_recurring_update_from_forwards_fee_discount_reward():
    """`update-from` 端點(連同未來週期):手續費/折扣/回饋規則要能批次套用
    到規則本身 + 該期以後所有未 overridden 的已生成交易,overridden 的期数
    要跳过。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recfee5@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECFEE5"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _seed_category(client, hdr_app, ledger_id)
        account_id, reward_id = _seed_reward_account(client, hdr_app, ledger_id)

        web = _login_web(client, "recfee5@example.com")
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
                "account_id": account_id,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "end_at": end_at.isoformat(),
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

        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}/update-from/{occ0}",
            headers=hdr,
            json={
                "base_change_id": base,
                "base_amount": 90.0,
                "fee_amount": 15.0,
                "fee_label": "手續費",
                "reward_rule_ids": [reward_id],
            },
        )
        assert res.status_code == 200, res.text
        expected_amount = 90.0 + 15.0

        rules = _rules(client, hdr, ledger_id)
        assert rules[0]["amount"] == expected_amount
        assert rules[0]["base_amount"] == 90.0
        assert rules[0]["reward_rule_ids"] == [reward_id]

        txs = {t["id"]: t for t in _transactions(client, hdr, ledger_id)}
        assert txs[occ0]["amount"] == expected_amount
        assert txs[occ0]["base_amount"] == 90.0
        assert txs[occ0]["fee_amount"] == 15.0
        assert txs[occ0]["reward_rule_ids"] == [reward_id]

        assert txs[occ1]["amount"] == 999.0, "overridden 的期数不该被 update-from 覆盖"
        assert txs[occ1]["base_amount"] is None
    finally:
        app.dependency_overrides.clear()


def test_recurring_rule_transfer_rejects_fee_discount_fields():
    """transfer 沒有明確的收支方向語意,跟交易端一致,帶了手續費/折扣欄位
    直接 400。"""
    client, _TS = _make_client()
    try:
        owner = _register(client, "recfee6@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECFEE6"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-a",
              {"syncId": "acc-a", "name": "帳戶A", "type": "cash", "currency": "CNY"})
        _push(client, hdr_app, ledger_id, "account", "acc-b",
              {"syncId": "acc-b", "name": "帳戶B", "type": "cash", "currency": "CNY"})

        web = _login_web(client, "recfee6@example.com")
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
                "amount": 100.0,
                "frequency": "monthly",
                "next_run_at": next_run.isoformat(),
                "from_account_id": "acc-a",
                "to_account_id": "acc-b",
                "fee_amount": 5.0,
            },
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()
