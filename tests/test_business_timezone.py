"""業務時區(`LEDGER_TIMEZONE`,`services/business_time.py`)——信用卡回饋 / 帳單
週期的「日期歸屬」。

背景:交易 `happened_at` 是 UTC 瞬間,以前所有日期比對都用 UTC 日期,台灣(UTC+8)
使用者 00:00~08:00 的消費會被歸到前一天(活動起始日凌晨的消費被判成活動前一天、
每月 1 號凌晨的消費被算進上個月、結帳日邊界差一天)。

整個測試套件預設 `LEDGER_TIMEZONE=UTC`(tests/conftest.py),這支測試用 `taipei`
fixture 切到 Asia/Taipei;helper 單元測試則直接傳 `tz=` 參數,不碰設定。

時間換算備忘(台灣 = UTC+8):台灣 10/06 02:04 = 10/05 18:04Z;台灣 11/01 00:30 =
10/31 16:30Z。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import Settings, get_settings
from src.database import Base, get_db
from src.main import app
from src.models import (
    CardRewardPayout,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    User,
    UserAccountProjection,
)
from src.services import business_time as bt
from src.services import card_reward_payout, card_rewards, credit_card_billing

TPE = ZoneInfo("Asia/Taipei")
UTC = timezone.utc


def tpe(y, m, d, hh=0, mm=0, ss=0, us=0) -> datetime:
    """台灣牆上時間 → UTC aware(測試資料用,避免手算 -8 小時)。"""
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=TPE).astimezone(UTC)


@pytest.fixture
def taipei(monkeypatch):
    """把整個程序的業務時區切到 Asia/Taipei(env + 清 get_settings 快取)。"""
    monkeypatch.setenv("LEDGER_TIMEZONE", "Asia/Taipei")
    get_settings.cache_clear()
    assert bt.business_tz().key == "Asia/Taipei"
    yield
    # 先還原 env 再清快取,否則清完立刻又讀到還沒還原的 Asia/Taipei。
    monkeypatch.undo()
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# 測試基礎設施(比照 tests/test_card_reward_moze_extras.py)                    #
# --------------------------------------------------------------------------- #


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
        "action": "upsert", "updated_at": datetime.now(UTC).isoformat(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text


def _seed(client, ledger_id, email, *, billing_day=None, payment_due_day=None):
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "TWD"})
    card = {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "TWD"}
    if billing_day is not None:
        card["billingDay"] = billing_day
    if payment_due_day is not None:
        card["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card", card)
    _push(client, hdr_app, ledger_id, "account", "acc-wallet",
          {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "TWD"})
    return hdr_app, hdr_web


def _create_rule(client, hdr_web, ledger_id, **kwargs):
    payload = {"base_change_id": 0, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0}
    payload.update(kwargs)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules",
        headers=hdr_web, json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _spend(client, hdr_app, ledger_id, tx_id, when: datetime, amount, rule_id=None):
    payload = {
        "syncId": tx_id, "type": "expense", "amount": amount, "happenedAt": when.isoformat(),
        "accountId": "acc-card", "accountName": "信用卡",
    }
    if rule_id is not None:
        payload["rewardRuleIds"] = [rule_id]
    _push(client, hdr_app, ledger_id, "transaction", tx_id, payload)


def _rule_row(db, sync_id) -> ReadCardRewardRuleProjection:
    row = db.scalar(
        select(ReadCardRewardRuleProjection).where(ReadCardRewardRuleProjection.sync_id == sync_id)
    )
    assert row is not None
    return row


def _internal_ledger_id(db) -> str:
    ledger_id = db.scalar(select(ReadTxProjection.ledger_id).limit(1))
    assert ledger_id is not None
    return ledger_id


def _qualifying_ids(db, rule_id, period_start, period_end, **kw) -> set[str]:
    rule = _rule_row(db, rule_id)
    items = card_rewards._qualifying_transactions(
        db, ledger_id=_internal_ledger_id(db), rule=rule,
        period_start=period_start, period_end=period_end, **kw,
    )
    return {item["tx"].sync_id for item in items}


def _aware(dt: datetime) -> datetime:
    """SQLite 讀回的 DateTime 是 naive UTC。"""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# --------------------------------------------------------------------------- #
# (7) business_time helper 單元測試(直接傳 tz,不碰設定)                       #
# --------------------------------------------------------------------------- #


def test_to_business_date_handles_aware_and_naive_inputs():
    # aware UTC:台灣 10/06 02:04 = 10/05 18:04Z。
    assert bt.to_business_date(datetime(2026, 10, 5, 18, 4, tzinfo=UTC), tz=TPE) == date(2026, 10, 6)
    assert bt.to_business_date(datetime(2026, 10, 5, 15, 59, tzinfo=UTC), tz=TPE) == date(2026, 10, 5)
    # naive 視為 UTC(SQLite 讀回的 DateTime(timezone=True))。
    assert bt.to_business_date(datetime(2026, 10, 5, 18, 4), tz=TPE) == date(2026, 10, 6)
    assert bt.to_business_date(datetime(2026, 10, 5, 15, 59), tz=TPE) == date(2026, 10, 5)
    # 其它 offset 的 aware 輸入也正確換算。
    plus8 = timezone(timedelta(hours=8))
    assert bt.to_business_date(datetime(2026, 10, 6, 2, 4, tzinfo=plus8), tz=TPE) == date(2026, 10, 6)
    minus5 = timezone(timedelta(hours=-5))
    assert bt.to_business_date(datetime(2026, 10, 5, 13, 30, tzinfo=minus5), tz=TPE) == date(2026, 10, 6)
    # UTC 部署:行為與舊的 `.date()` 完全一致。
    assert bt.to_business_date(datetime(2026, 10, 5, 18, 4, tzinfo=UTC), tz=UTC) == date(2026, 10, 5)


def test_business_today_uses_given_instant():
    assert bt.business_today(datetime(2026, 10, 5, 17, 0, tzinfo=UTC), tz=TPE) == date(2026, 10, 6)
    assert bt.business_today(datetime(2026, 10, 5, 15, 59, 59, tzinfo=UTC), tz=TPE) == date(2026, 10, 5)
    assert bt.business_today(datetime(2026, 10, 5, 17, 0), tz=TPE) == date(2026, 10, 6)  # naive
    # 省略 now 時取系統當下,型別正確即可。
    assert isinstance(bt.business_today(tz=TPE), date)


def test_business_date_boundaries_are_utc_aware_and_roundtrip():
    d = date(2026, 10, 6)
    start = bt.business_date_start_utc(d, tz=TPE)
    end = bt.business_date_end_utc(d, tz=TPE)
    assert start == datetime(2026, 10, 5, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 6, 15, 59, 59, 999999, tzinfo=UTC)
    assert start.utcoffset() == timedelta(0) and end.utcoffset() == timedelta(0)
    # 起訖瞬間回頭換日期都是 d;差 1 微秒就是前/後一天。
    assert bt.to_business_date(start, tz=TPE) == d
    assert bt.to_business_date(end, tz=TPE) == d
    assert bt.to_business_date(start - timedelta(microseconds=1), tz=TPE) == d - timedelta(days=1)
    assert bt.to_business_date(end + timedelta(microseconds=1), tz=TPE) == d + timedelta(days=1)
    # UTC 部署:跟舊的 `datetime(d.year, d.month, d.day, tzinfo=utc)` 完全一致。
    assert bt.business_date_start_utc(d, tz=UTC) == datetime(2026, 10, 6, tzinfo=UTC)
    assert bt.business_date_end_utc(d, tz=UTC) == datetime(2026, 10, 6, 23, 59, 59, 999999, tzinfo=UTC)


def test_business_date_boundaries_follow_dst_of_zone():
    """業務時區若有 DST,同一個時區在不同日期的 UTC 偏移不同,邊界要跟著走
    (台灣沒有 DST,這裡用 America/New_York 驗證 helper 不寫死固定 offset)。"""
    ny = ZoneInfo("America/New_York")
    assert bt.business_date_start_utc(date(2026, 3, 7), tz=ny) == datetime(2026, 3, 7, 5, 0, tzinfo=UTC)  # EST
    assert bt.business_date_start_utc(date(2026, 3, 9), tz=ny) == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)  # EDT
    # DST 當天只有 23 小時,起訖相差 22:59:59.999999 + 1h 的偏移。
    start = bt.business_date_start_utc(date(2026, 3, 8), tz=ny)
    end = bt.business_date_end_utc(date(2026, 3, 8), tz=ny)
    assert end - start == timedelta(hours=23) - timedelta(microseconds=1)


def test_business_datetime_with_time_of_uses_business_wall_clock():
    source = datetime(2026, 10, 5, 12, 0, 7, 123456, tzinfo=UTC)  # 台灣 10/05 20:00:07.123456
    got = bt.business_datetime_with_time_of(date(2026, 10, 6), source, tz=TPE)
    assert got == datetime(2026, 10, 6, 12, 0, 7, 123456, tzinfo=UTC)  # 台灣 10/06 20:00:07.123456
    assert bt.to_business_date(got, tz=TPE) == date(2026, 10, 6)
    # naive 來源視為 UTC。
    assert bt.business_datetime_with_time_of(date(2026, 10, 6), source.replace(tzinfo=None), tz=TPE) == got
    # 跨日:台灣 10/06 00:30 的消費(10/05 16:30Z)結算到 10/07,仍是台灣 10/07 00:30。
    cross = bt.business_datetime_with_time_of(date(2026, 10, 7), tpe(2026, 10, 6, 0, 30), tz=TPE)
    assert cross == tpe(2026, 10, 7, 0, 30)


def test_card_rewards_wrappers_delegate_to_business_time(taipei):
    assert card_rewards._date_to_utc_dt(date(2026, 10, 6)) == tpe(2026, 10, 6)
    assert card_rewards._date_to_utc_dt(date(2026, 10, 6), end_of_day=True) == tpe(2026, 10, 6, 23, 59, 59, 999999)
    assert credit_card_billing.date_to_utc_dt(date(2026, 10, 6)) == tpe(2026, 10, 6)
    assert credit_card_billing.date_to_utc_dt(date(2026, 10, 6), end_of_day=True) == tpe(2026, 10, 6, 23, 59, 59, 999999)
    assert card_rewards.combine_settlement_date_with_source_time(
        date(2026, 10, 6), tpe(2026, 10, 5, 20, 0, 7),
    ) == tpe(2026, 10, 6, 20, 0, 7)


# --------------------------------------------------------------------------- #
# (6) LEDGER_TIMEZONE 設定驗證                                                 #
# --------------------------------------------------------------------------- #


def test_ledger_timezone_defaults_to_asia_taipei(monkeypatch):
    monkeypatch.delenv("LEDGER_TIMEZONE", raising=False)
    assert Settings(_env_file=None).ledger_timezone == "Asia/Taipei"


def test_ledger_timezone_blank_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("LEDGER_TIMEZONE", "  ")
    assert Settings(_env_file=None).ledger_timezone == "Asia/Taipei"


def test_ledger_timezone_accepts_valid_iana_name(monkeypatch):
    monkeypatch.setenv("LEDGER_TIMEZONE", "America/New_York")
    assert Settings(_env_file=None).ledger_timezone == "America/New_York"


@pytest.mark.parametrize("bad", ["Mars/Olympus", "Taipei", "UTC+8", "../etc/passwd"])
def test_ledger_timezone_invalid_value_fails_at_settings_load(monkeypatch, bad):
    monkeypatch.setenv("LEDGER_TIMEZONE", bad)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    # 錯誤訊息要點名變數與錯誤值,維運一眼看得懂。
    assert "LEDGER_TIMEZONE" in str(exc.value)
    assert bad in str(exc.value)


def test_invalid_ledger_timezone_makes_get_settings_raise(monkeypatch):
    monkeypatch.setenv("LEDGER_TIMEZONE", "Not/AZone")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# (1)(5) 規則起訖日:以業務日期比對,含頭含尾                                  #
# --------------------------------------------------------------------------- #

RULE_WINDOW = {"starts_at": "2026-10-06T00:00:00Z", "ends_at": "2026-10-31T00:00:00Z"}


def test_rule_start_and_end_dates_compare_by_business_date(taipei):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt1", "bt1@t.com")
        rule_id = _create_rule(client, hdr_web, "lg-bt1", interval="calendar_month", **RULE_WINDOW)
        _spend(client, hdr_app, "lg-bt1", "tx-before", tpe(2026, 10, 5, 23, 59), 100.0, rule_id)  # 活動前一天
        _spend(client, hdr_app, "lg-bt1", "tx-start", tpe(2026, 10, 6, 2, 4), 100.0, rule_id)     # 起始日凌晨(10/05 18:04Z)
        _spend(client, hdr_app, "lg-bt1", "tx-end", tpe(2026, 10, 31, 23, 30), 100.0, rule_id)    # 迄日當天晚上(10/31 15:30Z)
        _spend(client, hdr_app, "lg-bt1", "tx-after", tpe(2026, 11, 1, 0, 30), 100.0, rule_id)    # 迄日隔天凌晨(10/31 16:30Z)

        with TS() as db:
            got = _qualifying_ids(db, rule_id, date(2026, 10, 1), date(2026, 10, 31))
        assert got == {"tx-start", "tx-end"}
    finally:
        app.dependency_overrides.clear()


def test_rule_window_comparison_is_unchanged_under_utc_deployment():
    """對照組(套件預設 LEDGER_TIMEZONE=UTC):同樣的資料用 UTC 日期歸屬——
    台灣 10/06 02:04(10/05 18:04Z)落在 10/05,不算;台灣 11/01 00:30
    (10/31 16:30Z)落在 10/31,算。證明預設行為(UTC 部署)維持舊語意。"""
    assert bt.business_tz().key == "UTC"
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt1u", "bt1u@t.com")
        rule_id = _create_rule(client, hdr_web, "lg-bt1u", interval="calendar_month", **RULE_WINDOW)
        _spend(client, hdr_app, "lg-bt1u", "tx-before", tpe(2026, 10, 5, 23, 59), 100.0, rule_id)
        _spend(client, hdr_app, "lg-bt1u", "tx-start", tpe(2026, 10, 6, 2, 4), 100.0, rule_id)
        _spend(client, hdr_app, "lg-bt1u", "tx-end", tpe(2026, 10, 31, 23, 30), 100.0, rule_id)
        _spend(client, hdr_app, "lg-bt1u", "tx-after", tpe(2026, 11, 1, 0, 30), 100.0, rule_id)
        with TS() as db:
            got = _qualifying_ids(db, rule_id, date(2026, 10, 1), date(2026, 10, 31))
        assert got == {"tx-end", "tx-after"}
    finally:
        app.dependency_overrides.clear()


def test_custom_range_includes_both_end_days_by_business_date(taipei):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt5", "bt5@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lg-bt5", interval="custom_range",
            starts_at="2026-10-06T00:00:00Z", ends_at="2026-10-08T00:00:00Z",
        )
        _spend(client, hdr_app, "lg-bt5", "tx-pre", tpe(2026, 10, 5, 23, 59, 30), 100.0, rule_id)
        _spend(client, hdr_app, "lg-bt5", "tx-first", tpe(2026, 10, 6, 0, 0, 30), 100.0, rule_id)   # 起日剛過零點
        _spend(client, hdr_app, "lg-bt5", "tx-last", tpe(2026, 10, 8, 23, 59, 30), 100.0, rule_id)  # 迄日最後一分鐘
        _spend(client, hdr_app, "lg-bt5", "tx-post", tpe(2026, 10, 9, 0, 0, 30), 100.0, rule_id)

        with TS() as db:
            rule = _rule_row(db, rule_id)
            period = card_rewards._custom_range_period(rule)
            assert period == (date(2026, 10, 6), date(2026, 10, 8))  # 純日期:取 UTC 年月日,不轉時區
            assert _qualifying_ids(db, rule_id, *period) == {"tx-first", "tx-last"}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# (2) calendar_month:每月 1 號凌晨歸當月                                       #
# --------------------------------------------------------------------------- #


def test_calendar_month_first_day_early_morning_belongs_to_new_month(taipei):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt2", "bt2@t.com")
        rule_id = _create_rule(client, hdr_web, "lg-bt2", interval="calendar_month")
        _spend(client, hdr_app, "lg-bt2", "tx-oct-first", tpe(2026, 10, 1, 0, 30), 100.0, rule_id)   # 09/30 16:30Z
        _spend(client, hdr_app, "lg-bt2", "tx-oct-last", tpe(2026, 10, 31, 23, 30), 200.0, rule_id)  # 10/31 15:30Z
        _spend(client, hdr_app, "lg-bt2", "tx-nov-first", tpe(2026, 11, 1, 0, 30), 400.0, rule_id)   # 10/31 16:30Z

        with TS() as db:
            assert _qualifying_ids(db, rule_id, date(2026, 10, 1), date(2026, 10, 31)) == {
                "tx-oct-first", "tx-oct-last",
            }
            assert _qualifying_ids(db, rule_id, date(2026, 11, 1), date(2026, 11, 30)) == {"tx-nov-first"}
    finally:
        app.dependency_overrides.clear()


def test_compute_account_card_rewards_picks_period_by_business_today(taipei):
    """`now` = 台灣 11/01 00:30(10/31 16:30Z):業務日期已是 11/01,當期是 11 月。"""
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt2b", "bt2b@t.com")
        rule_id = _create_rule(client, hdr_web, "lg-bt2b", interval="calendar_month")
        _spend(client, hdr_app, "lg-bt2b", "tx-oct", tpe(2026, 10, 31, 23, 30), 100.0, rule_id)
        _spend(client, hdr_app, "lg-bt2b", "tx-nov", tpe(2026, 11, 1, 0, 10), 300.0, rule_id)

        with TS() as db:
            account = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc-card"))
            rule = _rule_row(db, rule_id)
            results = card_rewards.compute_account_card_rewards(
                db, ledger_id=_internal_ledger_id(db), account=account, rules=[rule],
                now=tpe(2026, 11, 1, 0, 30), period_offset=0,
            )
            assert len(results) == 1
            r = results[0]
            assert (r["period_start"], r["period_end"]) == (date(2026, 11, 1), date(2026, 11, 30))
            assert r["qualifying_spend"] == 300.0
            assert r["raw_reward"] == 30.0

            # 上一期(10 月)只含 10/31 的那筆。
            prev = card_rewards.compute_account_card_rewards(
                db, ledger_id=_internal_ledger_id(db), account=account, rules=[rule],
                now=tpe(2026, 11, 1, 0, 30), period_offset=-1,
            )
            assert (prev[0]["period_start"], prev[0]["period_end"]) == (date(2026, 10, 1), date(2026, 10, 31))
            assert prev[0]["qualifying_spend"] == 100.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# (3) 帳單週期結帳日邊界                                                        #
# --------------------------------------------------------------------------- #


def test_billing_cycle_closing_day_boundary_uses_business_date(taipei):
    """結帳日 15 號。台灣 10/15 23:30(15:30Z)是結帳日當天最後時段,屬於 9/15~10/15
    這期;台灣 10/16 00:30(10/15 16:30Z)已是下一期,UTC 日期仍是 10/15 但不能歸前一期。"""
    client, TS = _make_client()
    try:
        hdr_app, _hdr_web = _seed(client, "lg-bt3", "bt3@t.com", billing_day=15, payment_due_day=1)
        _spend(client, hdr_app, "lg-bt3", "tx-closing-day", tpe(2026, 10, 15, 23, 30), 100.0)
        _spend(client, hdr_app, "lg-bt3", "tx-next-cycle", tpe(2026, 10, 16, 0, 30), 200.0)
        _spend(client, hdr_app, "lg-bt3", "tx-prev-cycle", tpe(2026, 9, 16, 0, 30), 50.0)  # 9/15 16:30Z,屬 9/15~10/15

        now = tpe(2026, 10, 20, 12, 0)
        with TS() as db:
            account = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc-card"))
            children = credit_card_billing.resolve_billing_children(db, account=account)
            ledger_id = _internal_ledger_id(db)

            closed = credit_card_billing.compute_cycle_period_billing(
                db, ledger_id=ledger_id, group=account, children=children, now=now, cycle_offset=0,
            )
            assert (closed["cycle_start"], closed["cycle_end"]) == (date(2026, 9, 15), date(2026, 10, 15))
            assert closed["new_spend"] == 150.0  # 100 + 50;不含 10/16 凌晨的 200

            open_cycle = credit_card_billing.compute_cycle_period_billing(
                db, ledger_id=ledger_id, group=account, children=children, now=now, cycle_offset=1,
            )
            assert (open_cycle["cycle_start"], open_cycle["cycle_end"]) == (date(2026, 10, 15), date(2026, 11, 15))
            assert open_cycle["new_spend"] == 200.0
    finally:
        app.dependency_overrides.clear()


def test_group_billing_closed_cycle_uses_business_today(taipei):
    """`now` = 台灣 10/15 01:00(10/14 17:00Z):業務日期已是結帳日 10/15,
    「最近一次已結束的週期」是 9/15~10/15;UTC 日期還是 10/14,會誤判成 8/15~9/15。"""
    client, TS = _make_client()
    try:
        hdr_app, _hdr_web = _seed(client, "lg-bt3b", "bt3b@t.com", billing_day=15, payment_due_day=1)
        _spend(client, hdr_app, "lg-bt3b", "tx-1", tpe(2026, 10, 1, 12, 0), 100.0)
        with TS() as db:
            account = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc-card"))
            children = credit_card_billing.resolve_billing_children(db, account=account)
            billing = credit_card_billing.compute_group_billing(
                db, ledger_id=_internal_ledger_id(db), group=account, children=children,
                now=tpe(2026, 10, 15, 1, 0),
            )
            assert (billing["cycle_start"], billing["cycle_end"]) == (date(2026, 9, 15), date(2026, 10, 15))
            assert billing["open_cycle_start"] == date(2026, 9, 15)
    finally:
        app.dependency_overrides.clear()


def test_billing_summary_response_keeps_utc_midnight_date_labels(taipei):
    """對外回傳的 cycle_start/cycle_end/due_date 是「日期標籤」,前端只取前 10 碼,
    必須維持 UTC 零點編碼——換成業務時區零點(台灣 = 前一天 16:00Z)會讓畫面少一天。"""
    client, _TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt3c", "bt3c@t.com", billing_day=15, payment_due_day=1)
        r = client.get("/api/v1/read/ledgers/lg-bt3c/accounts/acc-card/billing-summary", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        for key in ("cycle_end", "open_cycle_end", "period_cycle_end"):
            parsed = datetime.fromisoformat(body[key].replace("Z", "+00:00"))
            assert parsed.day == 15, (key, body[key])
            assert (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0), (key, body[key])
            assert parsed.utcoffset() == timedelta(0)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# (4) 逐筆入帳:now / 入帳日 / 回饋交易時間                                     #
# --------------------------------------------------------------------------- #


def _tick(TS, now):
    with TS() as db:
        result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
        db.commit()
    return result


def _incomes(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.account_sync_id == account_id, ReadTxProjection.tx_type == "income",
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def test_per_tx_settlement_uses_business_today_and_aligns_reward_time(taipei):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt4", "bt4@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lg-bt4", interval="calendar_month",
            settlement_type="immediate_after_tx", settlement_days=1, reward_account_id="acc-wallet",
        )
        # 台灣 10/05 20:00:07(12:00:07Z)消費,settlement_days=1 → 入帳日 台灣 10/06。
        _spend(client, hdr_app, "lg-bt4", "tx-1", tpe(2026, 10, 5, 20, 0, 7), 100.0, rule_id)

        # 台灣 10/05 23:00(15:00Z):還沒到入帳日。
        assert _tick(TS, tpe(2026, 10, 5, 23, 0)) == {"tx_payouts": 0, "period_payouts": 0}
        assert _incomes(TS, "acc-wallet") == []

        # now = 2026-10-05T17:00:00Z = 台灣 10/06 01:00 → 業務日期 10/06,已到入帳日。
        # (UTC 日期仍是 10/05,舊行為會再多等 7 小時。)
        now = datetime(2026, 10, 5, 17, 0, tzinfo=UTC)
        assert _tick(TS, now) == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _incomes(TS, "acc-wallet")
        assert len(incomes) == 1 and incomes[0].amount == 10.0
        # 回饋交易 happened_at = 入帳日(台灣 10/06)+ 來源交易的台灣牆上時間 20:00:07。
        assert _aware(incomes[0].happened_at) == tpe(2026, 10, 6, 20, 0, 7)
        assert bt.to_business_date(incomes[0].happened_at) == date(2026, 10, 6)

        # 重跑不重複入帳。
        assert _tick(TS, now) == {"tx_payouts": 0, "period_payouts": 0}
    finally:
        app.dependency_overrides.clear()


def test_per_tx_settlement_days_zero_pays_immediately_and_keeps_source_wall_time(taipei):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt4b", "bt4b@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lg-bt4b", interval="calendar_month",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        source_at = tpe(2026, 10, 6, 0, 30)  # 10/05 16:30Z
        _spend(client, hdr_app, "lg-bt4b", "tx-1", source_at, 100.0, rule_id)

        assert _tick(TS, datetime(2026, 10, 5, 17, 0, tzinfo=UTC)) == {"tx_payouts": 1, "period_payouts": 0}
        incomes = _incomes(TS, "acc-wallet")
        assert len(incomes) == 1
        assert _aware(incomes[0].happened_at) == source_at  # 對齊來源交易的台灣 00:30
    finally:
        app.dependency_overrides.clear()


def test_per_tx_cap_tracks_calendar_month_by_business_date(taipei):
    """逐筆結算的月上限(cap)以來源消費的業務日期決定歸哪個月:
    台灣 11/01 00:30(10/31 16:30Z)的消費屬 11 月,不與 10 月共用 cap。"""
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt4c", "bt4c@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lg-bt4c", interval="calendar_month", cap_amount=10.0,
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lg-bt4c", "tx-oct", tpe(2026, 10, 20, 12, 0), 100.0, rule_id)      # 回饋 10 = 10 月額滿
        _spend(client, hdr_app, "lg-bt4c", "tx-nov", tpe(2026, 11, 1, 0, 30), 100.0, rule_id)       # 11 月,不受 10 月額滿影響

        assert _tick(TS, tpe(2026, 11, 2, 12, 0)) == {"tx_payouts": 2, "period_payouts": 0}
        assert [t.amount for t in _incomes(TS, "acc-wallet")] == [10.0, 10.0]
    finally:
        app.dependency_overrides.clear()


def test_period_end_payout_counts_business_month_and_stamps_settlement_start(taipei):
    client, TS = _make_client()
    email = "bt4d@t.com"
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt4d", email)
        rule_id = _create_rule(
            client, hdr_web, "lg-bt4d", interval="calendar_month",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        # 台灣 11/01 00:30(10/31 16:30Z)→ 業務日期屬 11 月;UTC 日期屬 10 月。
        _spend(client, hdr_app, "lg-bt4d", "tx-nov", tpe(2026, 11, 1, 0, 30), 100.0, rule_id)

        # 台灣 12/01 10:00:前一期 = 11 月,期末 11/30 → 當天入帳。
        assert _tick(TS, tpe(2026, 12, 1, 10, 0)) == {"tx_payouts": 0, "period_payouts": 1}
        incomes = _incomes(TS, "acc-wallet")
        assert [t.amount for t in incomes] == [10.0]
        # 期末結算沒有單一來源消費可對齊 → 入帳日(台灣 11/30)的 00:00。
        assert _aware(incomes[0].happened_at) == tpe(2026, 11, 30)
        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            keys = db.scalars(
                select(CardRewardPayout.dedup_key).where(CardRewardPayout.user_id == user_id)
            ).all()
        assert keys == ["2026-11-30"]
    finally:
        app.dependency_overrides.clear()


def test_custom_range_period_end_waits_until_campaign_over_in_business_time(taipei):
    """活動 ends_at=10/08:台灣 10/08 23:30(15:30Z)仍在活動最後一天,不結算;
    台灣 10/09 00:30(10/08 16:30Z)才結算(UTC 日期仍是 10/08,舊行為會再多等 7.5 小時)。"""
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-bt4e", "bt4e@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lg-bt4e", interval="custom_range",
            starts_at="2026-10-06T00:00:00Z", ends_at="2026-10-08T00:00:00Z",
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lg-bt4e", "tx-1", tpe(2026, 10, 8, 23, 0), 100.0, rule_id)

        assert _tick(TS, tpe(2026, 10, 8, 23, 30)) == {"tx_payouts": 0, "period_payouts": 0}
        assert _tick(TS, tpe(2026, 10, 9, 0, 30)) == {"tx_payouts": 0, "period_payouts": 1}
        assert [t.amount for t in _incomes(TS, "acc-wallet")] == [10.0]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# workspace:帳戶詳情清單的業務日期區間 + 記帳天數統計                          #
# --------------------------------------------------------------------------- #


def test_workspace_transactions_day_range_uses_business_date(taipei):
    """帳單週期以「日期」定義,清單用 day_from/day_to(業務日期,含頭含尾);
    台灣 10/06 02:04(= 10/05 18:04Z)要算進 10/06 起的區間,台灣 10/06 23:59 與
    10/07 00:30 則分別落在 10/06 與 10/07。"""
    client, _TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-ws1", "ws1@t.com")
        _spend(client, hdr_app, "lg-ws1", "t-before", tpe(2026, 10, 5, 23, 59), 10)
        _spend(client, hdr_app, "lg-ws1", "t-early", tpe(2026, 10, 6, 2, 4), 20)
        _spend(client, hdr_app, "lg-ws1", "t-late", tpe(2026, 10, 6, 23, 59), 30)
        _spend(client, hdr_app, "lg-ws1", "t-next", tpe(2026, 10, 7, 0, 30), 40)

        def ids(**params):
            r = client.get(
                "/api/v1/read/workspace/transactions", headers=hdr_web,
                params={"account_sync_id": "acc-card", **params},
            )
            assert r.status_code == 200, r.text
            return {item["id"] for item in r.json()["items"]}

        assert ids(day_from="2026-10-06", day_to="2026-10-06") == {"t-early", "t-late"}
        assert ids(day_from="2026-10-06") == {"t-early", "t-late", "t-next"}
        assert ids(day_to="2026-10-05") == {"t-before"}
        assert ids(day_from="2026-10-07", day_to="2026-10-07") == {"t-next"}
    finally:
        app.dependency_overrides.clear()


def test_workspace_ledger_counts_use_business_date(taipei, monkeypatch):
    """distinct_days / days_since_first_tx 以業務日期算:台灣 10/06 00:10 與
    10/06 23:50 是同一天(UTC 分屬 10/05、10/06),不能算成 2 天。"""
    client, _TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lg-ws2", "ws2@t.com")
        _spend(client, hdr_app, "lg-ws2", "c-1", tpe(2026, 10, 6, 0, 10), 10)
        _spend(client, hdr_app, "lg-ws2", "c-2", tpe(2026, 10, 6, 23, 50), 10)
        _spend(client, hdr_app, "lg-ws2", "c-3", tpe(2026, 10, 8, 9, 0), 10)

        monkeypatch.setattr(
            "src.routers.read.workspace.business_today", lambda *a, **k: date(2026, 10, 9),
        )
        r = client.get("/api/v1/read/workspace/ledger-counts", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tx_count"] == 3
        assert body["distinct_days"] == 2  # 10/06、10/08(UTC 日期會算成 3 天)
        assert body["days_since_first_tx"] == 4  # 10/06 → 10/09 含頭尾
    finally:
        app.dependency_overrides.clear()
