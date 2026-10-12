"""彈性結帳日 + 繳款期限(MOZE 對標,alembic 0070)的帳戶欄位契約測試,比照
tests/test_account_include_in_total_sync.py 的風格:

- push payload 帶 `billingHolidayRule`/`billingHolidayRegion`/`paymentDueMode`/
  `billingCycleOverrides` → 落 user_account_projection 四個新欄位
- **merge 契約(CLAUDE.md 硬門檻)**:partial update(只改 name、不帶這些鍵)保持原值;
  空字串 / 空物件 = 明確清空;覆寫裡的壞資料被過濾
- /sync/full 的 snapshot_builder 帶這四個欄位
- /read/ledgers/{id}/accounts 回傳這四個欄位
- Web PATCH:值域校驗、改結帳日/規則時重設單期覆寫(值沒變不重設)、可清空
- billing-summary:結帳日遇假日順延後週期/繳款期限正確、帶出「調整本期結帳日」所需欄位
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadTxProjection, User, UserAccountProjection
from src.services import credit_card_billing


def _make_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
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


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": device_id,
            "client_type": client_type, "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d-app"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": datetime.now(timezone.utc).isoformat(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text


def _headers(client, email):
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    return (
        {"Authorization": f"Bearer {app_tok}"},
        {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"},
    )


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id, UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def _card(**extra):
    payload = {
        "syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "TWD",
        "billingDay": 15, "paymentDueDay": 7,
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# push / merge                                                                #
# --------------------------------------------------------------------------- #


def test_push_persists_flexible_billing_fields():
    client, TS = _make_client()
    try:
        hdr, _web = _headers(client, "fb1@t.com")
        _push(client, hdr, "lg1", "account", "acc-card", _card(
            billingHolidayRule="postpone", billingHolidayRegion="TW",
            paymentDueMode="days_after_closing",
            billingCycleOverrides={"2026-10": "2026-10-14", "bad": "x"},
        ))
        row = _account_row(TS, "fb1@t.com", "acc-card")
        assert row.billing_holiday_rule == "postpone"
        assert row.billing_holiday_region == "TW"
        assert row.payment_due_mode == "days_after_closing"
        # 壞資料被過濾,好的留下(key 排序後的 JSON 字串)
        assert json.loads(row.billing_cycle_overrides_json) == {"2026-10": "2026-10-14"}
    finally:
        app.dependency_overrides.clear()


def test_partial_update_keeps_flexible_billing_fields():
    """merge 契約:後續 push 只改 name、不帶新欄位 → 四個欄位保持原值,不被靜默沖成 null。"""
    client, TS = _make_client()
    try:
        hdr, _web = _headers(client, "fb2@t.com")
        _push(client, hdr, "lg1", "account", "acc-card", _card(
            billingHolidayRule="advance", billingHolidayRegion="JP",
            paymentDueMode="days_after_closing", billingCycleOverrides={"2026-11": "2026-11-03"},
        ))
        _push(client, hdr, "lg1", "account", "acc-card", {"syncId": "acc-card", "name": "信用卡改名"})
        row = _account_row(TS, "fb2@t.com", "acc-card")
        assert row.name == "信用卡改名"
        assert row.billing_holiday_rule == "advance"
        assert row.billing_holiday_region == "JP"
        assert row.payment_due_mode == "days_after_closing"
        assert json.loads(row.billing_cycle_overrides_json) == {"2026-11": "2026-11-03"}
    finally:
        app.dependency_overrides.clear()


def test_empty_string_and_empty_object_clear_fields():
    """App 關掉假日規則時送 ''(字串)/{}(覆寫)= 明確清空。"""
    client, TS = _make_client()
    try:
        hdr, _web = _headers(client, "fb3@t.com")
        _push(client, hdr, "lg1", "account", "acc-card", _card(
            billingHolidayRule="postpone", billingHolidayRegion="TW",
            paymentDueMode="days_after_closing", billingCycleOverrides={"2026-10": "2026-10-14"},
        ))
        _push(client, hdr, "lg1", "account", "acc-card", _card(
            billingHolidayRule="", billingHolidayRegion="", paymentDueMode="", billingCycleOverrides={},
        ))
        row = _account_row(TS, "fb3@t.com", "acc-card")
        assert row.billing_holiday_rule is None
        assert row.billing_holiday_region is None
        assert row.payment_due_mode is None
        assert row.billing_cycle_overrides_json is None
    finally:
        app.dependency_overrides.clear()


def test_snapshot_builder_and_read_endpoint_expose_fields():
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        hdr, web = _headers(client, "fb4@t.com")
        _push(client, hdr, "lg1", "ledger", "lg1", {"syncId": "lg1", "ledgerName": "账本", "currency": "TWD"})
        _push(client, hdr, "lg1", "account", "acc-card", _card(
            billingHolidayRule="postpone", billingHolidayRegion="TW",
            paymentDueMode="days_after_closing", billingCycleOverrides={"2026-10": "2026-10-14"},
        ))
        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        acc = next(a for a in snap["accounts"] if a["syncId"] == "acc-card")
        assert acc["billingHolidayRule"] == "postpone"
        assert acc["billingHolidayRegion"] == "TW"
        assert acc["paymentDueMode"] == "days_after_closing"
        assert acc["billingCycleOverrides"] == {"2026-10": "2026-10-14"}

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=web)
        assert r.status_code == 200, r.text
        out = next(x for x in r.json() if x["id"] == "acc-card")
        assert out["billing_holiday_rule"] == "postpone"
        assert out["billing_holiday_region"] == "TW"
        assert out["payment_due_mode"] == "days_after_closing"
        assert out["billing_cycle_overrides"] == {"2026-10": "2026-10-14"}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Web write                                                                   #
# --------------------------------------------------------------------------- #


def _patch(client, web, ledger, account, body):
    return client.patch(
        f"/api/v1/write/ledgers/{ledger}/accounts/{account}", headers=web,
        json={"base_change_id": 0, **body},
    )


def _seed_card(client, email):
    hdr, web = _headers(client, email)
    _push(client, hdr, "lgw", "ledger", "lgw", {"syncId": "lgw", "ledgerName": "账本", "currency": "TWD"})
    _push(client, hdr, "lgw", "account", "acc-card", _card())
    return hdr, web


def test_web_patch_sets_fields_and_validates_values():
    client, TS = _make_client()
    try:
        _hdr, web = _seed_card(client, "fbw1@t.com")
        r = _patch(client, web, "lgw", "acc-card", {
            "billing_holiday_rule": "postpone", "billing_holiday_region": "tw",
            "payment_due_mode": "days_after_closing",
        })
        assert r.status_code == 200, r.text
        row = _account_row(TS, "fbw1@t.com", "acc-card")
        assert (row.billing_holiday_rule, row.billing_holiday_region, row.payment_due_mode) == (
            "postpone", "TW", "days_after_closing",
        )
        for bad in (
            {"billing_holiday_rule": "later"},
            {"billing_holiday_region": "ZZ"},
            {"payment_due_mode": "weekly"},
            {"billing_cycle_overrides": {"2026-10": "nope"}},
        ):
            assert _patch(client, web, "lgw", "acc-card", bad).status_code in (400, 422), bad
    finally:
        app.dependency_overrides.clear()


def test_web_patch_overrides_reset_only_when_schedule_actually_changes():
    client, TS = _make_client()
    try:
        _hdr, web = _seed_card(client, "fbw2@t.com")
        assert _patch(client, web, "lgw", "acc-card", {
            "billing_holiday_rule": "postpone", "billing_holiday_region": "TW",
            "billing_cycle_overrides": {"2026-10": "2026-10-14"},
        }).status_code == 200
        assert _account_row(TS, "fbw2@t.com", "acc-card").billing_cycle_overrides_json is not None

        # 編輯表單整包帶 billing_day(值沒變)→ 覆寫不能被重設
        assert _patch(client, web, "lgw", "acc-card", {"billing_day": 15, "note": "x"}).status_code == 200
        assert _account_row(TS, "fbw2@t.com", "acc-card").billing_cycle_overrides_json is not None

        # 結帳日真的改了 → 重設
        assert _patch(client, web, "lgw", "acc-card", {"billing_day": 20}).status_code == 200
        assert _account_row(TS, "fbw2@t.com", "acc-card").billing_cycle_overrides_json is None
    finally:
        app.dependency_overrides.clear()


def test_web_patch_can_set_and_clear_overrides_and_rule():
    client, TS = _make_client()
    try:
        _hdr, web = _seed_card(client, "fbw3@t.com")
        assert _patch(client, web, "lgw", "acc-card", {
            "billing_holiday_rule": "advance", "billing_holiday_region": "JP",
            "billing_cycle_overrides": {"2026-10": "2026-10-14"},
        }).status_code == 200
        # 只清覆寫(帶 {}),規則不動
        assert _patch(client, web, "lgw", "acc-card", {"billing_cycle_overrides": {}}).status_code == 200
        row = _account_row(TS, "fbw3@t.com", "acc-card")
        assert row.billing_cycle_overrides_json is None
        assert row.billing_holiday_rule == "advance"
        # 關掉規則(null)
        assert _patch(client, web, "lgw", "acc-card", {
            "billing_holiday_rule": None, "billing_holiday_region": None,
        }).status_code == 200
        row = _account_row(TS, "fbw3@t.com", "acc-card")
        assert row.billing_holiday_rule is None and row.billing_holiday_region is None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# billing-summary                                                             #
# --------------------------------------------------------------------------- #


def test_billing_cycle_uses_postponed_closing_date_and_exposes_adjust_info():
    """結帳日 31、遇非工作日延後:2026-10-31 是週六 → 實際結帳日 11/2;繳款期限結帳日後第 7 天 → 11/9。"""
    client, TS = _make_client()
    try:
        hdr, _web = _headers(client, "fbs1@t.com")
        _push(client, hdr, "lgs", "ledger", "lgs", {"syncId": "lgs", "ledgerName": "账本", "currency": "TWD"})
        _push(client, hdr, "lgs", "account", "acc-card", {
            "syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "TWD",
            "billingDay": 31, "paymentDueDay": 7, "billingHolidayRule": "postpone",
            "billingHolidayRegion": "TW", "paymentDueMode": "days_after_closing",
        })
        _push(client, hdr, "lgs", "transaction", "tx1", {
            "syncId": "tx1", "type": "expense", "amount": 100.0,
            "happenedAt": datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc).isoformat(),
            "accountId": "acc-card", "accountName": "信用卡",
        })
        now = datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc)
        with TS() as db:
            account = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc-card"))
            children = credit_card_billing.resolve_billing_children(db, account=account)
            ledger_id = db.scalar(select(ReadTxProjection.ledger_id).limit(1))
            closed = credit_card_billing.compute_cycle_period_billing(
                db, ledger_id=ledger_id, group=account, children=children, now=now, cycle_offset=0,
            )
            # 11/5 時最近已結帳的是 10 月那期:實際結帳日 11/2(不是原定 10/31)
            assert closed["cycle_end"] == date(2026, 11, 2)
            assert closed["cycle_start"] == date(2026, 9, 30)
            assert closed["due_date"] == date(2026, 11, 9)
            assert closed["new_spend"] == 100.0  # 11/2 當天的消費算進這期
            assert closed["cycle_month_key"] == "2026-10"
            assert closed["cycle_adjusted"] is False
            assert closed["default_cycle_end"] == date(2026, 11, 2)
            assert closed["next_cycle_end"] == date(2026, 11, 30)
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_endpoint_returns_schedule_fields():
    client, _TS = _make_client()
    try:
        hdr, web = _headers(client, "fbs2@t.com")
        _push(client, hdr, "lgs2", "ledger", "lgs2", {"syncId": "lgs2", "ledgerName": "账本", "currency": "TWD"})
        _push(client, hdr, "lgs2", "account", "acc-card", {
            "syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "TWD",
            "billingDay": 15, "paymentDueDay": 7, "paymentDueMode": "days_after_closing",
            "billingHolidayRule": "advance", "billingHolidayRegion": "TW",
        })
        r = client.get("/api/v1/read/ledgers/lgs2/accounts/acc-card/billing-summary", headers=web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment_due_mode"] == "days_after_closing"
        assert body["billing_holiday_rule"] == "advance"
        assert body["billing_holiday_region"] == "TW"
        assert body["period_cycle_month_key"] is not None
        assert body["period_cycle_adjusted"] is False
        # 繳款期限 = 實際結帳日後第 7 天
        end = date.fromisoformat(body["period_cycle_end"][:10])
        due = date.fromisoformat(body["period_due_date"][:10])
        assert (due - end).days == 7
    finally:
        app.dependency_overrides.clear()
