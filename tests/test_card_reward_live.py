"""信用卡回饋「即時連動的專屬回饋交易」(2026-10-16,`services/card_reward_live.py`)。

消費一寫入(App push / Web 寫入),同一次 commit 就把回饋交易建好/重算。舊排程
(`card_reward_payout`)只處理即時回饋上線前就已勾選的配對(`card_reward_legacy_links`)
——這裡用 `CARD_REWARD_LIVE_ENABLED=0` 先寫一批「部署前」的消費(關閉期間新勾的配對
會自動記成舊套用),再打開開關模擬部署後。
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import get_settings
from src.database import Base, get_db
from src.main import app
from src.models import (
    CardRewardLegacyLink,
    CardRewardPayout,
    Notification,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    User,
)
from src.services import card_reward_live, card_reward_payout, card_rewards


@pytest.fixture(autouse=True)
def _live_on(monkeypatch):
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "1")
    yield
    app.dependency_overrides.clear()


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


def _iso(dt):
    return dt.isoformat()


def _login(client, email, *, device_id, client_type):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post("/api/v1/auth/login", json={
        "email": email, "password": "Pa$$word1!", "device_id": device_id,
        "client_type": client_type, "device_name": "pytest", "platform": "test",
    })
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(datetime.now(timezone.utc)), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": "d-app", "changes": [body]})
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 1, r.text


def _seed(client, ledger_id, email, *, billing_day=None, payment_due_day=None):
    hdr_app = {"Authorization": f"Bearer {_login(client, email, device_id='d-app', client_type='app')}"}
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "帳本", "currency": "TWD"})
    card = {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "TWD"}
    if billing_day is not None:
        card["billingDay"] = billing_day
        card["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card", card)
    for acc_id, name in (("acc-wallet", "點數錢包"), ("acc-wallet2", "另一個錢包")):
        _push(client, hdr_app, ledger_id, "account", acc_id,
              {"syncId": acc_id, "name": name, "type": "cash", "currency": "TWD"})
    return hdr_app, hdr_web


def _latest_change_id(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr)
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _create_rule(client, hdr_web, ledger_id, **kwargs):
    payload = {"base_change_id": 0, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0}
    payload.update(kwargs)
    r = client.post(f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules",
                    headers=hdr_web, json=payload)
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _patch_rule(client, hdr_web, ledger_id, rule_id, **kwargs):
    base = _latest_change_id(client, hdr_web, ledger_id)
    r = client.patch(f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules/{rule_id}",
                     headers=hdr_web, json={"base_change_id": base, **kwargs})
    assert r.status_code == 200, r.text


def _monthly_rule(client, hdr_web, ledger_id, **kwargs):
    """自然月、整期結算、次月 5 號入帳到點數錢包。"""
    args = dict(
        interval="calendar_month", settlement_type="period_end",
        settlement_month_offset=1, settlement_day_of_month=5, reward_account_id="acc-wallet",
    )
    args.update(kwargs)
    return _create_rule(client, hdr_web, ledger_id, **args)


def _spend(client, hdr_app, ledger_id, sync_id, amount, when, rule_ids, **extra):
    payload = {
        "syncId": sync_id, "type": "expense", "amount": amount, "happenedAt": _iso(when),
        "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": rule_ids,
    }
    payload.update(extra)
    _push(client, hdr_app, ledger_id, "transaction", sync_id, payload)


def _delete(client, hdr_app, ledger_id, sync_id):
    _push(client, hdr_app, ledger_id, "transaction", sync_id, {"syncId": sync_id}, action="delete")


def _live_txs(TS, rule_id=None):
    with TS() as db:
        q = select(ReadTxProjection).where(ReadTxProjection.reward_payout_key.is_not(None))
        if rule_id is not None:
            q = q.where(ReadTxProjection.reward_payout_rule_sync_id == rule_id)
        rows = db.scalars(q.order_by(ReadTxProjection.happened_at.asc())).all()
        for row in rows:
            db.expunge(row)
        return rows


def _all_income(TS, account_id="acc-wallet"):
    with TS() as db:
        rows = db.scalars(select(ReadTxProjection).where(
            ReadTxProjection.tx_type == "income", ReadTxProjection.account_sync_id == account_id,
        )).all()
        for row in rows:
            db.expunge(row)
        return rows


def _payouts(TS, rule_id):
    with TS() as db:
        rows = db.scalars(select(CardRewardPayout).where(CardRewardPayout.rule_sync_id == rule_id)).all()
        for row in rows:
            db.expunge(row)
        return rows


def _next_month_day(d: date, day: int, months: int = 1) -> date:
    total = d.month - 1 + months
    year, month = d.year + total // 12, total % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _this_month_noon(now: datetime) -> datetime:
    """本月某天中午(UTC),保證還在本月、入帳日(次月 5 號)在未來。"""
    day = min(now.day, 28)
    return datetime(now.year, now.month, day, 4, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 整期模式                                                                      #
# --------------------------------------------------------------------------- #


def test_period_reward_appears_immediately_and_tracks_spend():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv1", "lv1@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv1")
    now = datetime.now(timezone.utc)
    when = _this_month_noon(now)

    _spend(client, hdr_app, "lv1", "s1", 500.0, when, [rule_id])
    live = _live_txs(TS, rule_id)
    assert len(live) == 1
    reward = live[0]
    assert reward.amount == 50.0
    assert reward.tx_type == "income"
    assert reward.account_sync_id == "acc-wallet"
    assert reward.category_name == card_rewards.REWARD_CATEGORY_NAME
    expected_day = _next_month_day(when.date(), 5)
    assert reward.happened_at.date() == expected_day
    assert reward.reward_payout_key.startswith(f"live:{date(when.year, when.month, 1).isoformat()}~")
    assert reward.note.startswith("信用卡回饋：測試規則（")

    # 再一筆:同一筆回饋交易,金額增加。
    _spend(client, hdr_app, "lv1", "s2", 300.0, when + timedelta(hours=1), [rule_id])
    live = _live_txs(TS, rule_id)
    assert [t.sync_id for t in live] == [reward.sync_id]
    assert live[0].amount == 80.0

    # 改金額 → 重算;刪一筆 → 減少。
    _spend(client, hdr_app, "lv1", "s2", 1000.0, when + timedelta(hours=1), [rule_id])
    assert _live_txs(TS, rule_id)[0].amount == 150.0
    _delete(client, hdr_app, "lv1", "s1")
    assert _live_txs(TS, rule_id)[0].amount == 100.0

    # 取消勾選最後一筆 → 這期沒有消費,回饋交易刪除。
    _spend(client, hdr_app, "lv1", "s2", 1000.0, when + timedelta(hours=1), [])
    assert _live_txs(TS, rule_id) == []
    assert _payouts(TS, rule_id) == []

    # 舊排程跑到入帳日之後也不會再發(沒有舊套用配對)。
    with TS() as db:
        card_reward_payout.materialize_due_card_reward_payouts(
            db, now=datetime.combine(expected_day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1),
        )
        db.commit()
    assert _all_income(TS) == []


def test_period_threshold_crossing_creates_then_removes():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv2", "lv2@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv2", min_spend_threshold=1000.0)
    when = _this_month_noon(datetime.now(timezone.utc))

    _spend(client, hdr_app, "lv2", "a", 600.0, when, [rule_id])
    assert _live_txs(TS, rule_id) == []  # 未達門檻,不放 0 元佔位
    _spend(client, hdr_app, "lv2", "b", 600.0, when, [rule_id])
    assert [t.amount for t in _live_txs(TS, rule_id)] == [120.0]
    _delete(client, hdr_app, "lv2", "b")
    assert _live_txs(TS, rule_id) == []


def test_period_shared_cap_pools_rules():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv3", "lv3@t.com")
    r1 = _monthly_rule(client, hdr_web, "lv3", label="網購", cap_amount=100.0, cap_shared_key="ubear")
    r2 = _monthly_rule(client, hdr_web, "lv3", label="一般", cap_amount=100.0, cap_shared_key="ubear")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv3", "a", 800.0, when, [r1])
    assert [t.amount for t in _live_txs(TS, r1)] == [80.0]
    _spend(client, hdr_app, "lv3", "b", 800.0, when, [r2])
    a1 = _live_txs(TS, r1)[0].amount
    a2 = _live_txs(TS, r2)[0].amount
    assert round(a1 + a2, 2) == 100.0  # 共用上限,兩條一起重算
    assert a1 == 50.0 and a2 == 50.0


def test_partial_refund_reduces_live_reward_without_reversal_tx():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv4", "lv4@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv4")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv4", "s1", 1000.0, when, [rule_id])
    assert _live_txs(TS, rule_id)[0].amount == 100.0
    _push(client, hdr_app, "lv4", "transaction", "rf1", {
        "syncId": "rf1", "type": "income", "amount": 400.0, "happenedAt": _iso(when + timedelta(hours=2)),
        "accountId": "acc-card", "refundOfId": "s1",
    })
    assert _live_txs(TS, rule_id)[0].amount == 60.0
    with TS() as db:
        reversals = db.scalars(select(ReadTxProjection).where(
            ReadTxProjection.tx_type == "expense", ReadTxProjection.account_sync_id == "acc-wallet",
        )).all()
    assert reversals == []


def test_manual_date_and_account_edits_survive_recompute():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv5", "lv5@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv5")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv5", "s1", 500.0, when, [rule_id])
    reward = _live_txs(TS, rule_id)[0]

    edited_day = reward.happened_at.replace(tzinfo=timezone.utc) + timedelta(days=2)
    _push(client, hdr_app, "lv5", "transaction", reward.sync_id, {
        "syncId": reward.sync_id, "type": "income", "amount": reward.amount,
        "happenedAt": _iso(edited_day), "accountId": "acc-wallet2", "accountName": "另一個錢包",
        "note": "我自己的備註",
    })
    _spend(client, hdr_app, "lv5", "s2", 500.0, when, [rule_id])
    after = _live_txs(TS, rule_id)[0]
    assert after.sync_id == reward.sync_id
    assert after.amount == 100.0  # 金額一律系統算
    assert after.account_sync_id == "acc-wallet2"  # 手動改的帳戶保留
    assert after.happened_at.replace(tzinfo=timezone.utc) == edited_day  # 手動改的日期保留
    assert after.note == "我自己的備註"
    assert after.reward_payout_rule_sync_id == rule_id  # 舊版 App 推回來時連結不能掉


def test_deleting_live_reward_tx_dismisses_it_for_good():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv6", "lv6@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv6")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv6", "s1", 500.0, when, [rule_id])
    reward = _live_txs(TS, rule_id)[0]
    _delete(client, hdr_app, "lv6", reward.sync_id)
    payout = _payouts(TS, rule_id)[0]
    assert payout.dismissed is True
    _spend(client, hdr_app, "lv6", "s2", 500.0, when, [rule_id])
    assert _live_txs(TS, rule_id) == []


def test_rule_edit_moves_unposted_reward_and_updates_amount():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv7", "lv7@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv7")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv7", "s1", 500.0, when, [rule_id])
    _patch_rule(client, hdr_web, "lv7", rule_id, rate_value=20.0, settlement_day_of_month=10)
    reward = _live_txs(TS, rule_id)[0]
    assert reward.amount == 100.0
    assert reward.happened_at.date() == _next_month_day(when.date(), 10)

    # 停用規則 → 未入帳的即時回饋刪除。
    _patch_rule(client, hdr_web, "lv7", rule_id, enabled=False)
    assert _live_txs(TS, rule_id) == []


def test_posted_period_reward_is_frozen():
    """消費補記在入帳日已過的期別:建一次(日期在過去 = 已入帳),之後再補勾/改規則都不動。
    入帳後每小時的保險排程發一次 card_reward 通知。"""
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv8", "lv8@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv8")
    now = datetime.now(timezone.utc)
    two_months_ago = datetime(now.year, now.month, 1, 4, tzinfo=timezone.utc) - timedelta(days=40)
    _spend(client, hdr_app, "lv8", "old", 500.0, two_months_ago, [rule_id])
    reward = _live_txs(TS, rule_id)[0]
    assert reward.amount == 50.0
    assert reward.happened_at.date() <= now.date()

    _spend(client, hdr_app, "lv8", "old2", 500.0, two_months_ago, [rule_id])
    _patch_rule(client, hdr_web, "lv8", rule_id, rate_value=30.0)
    assert _live_txs(TS, rule_id)[0].amount == 50.0

    with TS() as db:
        result = card_reward_live.reconcile_live_card_rewards(db)
        db.commit()
        result2 = card_reward_live.reconcile_live_card_rewards(db)
        db.commit()
        notes = db.scalars(select(Notification).where(Notification.category == "card_reward")).all()
    assert result["notified"] == 1 and result2["notified"] == 0
    assert len(notes) == 1
    assert notes[0].payload_json["txId"] == reward.sync_id


# --------------------------------------------------------------------------- #
# 逐筆模式                                                                      #
# --------------------------------------------------------------------------- #


def test_per_tx_three_rules_give_three_independent_rewards():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv9", "lv9@t.com")
    rules = [
        _create_rule(client, hdr_web, "lv9", label=f"逐筆{i}", rate_value=float(i),
                     settlement_type="immediate_after_tx", settlement_days=30, reward_account_id="acc-wallet")
        for i in (1, 2, 3)
    ]
    when = datetime.now(timezone.utc) - timedelta(days=1)
    _spend(client, hdr_app, "lv9", "s1", 1000.0, when, rules)
    live = _live_txs(TS)
    assert sorted(t.amount for t in live) == [10.0, 20.0, 30.0]
    assert {t.reward_source_tx_sync_id for t in live} == {"s1"}
    assert {t.reward_payout_key for t in live} == {"s1"}
    assert all(t.note.startswith("信用卡回饋：逐筆") for t in live)
    assert all(t.happened_at.date() == (when + timedelta(days=30)).date() for t in live)

    # 改金額 → 三筆都重算;取消一條 → 只刪那一筆。
    _spend(client, hdr_app, "lv9", "s1", 2000.0, when, rules)
    assert sorted(t.amount for t in _live_txs(TS)) == [20.0, 40.0, 60.0]
    _spend(client, hdr_app, "lv9", "s1", 2000.0, when, rules[:2])
    assert sorted(t.amount for t in _live_txs(TS)) == [20.0, 40.0]
    assert _live_txs(TS, rules[2]) == []

    # 第二筆消費各自獨立,不合併。
    _spend(client, hdr_app, "lv9", "s2", 100.0, when, rules[:1])
    assert sorted(t.amount for t in _live_txs(TS, rules[0])) == [1.0, 20.0]

    # 刪消費 → 它的回饋全部刪除。
    _delete(client, hdr_app, "lv9", "s1")
    assert [t.reward_source_tx_sync_id for t in _live_txs(TS)] == ["s2"]


def test_per_tx_cap_allocates_in_date_order():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv10", "lv10@t.com")
    rule_id = _create_rule(client, hdr_web, "lv10", interval="calendar_month", cap_amount=150.0,
                           settlement_type="immediate_after_tx", settlement_days=40,
                           reward_account_id="acc-wallet")
    base = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv10", "a", 1000.0, base, [rule_id])
    _spend(client, hdr_app, "lv10", "b", 1000.0, base + timedelta(minutes=5), [rule_id])
    by_src = {t.reward_source_tx_sync_id: t.amount for t in _live_txs(TS, rule_id)}
    assert by_src == {"a": 100.0, "b": 50.0}
    _delete(client, hdr_app, "lv10", "a")
    assert {t.reward_source_tx_sync_id: t.amount for t in _live_txs(TS, rule_id)} == {"b": 100.0}


def test_per_tx_posted_reward_ignores_rule_edit_but_follows_its_own_source():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv11", "lv11@t.com")
    rule_id = _create_rule(client, hdr_web, "lv11", settlement_type="immediate_after_tx", settlement_days=0,
                           reward_account_id="acc-wallet")
    when = datetime.now(timezone.utc) - timedelta(days=2)
    _spend(client, hdr_app, "lv11", "s1", 500.0, when, [rule_id])
    assert _live_txs(TS, rule_id)[0].amount == 50.0
    _patch_rule(client, hdr_web, "lv11", rule_id, rate_value=20.0)
    assert _live_txs(TS, rule_id)[0].amount == 50.0  # 已入帳:改規則不動
    _spend(client, hdr_app, "lv11", "s1", 800.0, when, [rule_id])
    assert _live_txs(TS, rule_id)[0].amount == 160.0  # 自己的消費改了照樣重算


# --------------------------------------------------------------------------- #
# 新舊以交易為切點                                                              #
# --------------------------------------------------------------------------- #


def test_legacy_and_live_spend_in_same_period_never_double_count(monkeypatch):
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv12", "lv12@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv12")
    when = _this_month_noon(datetime.now(timezone.utc))

    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "0")  # 部署前
    _spend(client, hdr_app, "lv12", "old", 300.0, when, [rule_id])
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "1")  # 部署後
    assert _live_txs(TS) == []
    with TS() as db:
        assert db.scalar(select(func.count()).select_from(CardRewardLegacyLink)) == 1

    _spend(client, hdr_app, "lv12", "new", 500.0, when, [rule_id])
    live = _live_txs(TS, rule_id)
    assert [t.amount for t in live] == [50.0]  # 只含新消費的部分

    settle = _next_month_day(when.date(), 5)
    with TS() as db:
        card_reward_payout.materialize_due_card_reward_payouts(
            db, now=datetime.combine(settle, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1),
        )
        db.commit()
    incomes = sorted(t.amount for t in _all_income(TS))
    assert incomes == [30.0, 50.0]  # 舊排程發舊的 30,即時交易 50,合計 = 整期 80


def test_mixed_period_threshold_only_met_with_new_spend(monkeypatch):
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv13", "lv13@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv13", min_spend_threshold=1000.0)
    when = _this_month_noon(datetime.now(timezone.utc))
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "0")
    _spend(client, hdr_app, "lv13", "old", 600.0, when, [rule_id])
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "1")
    _spend(client, hdr_app, "lv13", "new", 600.0, when, [rule_id])
    assert [t.amount for t in _live_txs(TS, rule_id)] == [120.0]  # 整期回饋全部算在新交易
    settle = _next_month_day(when.date(), 5)
    with TS() as db:
        card_reward_payout.materialize_due_card_reward_payouts(
            db, now=datetime.combine(settle, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1),
        )
        db.commit()
    assert sorted(t.amount for t in _all_income(TS)) == [120.0]


def test_legacy_untick_then_retick_becomes_live_and_extra_rule_is_live(monkeypatch):
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv14", "lv14@t.com")
    per_tx = dict(settlement_type="immediate_after_tx", settlement_days=30, reward_account_id="acc-wallet")
    r_old = _create_rule(client, hdr_web, "lv14", label="舊", **per_tx)
    r_new = _create_rule(client, hdr_web, "lv14", label="新", rate_value=5.0, **per_tx)
    when = datetime.now(timezone.utc) - timedelta(days=1)

    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "0")
    _spend(client, hdr_app, "lv14", "s1", 1000.0, when, [r_old])
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "1")

    # 舊消費多勾一條新規則:只有新勾的那條走即時機制。
    _spend(client, hdr_app, "lv14", "s1", 1000.0, when, [r_old, r_new])
    assert _live_txs(TS, r_old) == []
    assert [t.amount for t in _live_txs(TS, r_new)] == [50.0]
    # 舊套用的配對改金額:仍由舊排程處理(不產生即時交易)。
    _spend(client, hdr_app, "lv14", "s1", 2000.0, when, [r_old, r_new])
    assert _live_txs(TS, r_old) == []
    assert [t.amount for t in _live_txs(TS, r_new)] == [100.0]

    # 取消勾選舊規則 → legacy 列刪除;再勾回來 → 新套用。
    _spend(client, hdr_app, "lv14", "s1", 2000.0, when, [r_new])
    with TS() as db:
        assert db.scalar(select(func.count()).select_from(CardRewardLegacyLink)) == 0
    _spend(client, hdr_app, "lv14", "s1", 2000.0, when, [r_old, r_new])
    assert [t.amount for t in _live_txs(TS, r_old)] == [200.0]


def test_snapshot_legacy_links_matches_tagged_pairs(monkeypatch):
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv15", "lv15@t.com")
    monkeypatch.setenv("CARD_REWARD_LIVE_ENABLED", "0")
    r1 = _monthly_rule(client, hdr_web, "lv15")
    r2 = _monthly_rule(client, hdr_web, "lv15")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv15", "a", 100.0, when, [r1, r2])
    _spend(client, hdr_app, "lv15", "b", 100.0, when, [r1])
    with TS() as db:
        db.query(CardRewardLegacyLink).delete()
        db.commit()
        assert card_reward_live.snapshot_legacy_links(db) == 3
        assert card_reward_live.snapshot_legacy_links(db) == 0  # 冪等
        db.commit()


# --------------------------------------------------------------------------- #
# Web 寫入 / 邊界                                                               #
# --------------------------------------------------------------------------- #


def test_web_write_create_and_delete_trigger_live_reward():
    client, TS = _make_client()
    _hdr_app, hdr_web = _seed(client, "lv16", "lv16@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv16")
    when = _this_month_noon(datetime.now(timezone.utc))
    r = client.post("/api/v1/write/ledgers/lv16/transactions", headers=hdr_web, json={
        "base_change_id": _latest_change_id(client, hdr_web, "lv16"),
        "tx_type": "expense", "amount": 700.0, "happened_at": _iso(when),
        "account_id": "acc-card", "account_name": "信用卡", "reward_rule_ids": [rule_id],
    })
    assert r.status_code == 200, r.text
    tx_id = r.json()["entity_id"]
    live = _live_txs(TS, rule_id)
    assert [t.amount for t in live] == [70.0]

    # 讀取 API 帶出連結欄位(Web 交易詳情用)。
    lst = client.get("/api/v1/read/ledgers/lv16/transactions", headers=hdr_web).json()
    row = next(i for i in lst if i["id"] == live[0].sync_id)
    assert row["reward_payout_rule_id"] == rule_id
    assert row["reward_payout_key"] == live[0].reward_payout_key

    r = client.request("DELETE", f"/api/v1/write/ledgers/lv16/transactions/{tx_id}", headers=hdr_web,
                       json={"base_change_id": _latest_change_id(client, hdr_web, "lv16")})
    assert r.status_code == 200, r.text
    assert _live_txs(TS, rule_id) == []


def test_billing_cycle_closing_day_spend_counts_in_one_cycle_only():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv17", "lv17@t.com", billing_day=5, payment_due_day=25)
    rule_id = _create_rule(client, hdr_web, "lv17", interval="billing_cycle", settlement_type="period_end",
                           reward_account_id="acc-wallet")
    _spend(client, hdr_app, "lv17", "closing", 100.0, datetime(2026, 9, 5, 10, tzinfo=timezone.utc), [rule_id])
    with TS() as db:
        rule = db.scalar(select(ReadCardRewardRuleProjection).where(ReadCardRewardRuleProjection.sync_id == rule_id))
        ledger_id = db.scalar(select(ReadTxProjection.ledger_id).where(ReadTxProjection.sync_id == "closing"))
        first = card_rewards._qualifying_transactions(
            db, ledger_id=ledger_id, rule=rule, period_start=date(2026, 8, 5), period_end=date(2026, 9, 5))
        second = card_rewards._qualifying_transactions(
            db, ledger_id=ledger_id, rule=rule, period_start=date(2026, 9, 5), period_end=date(2026, 10, 5))
    assert [i["tx"].sync_id for i in first] == ["closing"]
    assert second == []


@pytest.fixture
def taipei(monkeypatch):
    monkeypatch.setenv("LEDGER_TIMEZONE", "Asia/Taipei")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def test_taipei_month_boundary_spend_lands_in_right_period(taipei):
    """台灣 00:00~08:00 與 23:00~24:00 的消費要歸到台灣日期所在的月份。"""
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv18", "lv18@t.com")
    rule_id = _monthly_rule(client, hdr_web, "lv18")
    tpe = ZoneInfo("Asia/Taipei")
    today = datetime.now(tpe).date()
    m_start = _next_month_day(today, 1, months=2)  # 兩個月後的 1 號(入帳日一定在未來)
    early = datetime(m_start.year, m_start.month, 1, 0, 30, tzinfo=tpe)  # = 前一天 16:30Z
    prev_last = m_start - timedelta(days=1)
    late = datetime(prev_last.year, prev_last.month, prev_last.day, 23, 30, tzinfo=tpe)
    _spend(client, hdr_app, "lv18", "early", 1000.0, early.astimezone(timezone.utc), [rule_id])
    _spend(client, hdr_app, "lv18", "late", 500.0, late.astimezone(timezone.utc), [rule_id])
    by_period = {t.reward_payout_key.split(":")[1].split("~")[0]: t.amount for t in _live_txs(TS, rule_id)}
    assert by_period == {
        m_start.isoformat(): 100.0,
        date(prev_last.year, prev_last.month, 1).isoformat(): 50.0,
    }
    # 入帳日 = 次月 5 號(台灣時間 00:00)
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == "lv18@t.com"))
    early_reward = next(t for t in _live_txs(TS, rule_id) if t.amount == 100.0)
    assert early_reward.happened_at.replace(tzinfo=timezone.utc).astimezone(tpe).date() == _next_month_day(m_start, 5)
    assert user_id


def test_read_apis_expose_live_reward_links():
    client, TS = _make_client()
    hdr_app, hdr_web = _seed(client, "lv19", "lv19@t.com")
    period_rule = _monthly_rule(client, hdr_web, "lv19")
    per_tx_rule = _create_rule(client, hdr_web, "lv19", interval="calendar_month",
                               settlement_type="immediate_after_tx",
                               settlement_days=30, reward_account_id="acc-wallet")
    when = _this_month_noon(datetime.now(timezone.utc))
    _spend(client, hdr_app, "lv19", "s1", 500.0, when, [period_rule, per_tx_rule])

    # 規則明細:這一期帶出連動的即時回饋交易。
    r = client.get(
        f"/api/v1/read/ledgers/lv19/accounts/acc-card/card-reward-rules/{period_rule}/transactions",
        headers=hdr_web,
    )
    assert r.status_code == 200, r.text
    period = next(p for p in r.json()["periods"] if p["items"])
    live = _live_txs(TS, period_rule)[0]
    assert period["live_reward_tx_id"] == live.sync_id
    assert period["live_reward_amount"] == 50.0
    assert period["live_reward_posted"] is False

    # 逐筆規則:明細的入帳日 = 實際回饋交易日期,payout_tx_id 指向它。
    r = client.get(
        f"/api/v1/read/ledgers/lv19/accounts/acc-card/card-reward-rules/{per_tx_rule}/transactions",
        headers=hdr_web,
    )
    item = next(i for p in r.json()["periods"] for i in p["items"] if i["tx_id"] == "s1")
    per_tx_live = _live_txs(TS, per_tx_rule)[0]
    assert item["payout_tx_id"] == per_tx_live.sync_id
    assert item["settlement_date"].startswith((when + timedelta(days=30)).date().isoformat())

    # 交易列表依來源消費篩選(Web 消費詳情列出逐筆回饋)。
    r = client.get("/api/v1/read/workspace/transactions", headers=hdr_web,
                   params={"reward_source_tx_id": "s1"})
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [per_tx_live.sync_id]
