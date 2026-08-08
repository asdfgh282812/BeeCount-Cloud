"""需求 #14(2026-08 使用者回饋改善 SD Phase 12)一次性回填腳本契约:

`scripts/backfill_recurring_installment_categories.py` 把 `category_sync_id`
是 NULL 的舊週期性收支規則(非轉帳)/分期付款計畫,歸到使用者名下「未分類」
專屬分類。用 mobile `/sync/push` 直接建立這種「legacy 缺分類」的規則/計畫
(web write endpoint 現在已經擋分類必填,push 路徑不受這條限制,剛好拿來
模擬上線前的舊資料)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadInstallmentPlanProjection, ReadRecurringRuleProjection, UserCategoryProjection

import scripts.backfill_recurring_installment_categories as backfill_script


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


def _register(client, email):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
            "device_id": "d1",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": "upsert",
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


def test_backfill_fills_missing_categories_and_is_idempotent():
    client, TS = _make_client()
    try:
        tok = _register(client, "backfill1@example.com")["access_token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        next_run = datetime.now(timezone.utc) + timedelta(days=5)

        # legacy 週期性收支(非轉帳,沒帶 categoryId)
        _push(client, hdr, "lg1", "recurring_rule", "rec-legacy", {
            "syncId": "rec-legacy",
            "txType": "expense",
            "amount": 100.0,
            "frequency": "monthly",
            "interval": 1,
            "nextRunAt": next_run.isoformat(),
            "enabled": True,
        })
        # 轉帳規則沒有分類語意,即使沒帶 categoryId 也不該被腳本動到。
        _push(client, hdr, "lg1", "recurring_rule", "rec-transfer", {
            "syncId": "rec-transfer",
            "txType": "transfer",
            "amount": 50.0,
            "frequency": "monthly",
            "interval": 1,
            "nextRunAt": next_run.isoformat(),
            "enabled": True,
        })
        # legacy 分期付款(沒帶 categoryId)
        _push(client, hdr, "lg1", "installment_plan", "ins-legacy", {
            "syncId": "ins-legacy",
            "totalAmount": 1200.0,
            "periods": 12,
            "periodAmount": 100.0,
            "firstPeriodAt": next_run.isoformat(),
            "nextPeriodAt": next_run.isoformat(),
            "status": "active",
        })

        # monkeypatch 腳本用的 SessionLocal,指到跟這個測試同一個 in-memory
        # 引擎,模擬「對正式 DB 跑這支腳本」。
        backfill_script.SessionLocal = TS

        import sys
        old_argv = sys.argv

        # --dry-run 不應該寫入任何東西。
        try:
            sys.argv = ["backfill_recurring_installment_categories.py", "--dry-run"]
            rc_dry = backfill_script.main()
        finally:
            sys.argv = old_argv
        assert rc_dry == 0
        with TS() as db:
            row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-legacy"
                )
            )
            assert row is not None
            assert row.category_sync_id is None  # dry-run 不寫入

        try:
            sys.argv = ["backfill_recurring_installment_categories.py"]
            rc = backfill_script.main()
        finally:
            sys.argv = old_argv
        assert rc == 0

        with TS() as db:
            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-legacy"
                )
            )
            assert rule_row is not None
            assert rule_row.category_sync_id is not None
            cat = db.scalar(
                select(UserCategoryProjection).where(
                    UserCategoryProjection.sync_id == rule_row.category_sync_id
                )
            )
            assert cat is not None
            assert cat.name == "未分類"
            assert cat.kind == "expense"

            # 轉帳規則不受影響,依然沒有分類。
            transfer_row = db.scalar(
                select(ReadRecurringRuleProjection).where(
                    ReadRecurringRuleProjection.sync_id == "rec-transfer"
                )
            )
            assert transfer_row is not None
            assert transfer_row.category_sync_id is None

            plan_row = db.scalar(
                select(ReadInstallmentPlanProjection).where(
                    ReadInstallmentPlanProjection.sync_id == "ins-legacy"
                )
            )
            assert plan_row is not None
            assert plan_row.category_sync_id is not None
            # 分期付款恆為 expense,用同一個「未分類」分類(同 user 同 kind
            # 幂等複用同一筆,不會重複建立)。
            assert plan_row.category_sync_id == rule_row.category_sync_id

        # 重新跑一次:已经补过的规则/计划不应该再被处理(幂等)。
        try:
            sys.argv = ["backfill_recurring_installment_categories.py"]
            rc2 = backfill_script.main()
        finally:
            sys.argv = old_argv
        assert rc2 == 0

        with TS() as db:
            remaining = db.scalars(
                select(ReadRecurringRuleProjection.sync_id).where(
                    ReadRecurringRuleProjection.tx_type != "transfer",
                    ReadRecurringRuleProjection.category_sync_id.is_(None),
                )
            ).all()
            assert remaining == []
    finally:
        app.dependency_overrides.clear()
