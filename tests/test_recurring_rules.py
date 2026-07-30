"""週期性收支(§2.2 MOZE_FEATURE_GAP_SD.md Phase 1)—— recurring_rule entity 契约:

- web `/write/ledgers/{id}/recurring-rules` CRUD 落 projection + 能被
  `/read/ledgers/{id}/recurring-rules` 读到
- mobile `/sync/push` 的 `recurring_rule` merge 契约(CLAUDE.md 要求的
  `test_mobile_push_<entity>_partial_update_keeps_existing_fields` 风格):
  partial update 不带某字段时保留旧值,不能被静默冲成默认值
- `services.recurring_materializer.materialize_due_recurring_rules`:
  到期规则生成真正的交易 + 推进 next_run_at + 到 end_at 后自动 disable +
  落一条通知

============================================================================
手动检查清单 —— 本文件覆盖了物化逻辑本身(直接调函数),但下面两点是
pytest 测不到的运行时行为,真实环境上线后建议手动过一遍:

1. **main.py 的周期性 asyncio loop 真的会跑**:`make dev-api` 启动后,15 分钟
   内不会触发(`_RECURRING_MATERIALIZE_INTERVAL_SECONDS = 900`)。想立即验证,
   用 admin token 调 `POST /api/v1/internal/tasks/materialize-recurring`
   (curl 或 Web 的 API 调试面板都行),应返回
   `{"recurring_transactions": N, "installment_transactions": M}`。
2. **`sqlite3 beecount.db`** 直接查
   `SELECT * FROM read_recurring_rule_projection;` 确认 `next_run_at` 真的
   往后推了,`enabled` 该 disable 的确实变成 0。
3. 到期物化后,`GET /api/v1/notifications?category=reminder` 应该能看到一条
   "週期性记账已自动生成" 的通知。
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
from src.models import Notification, ReadRecurringRuleProjection, ReadTxProjection
from src.services.recurring_materializer import materialize_due_recurring_rules


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
# Web write CRUD round-trip
# ---------------------------------------------------------------------------


def test_web_create_recurring_rule_then_read_list():
    client, _TS = _make_client()
    try:
        owner = _register(client, "rec1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REC1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "rec1@example.com")
        token = web["access_token"]

        base = _latest_change_id(client, token, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 99.5,
                "note": "房租",
                "frequency": "monthly",
                "interval": 1,
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 200, res.text

        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/recurring-rules",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        rules = res.json()
        assert len(rules) == 1
        assert rules[0]["amount"] == 99.5
        assert rules[0]["note"] == "房租"
        assert rules[0]["frequency"] == "monthly"
        assert rules[0]["enabled"] is True
    finally:
        app.dependency_overrides.clear()


def test_web_update_and_delete_recurring_rule():
    client, _TS = _make_client()
    try:
        owner = _register(client, "rec2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_REC2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "rec2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        base = _latest_change_id(client, token, ledger_id)
        next_run = datetime.now(timezone.utc) + timedelta(days=1)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 50,
                "next_run_at": next_run.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        res = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base, "amount": 80, "enabled": False},
        )
        assert res.status_code == 200, res.text

        res = client.get(f"/api/v1/read/ledgers/{ledger_id}/recurring-rules", headers=hdr)
        rules = res.json()
        assert len(rules) == 1
        assert rules[0]["amount"] == 80.0
        assert rules[0]["enabled"] is False

        base = _latest_change_id(client, token, ledger_id)
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules/{rule_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text

        res = client.get(f"/api/v1/read/ledgers/{ledger_id}/recurring-rules", headers=hdr)
        assert res.json() == []
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mobile push merge 契约(CLAUDE.md 硬门槛)
# ---------------------------------------------------------------------------


def test_mobile_push_recurring_rule_partial_update_keeps_existing_fields():
    """先 push 一条完整规则,再 push 一条只带 amount 的 partial update ——
    note/frequency/category 等字段必须保留旧值,不能被冲成 None/默认值。"""
    client, TS = _make_client()
    try:
        tok = _register(client, "recmerge@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) + timedelta(days=5)

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
# 到期物化(services.recurring_materializer)
# ---------------------------------------------------------------------------


def test_materialize_due_rule_generates_tx_and_advances_next_run():
    client, TS = _make_client()
    try:
        owner = _register(client, "recmat1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECMAT1"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recmat1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        # 到期时间设成过去,materializer 一跑就该命中
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 42.0,
                "note": "订阅费",
                "frequency": "monthly",
                "next_run_at": past.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            ledger_row = db.execute(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            ).scalar_one()
            ledger_internal_id = ledger_row.ledger_id
            user_id = ledger_row.user_id
            old_next_run = ledger_row.next_run_at

            generated = materialize_due_recurring_rules(db)
            db.commit()
            assert generated == 1

            rows = db.scalars(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                )
            ).all()
            assert len(rows) == 1
            tx = rows[0]
            assert tx.amount == 42.0
            assert tx.tx_type == "expense"
            assert tx.note == "订阅费"

            refreshed_rule = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
            assert refreshed_rule.next_run_at > old_next_run, "next_run_at 必须推进到下一週期"
            assert refreshed_rule.enabled is True

            notif = db.scalar(
                select(Notification).where(Notification.user_id == user_id)
            )
            assert notif is not None
            assert notif.category == "reminder"

        # 再跑一次不该重复生成(next_run_at 已经推到未来)
        with TS() as db:
            generated_again = materialize_due_recurring_rules(db)
            db.commit()
            assert generated_again == 0
    finally:
        app.dependency_overrides.clear()


def test_materialize_disables_rule_past_end_at():
    """规则的下一次 next_run_at 会超过 end_at → 自动 disable,不再产生后续交易。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "recmat2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_RECMAT2"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "recmat2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        end_at = datetime.now(timezone.utc) + timedelta(days=1)  # 下一週期(+1月)必然超过它
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/recurring-rules",
            headers=hdr,
            json={
                "base_change_id": base,
                "amount": 10.0,
                "frequency": "monthly",
                "next_run_at": past.isoformat(),
                "end_at": end_at.isoformat(),
            },
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            generated = materialize_due_recurring_rules(db)
            db.commit()
            assert generated == 1

            rule = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
            assert rule.enabled is False, "下一週期超过 end_at 应自动停用"
    finally:
        app.dependency_overrides.clear()
