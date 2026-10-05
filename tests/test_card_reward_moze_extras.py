"""信用卡紅利回饋三項新能力(2026-10,對齊 Moze https://doc.moze.app/credit-card/rewards,
docs/MOZE_FEATURE_GAP_SD.md §2.9.5):

1. `interval == "custom_range"`(指定活動區間):沿用規則既有 starts_at/ends_at 當活動
   起訖日,兩者皆必填(REST 422),計算期間固定單一期、忽略 period_offset/帳單日;
   `period_end` 自動入帳要等活動結束才結算、只入帳一次(lookback 迴圈不重複入帳)。
2. `is_basic`(基本回饋旗標):REST 與 sync push 往返,非鎖定欄位。
3. `reward_project_id`(回饋金歸屬專案):REST 驗證專案存在(422)、sync push 不驗證;
   自動入帳交易的 `projectId` 解析(明確專案 / 沿用來源消費專案 / 整期彙總不帶 /
   專案已刪降級)與退款沖銷同專案。
4. alembic 0065 升級(SQLite)。

測試寫法比照 tests/test_card_rewards.py / test_card_reward_payout.py。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    CardRewardPayout,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    User,
)
from src.services import card_reward_payout, card_rewards

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


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


def _utc(y, m, d, hour=12):
    return datetime(y, m, d, hour, tzinfo=timezone.utc)


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d-app", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _seed(client, ledger_id, email, *, billing_day=None, payment_due_day=None):
    """回傳 (hdr_app, hdr_web)。帳本 + 信用卡 acc-card + 回饋入帳錢包 acc-wallet。"""
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"})
    card = {"syncId": "acc-card", "name": "信用卡", "type": "credit_card", "currency": "CNY"}
    if billing_day is not None:
        card["billingDay"] = billing_day
    if payment_due_day is not None:
        card["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card", card)
    _push(client, hdr_app, ledger_id, "account", "acc-wallet",
          {"syncId": "acc-wallet", "name": "點數錢包", "type": "cash", "currency": "CNY"})
    return hdr_app, hdr_web


def _rules_url(ledger_id):
    return f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules"


def _create_rule(client, hdr_web, ledger_id, *, expect=200, **kwargs):
    payload = {"base_change_id": 0, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0}
    payload.update(kwargs)
    r = client.post(_rules_url(ledger_id), headers=hdr_web, json=payload)
    assert r.status_code == expect, r.text
    return r.json()["entity_id"] if expect == 200 else r


def _patch_rule(client, hdr_web, ledger_id, rule_id, **fields):
    base = int(client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr_web).json()["source_change_id"])
    return client.patch(
        f"{_rules_url(ledger_id)}/{rule_id}", headers=hdr_web, json={"base_change_id": base, **fields},
    )


def _list_rules(client, hdr_web, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/accounts/acc-card/card-reward-rules", headers=hdr_web)
    assert r.status_code == 200, r.text
    return r.json()


def _rule_row(TS, email, sync_id):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        row = db.scalar(
            select(ReadCardRewardRuleProjection).where(
                ReadCardRewardRuleProjection.user_id == user_id,
                ReadCardRewardRuleProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def _spend(client, hdr_app, ledger_id, tx_id, when, amount, rule_id, *, project_id=None):
    payload = {
        "syncId": tx_id, "type": "expense", "amount": amount, "happenedAt": _iso(when),
        "accountId": "acc-card", "accountName": "信用卡", "rewardRuleIds": [rule_id],
    }
    if project_id is not None:
        payload["projectId"] = project_id
    _push(client, hdr_app, ledger_id, "transaction", tx_id, payload)


def _txs(TS, account_id, tx_type):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.account_sync_id == account_id,
                ReadTxProjection.tx_type == tx_type,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _payouts(TS, email, rule_id):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(CardRewardPayout).where(
                CardRewardPayout.user_id == user_id, CardRewardPayout.rule_sync_id == rule_id,
            ).order_by(CardRewardPayout.id.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _tick(TS, now):
    with TS() as db:
        result = card_reward_payout.materialize_due_card_reward_payouts(db, now=now)
        db.commit()
    return result


def _project(client, hdr_app, ledger_id, sync_id, name="專案"):
    _push(client, hdr_app, ledger_id, "project", sync_id, {"syncId": sync_id, "name": name})


# 固定的過去活動區間(測試不依賴「今天」):3/10 ~ 3/20(含)。
CAMPAIGN = {"starts_at": "2026-03-10T00:00:00+00:00", "ends_at": "2026-03-20T00:00:00+00:00"}


# --------------------------------------------------------------------------- #
# 1. custom_range:期間計算                                                    #
# --------------------------------------------------------------------------- #


def test_custom_range_period_is_fixed_and_ignores_offset_and_billing_day():
    rule = SimpleNamespace(
        interval="custom_range",
        starts_at=_utc(2026, 3, 10, 0), ends_at=_utc(2026, 3, 20, 0),
    )
    # custom_range 不碰 db/account(不需要帳單週期),傳 None 也能算。
    for offset in (0, -1, -7, 1):
        assert card_rewards._resolve_period(
            None, account=None, rule=rule, now=date(2026, 10, 6), period_offset=offset,
        ) == (date(2026, 3, 10), date(2026, 3, 20))
        assert card_rewards._resolve_periods(
            None, account=None, rule=rule, now=date(2026, 10, 6), period_offset=offset,
        ) == [(date(2026, 3, 10), date(2026, 3, 20))]


def test_custom_range_incomplete_or_reversed_dates_degrade_instead_of_crashing():
    """sync push 不驗證,資料不完整時要降級成「沒有期間」,不能炸。"""
    for starts, ends in (
        (None, _utc(2026, 3, 20)), (_utc(2026, 3, 10), None), (None, None),
        (_utc(2026, 3, 20), _utc(2026, 3, 10)),
    ):
        rule = SimpleNamespace(interval="custom_range", starts_at=starts, ends_at=ends)
        assert card_rewards._resolve_period(
            None, account=None, rule=rule, now=date(2026, 10, 6), period_offset=0,
        ) is None
        assert card_rewards._resolve_periods(
            None, account=None, rule=rule, now=date(2026, 10, 6), period_offset=0,
        ) == []


# --------------------------------------------------------------------------- #
# 2. custom_range:REST 驗證 / 往返                                            #
# --------------------------------------------------------------------------- #


def test_custom_range_create_requires_both_dates_and_orders_them():
    client, _TS = _make_client()
    try:
        _hdr_app, hdr_web = _seed(client, "lgx1", "x1@t.com")
        # 缺兩者 / 缺一 → 422
        _create_rule(client, hdr_web, "lgx1", expect=422, interval="custom_range")
        _create_rule(client, hdr_web, "lgx1", expect=422, interval="custom_range",
                     starts_at=CAMPAIGN["starts_at"])
        _create_rule(client, hdr_web, "lgx1", expect=422, interval="custom_range",
                     ends_at=CAMPAIGN["ends_at"])
        # ends 早於 starts → 422
        _create_rule(client, hdr_web, "lgx1", expect=422, interval="custom_range",
                     starts_at=CAMPAIGN["ends_at"], ends_at=CAMPAIGN["starts_at"])
        # 非 custom_range 不受影響(日期仍選填)
        _create_rule(client, hdr_web, "lgx1", interval="calendar_month")
        # 合法 → 200,GET 往返
        rule_id = _create_rule(client, hdr_web, "lgx1", interval="custom_range", **CAMPAIGN)
        item = next(i for i in _list_rules(client, hdr_web, "lgx1") if i["id"] == rule_id)
        assert item["interval"] == "custom_range"
        assert datetime.fromisoformat(item["starts_at"]).date() == date(2026, 3, 10)
        assert datetime.fromisoformat(item["ends_at"]).date() == date(2026, 3, 20)
    finally:
        app.dependency_overrides.clear()


def test_custom_range_patch_validates_merged_final_state():
    client, _TS = _make_client()
    try:
        _hdr_app, hdr_web = _seed(client, "lgx2", "x2@t.com")
        plain = _create_rule(client, hdr_web, "lgx2")  # billing_cycle,沒日期
        # 只改 interval、沒有日期 → 合併後缺日期 → 422
        assert _patch_rule(client, hdr_web, "lgx2", plain, interval="custom_range").status_code == 422
        # interval + 日期一起給 → 200
        ok = _patch_rule(client, hdr_web, "lgx2", plain, interval="custom_range", **CAMPAIGN)
        assert ok.status_code == 200, ok.text
        # 清掉 ends_at → 最終狀態缺日期 → 422
        assert _patch_rule(client, hdr_web, "lgx2", plain, ends_at=None).status_code == 422
        # ends 早於既有 starts → 422
        assert _patch_rule(
            client, hdr_web, "lgx2", plain, ends_at="2026-03-01T00:00:00+00:00",
        ).status_code == 422
        # 沒動 interval/日期的 PATCH(例如改名)不受影響
        assert _patch_rule(client, hdr_web, "lgx2", plain, label="改名").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_custom_range_dates_locked_once_payout_exists():
    client, TS = _make_client()
    try:
        email = "x3@t.com"
        hdr_app, hdr_web = _seed(client, "lgx3", email)
        rule_id = _create_rule(
            client, hdr_web, "lgx3", interval="custom_range", **CAMPAIGN,
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        # 還沒入帳:日期可改(延長活動)
        assert _patch_rule(client, hdr_web, "lgx3", rule_id,
                           ends_at="2026-03-25T00:00:00+00:00").status_code == 200
        _spend(client, hdr_app, "lgx3", "tx-1", _utc(2026, 3, 12), 100.0, rule_id)
        _tick(TS, _utc(2026, 4, 1))
        assert len(_payouts(TS, email, rule_id)) == 1
        # 已入帳:改起訖日 → 422(去重鍵會錯位);但 is_basic/label 仍可改
        assert _patch_rule(client, hdr_web, "lgx3", rule_id,
                           ends_at="2026-03-30T00:00:00+00:00").status_code == 422
        assert _patch_rule(client, hdr_web, "lgx3", rule_id, is_basic=True, label="x").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_custom_range_sync_push_accepts_incomplete_rule_without_validation():
    """generic push 不驗證(與其它欄位一致),且資料不完整時讀路徑不能炸。"""
    client, _TS = _make_client()
    try:
        hdr_app, hdr_web = _seed(client, "lgx4", "x4@t.com")
        _push(client, hdr_app, "lgx4", "card_reward_rule", "crr_bad", {
            "syncId": "crr_bad", "accountId": "acc-card", "label": "殘缺", "rateType": "percentage",
            "rateValue": 5.0, "interval": "custom_range", "enabled": True,
        })
        r = client.get("/api/v1/read/ledgers/lgx4/accounts/acc-card/card-rewards", headers=hdr_web)
        assert r.status_code == 200, r.text
        item = r.json()["items"][0]
        assert item["periods"][0]["status"] == "no_billing_schedule"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 3. custom_range:回饋金額 / 上限                                             #
# --------------------------------------------------------------------------- #


def test_custom_range_reward_amount_and_cap_use_only_the_campaign_window():
    client, _TS = _make_client()
    try:
        # 刻意不設 billing_day:custom_range 不需要帳單週期。
        hdr_app, hdr_web = _seed(client, "lgx5", "x5@t.com")
        rule_id = _create_rule(
            client, hdr_web, "lgx5", interval="custom_range", **CAMPAIGN, cap_amount=25.0,
        )
        _spend(client, hdr_app, "lgx5", "tx-before", _utc(2026, 3, 9), 1000.0, rule_id)  # 活動前
        _spend(client, hdr_app, "lgx5", "tx-in1", _utc(2026, 3, 10, 18), 100.0, rule_id)  # 起日當天
        _spend(client, hdr_app, "lgx5", "tx-in2", _utc(2026, 3, 20, 12), 200.0, rule_id)  # 迄日當天(含)
        _spend(client, hdr_app, "lgx5", "tx-after", _utc(2026, 3, 21), 1000.0, rule_id)   # 活動後

        for offset in (0, -1, -5):  # 任何 offset 都是同一期
            r = client.get(
                f"/api/v1/read/ledgers/lgx5/accounts/acc-card/card-rewards?period_offset={offset}",
                headers=hdr_web,
            )
            assert r.status_code == 200, r.text
            periods = r.json()["items"][0]["periods"]
            assert len(periods) == 1
            p = periods[0]
            assert p["status"] == "ok"
            assert datetime.fromisoformat(p["period_start"]).date() == date(2026, 3, 10)
            assert datetime.fromisoformat(p["period_end"]).date() == date(2026, 3, 20)
            assert p["qualifying_spend"] == 300.0
            assert p["raw_reward"] == 30.0
            assert p["capped_reward"] == 25.0  # cap_amount=25
            assert p["remaining_reward_room"] == 0.0

        detail = client.get(
            f"/api/v1/read/ledgers/lgx5/accounts/acc-card/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        )
        assert detail.status_code == 200, detail.text
        assert {i["tx_id"] for i in detail.json()["periods"][0]["items"]} == {"tx-in1", "tx-in2"}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 4. custom_range:自動入帳                                                    #
# --------------------------------------------------------------------------- #


def test_custom_range_period_end_pays_once_after_campaign_ends():
    client, TS = _make_client()
    try:
        email = "x6@t.com"
        hdr_app, hdr_web = _seed(client, "lgx6", email)
        rule_id = _create_rule(
            client, hdr_web, "lgx6", interval="custom_range", **CAMPAIGN,
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx6", "tx-1", _utc(2026, 3, 12), 300.0, rule_id)

        # 活動進行中 / 最後一天當天:不結算(最後一天還可能有後續消費)。
        assert _tick(TS, _utc(2026, 3, 15)) == {"tx_payouts": 0, "period_payouts": 0}
        assert _tick(TS, _utc(2026, 3, 20, 23)) == {"tx_payouts": 0, "period_payouts": 0}
        assert _txs(TS, "acc-wallet", "income") == []

        # 活動結束後第一天:結算一次,去重鍵 = ends_at 的 iso 日期。
        assert _tick(TS, _utc(2026, 3, 21)) == {"tx_payouts": 0, "period_payouts": 1}
        incomes = _txs(TS, "acc-wallet", "income")
        assert [t.amount for t in incomes] == [30.0]
        payouts = _payouts(TS, email, rule_id)
        assert [p.dedup_key for p in payouts] == ["2026-03-20"]

        # 之後無論跑幾次、離多久都不重複入帳。
        for later in (_utc(2026, 3, 21, 18), _utc(2026, 4, 30), _utc(2026, 10, 6)):
            assert _tick(TS, later) == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_txs(TS, "acc-wallet", "income")) == 1
        assert len(_payouts(TS, email, rule_id)) == 1
    finally:
        app.dependency_overrides.clear()


def test_custom_range_lookback_loop_does_not_double_pay_with_delayed_settlement():
    """settlement_month_offset=2 → `_materialize_period_end` 會往回看 3 個 offset,
    而 custom_range 每個 offset 都回同一期;靠 `already_paid` 即時更新集合擋重複。
    同一個 tick 內只能入帳一次;事後補綁消費只補差額一次。"""
    client, TS = _make_client()
    try:
        email = "x7@t.com"
        hdr_app, hdr_web = _seed(client, "lgx7", email)
        rule_id = _create_rule(
            client, hdr_web, "lgx7", interval="custom_range", **CAMPAIGN,
            settlement_type="period_end", settlement_month_offset=2, settlement_day_of_month=5,
            reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx7", "tx-1", _utc(2026, 3, 12), 300.0, rule_id)

        # 入帳日 = 3 月 + 2 = 5/5。之前不發。
        assert _tick(TS, _utc(2026, 5, 4)) == {"tx_payouts": 0, "period_payouts": 0}
        # 到期的單一 tick:3 個 offset 看到同一期,只能發一次。
        assert _tick(TS, _utc(2026, 5, 6)) == {"tx_payouts": 0, "period_payouts": 1}
        assert [t.amount for t in _txs(TS, "acc-wallet", "income")] == [30.0]
        assert len(_payouts(TS, email, rule_id)) == 1
        assert _tick(TS, _utc(2026, 5, 7)) == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_txs(TS, "acc-wallet", "income")) == 1

        # 事後補一筆活動期間內的合格消費 → 補發差額(100*10%=10),且只補一次。
        _spend(client, hdr_app, "lgx7", "tx-late", _utc(2026, 3, 15), 100.0, rule_id)
        assert _tick(TS, _utc(2026, 5, 8)) == {"tx_payouts": 0, "period_payouts": 1}
        assert sorted(t.amount for t in _txs(TS, "acc-wallet", "income")) == [10.0, 30.0]
        assert [p.dedup_key for p in _payouts(TS, email, rule_id)] == ["2026-03-20", "2026-03-20#2"]
        assert _tick(TS, _utc(2026, 5, 9)) == {"tx_payouts": 0, "period_payouts": 0}
        assert len(_txs(TS, "acc-wallet", "income")) == 2
    finally:
        app.dependency_overrides.clear()


def test_custom_range_per_tx_cap_tracks_the_whole_campaign_window():
    """逐筆結算的 cap 期間走 `_resolve_period`:整個活動區間共用一個上限。"""
    client, TS = _make_client()
    try:
        email = "x8@t.com"
        hdr_app, hdr_web = _seed(client, "lgx8", email)
        rule_id = _create_rule(
            client, hdr_web, "lgx8", interval="custom_range", **CAMPAIGN, cap_amount=15.0,
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx8", "tx-1", _utc(2026, 3, 11), 100.0, rule_id)  # 10
        _spend(client, hdr_app, "lgx8", "tx-2", _utc(2026, 3, 19), 100.0, rule_id)  # 10 → 夾到 5
        _spend(client, hdr_app, "lgx8", "tx-3", _utc(2026, 3, 20), 100.0, rule_id)  # 夾到 0(仍記去重)
        _spend(client, hdr_app, "lgx8", "tx-out", _utc(2026, 3, 25), 100.0, rule_id)  # 活動外,不合格
        assert _tick(TS, _utc(2026, 4, 1)) == {"tx_payouts": 3, "period_payouts": 0}
        assert [t.amount for t in _txs(TS, "acc-wallet", "income")] == [10.0, 5.0]
        assert sorted(p.dedup_key for p in _payouts(TS, email, rule_id)) == ["tx-1", "tx-2", "tx-3"]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 5. is_basic / reward_project_id:REST 與 sync push 往返                      #
# --------------------------------------------------------------------------- #


def test_is_basic_and_reward_project_round_trip_via_rest():
    client, TS = _make_client()
    try:
        email = "x9@t.com"
        hdr_app, hdr_web = _seed(client, "lgx9", email)
        _project(client, hdr_app, "lgx9", "proj_a")
        _project(client, hdr_app, "lgx9", "proj_b")

        default_rule = _create_rule(client, hdr_web, "lgx9")
        item = next(i for i in _list_rules(client, hdr_web, "lgx9") if i["id"] == default_rule)
        assert item["is_basic"] is False
        assert item["reward_project_id"] is None

        rule_id = _create_rule(client, hdr_web, "lgx9", is_basic=True, reward_project_id="proj_a")
        # 同一張卡允許多條 is_basic=true
        rule2 = _create_rule(client, hdr_web, "lgx9", is_basic=True)
        items = {i["id"]: i for i in _list_rules(client, hdr_web, "lgx9")}
        assert items[rule_id]["is_basic"] is True
        assert items[rule_id]["reward_project_id"] == "proj_a"
        assert items[rule2]["is_basic"] is True

        # PATCH:改專案、改旗標、清掉專案
        assert _patch_rule(client, hdr_web, "lgx9", rule_id, reward_project_id="proj_b").status_code == 200
        assert _rule_row(TS, email, rule_id).reward_project_id == "proj_b"
        assert _patch_rule(client, hdr_web, "lgx9", rule_id, is_basic=False).status_code == 200
        row = _rule_row(TS, email, rule_id)
        assert row.is_basic is False and row.reward_project_id == "proj_b"
        assert _patch_rule(client, hdr_web, "lgx9", rule_id, reward_project_id=None).status_code == 200
        assert _rule_row(TS, email, rule_id).reward_project_id is None

        # 專案不存在 → 422(create / patch 皆然)
        _create_rule(client, hdr_web, "lgx9", expect=422, reward_project_id="proj_missing")
        assert _patch_rule(client, hdr_web, "lgx9", rule_id,
                           reward_project_id="proj_missing").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_is_basic_and_reward_project_are_not_locked_after_rule_has_history():
    client, TS = _make_client()
    try:
        email = "x10@t.com"
        hdr_app, hdr_web = _seed(client, "lgx10", email)
        _project(client, hdr_app, "lgx10", "proj_a")
        rule_id = _create_rule(
            client, hdr_web, "lgx10",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx10", "tx-1", _utc(2026, 3, 12), 100.0, rule_id)
        _tick(TS, _utc(2026, 4, 1))
        item = next(i for i in _list_rules(client, hdr_web, "lgx10") if i["id"] == rule_id)
        assert item["locked"] is True
        # 鎖定欄位照舊 422,但這兩個新欄位可改。
        assert _patch_rule(client, hdr_web, "lgx10", rule_id, rate_value=20.0).status_code == 422
        ok = _patch_rule(client, hdr_web, "lgx10", rule_id, is_basic=True, reward_project_id="proj_a")
        assert ok.status_code == 200, ok.text
        row = _rule_row(TS, email, rule_id)
        assert row.is_basic is True and row.reward_project_id == "proj_a"
    finally:
        app.dependency_overrides.clear()


def test_sync_push_round_trip_and_partial_push_keeps_new_fields():
    client, TS = _make_client()
    try:
        email = "x11@t.com"
        hdr_app, hdr_web = _seed(client, "lgx11", email)
        base = {
            "syncId": "crr_p1", "accountId": "acc-card", "label": "App 規則", "rateType": "percentage",
            "rateValue": 5.0, "interval": "custom_range",
            "startsAt": CAMPAIGN["starts_at"], "endsAt": CAMPAIGN["ends_at"], "enabled": True,
        }
        # generic push 不驗證專案存在(proj_ghost 根本沒建),也能存。
        _push(client, hdr_app, "lgx11", "card_reward_rule", "crr_p1",
              {**base, "isBasic": True, "rewardProjectId": "proj_ghost"})
        row = _rule_row(TS, email, "crr_p1")
        assert row.interval == "custom_range"
        assert row.is_basic is True
        assert row.reward_project_id == "proj_ghost"

        # 舊版 App 的 partial push(不帶這兩鍵)不能把它們沖回預設。
        _push(client, hdr_app, "lgx11", "card_reward_rule", "crr_p1", {"syncId": "crr_p1", "label": "改名"})
        row = _rule_row(TS, email, "crr_p1")
        assert row.label == "改名"
        assert row.is_basic is True
        assert row.reward_project_id == "proj_ghost"

        # 顯式 false / null 照寫。
        _push(client, hdr_app, "lgx11", "card_reward_rule", "crr_p1",
              {"syncId": "crr_p1", "isBasic": False, "rewardProjectId": None})
        row = _rule_row(TS, email, "crr_p1")
        assert row.is_basic is False and row.reward_project_id is None

        # Web 端經 snapshot_builder → mutator → diff 一輪後,push 進來的值不能遺失。
        _push(client, hdr_app, "lgx11", "card_reward_rule", "crr_p1",
              {"syncId": "crr_p1", "isBasic": True})
        assert _patch_rule(client, hdr_web, "lgx11", "crr_p1", label="web 改名").status_code == 200
        row = _rule_row(TS, email, "crr_p1")
        assert row.label == "web 改名" and row.is_basic is True and row.interval == "custom_range"

        # Web 建立的規則,sync pull 看得到 camelCase 新欄位。
        _project(client, hdr_app, "lgx11", "proj_a")
        rule_id = _create_rule(client, hdr_web, "lgx11", is_basic=True, reward_project_id="proj_a")
        pull = client.get("/api/v1/sync/pull?since=0&limit=500", headers=hdr_app)
        assert pull.status_code == 200, pull.text
        changes = [c for c in pull.json()["changes"]
                   if c["entity_type"] == "card_reward_rule" and c["entity_sync_id"] == rule_id]
        assert changes, pull.text
        payload = changes[-1]["payload"]
        assert payload["isBasic"] is True
        assert payload["rewardProjectId"] == "proj_a"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 6. 入帳交易的專案                                                           #
# --------------------------------------------------------------------------- #


def test_per_tx_payout_uses_explicit_rule_project_over_source_project():
    client, TS = _make_client()
    try:
        email = "x12@t.com"
        hdr_app, hdr_web = _seed(client, "lgx12", email)
        _project(client, hdr_app, "lgx12", "proj_reward")
        _project(client, hdr_app, "lgx12", "proj_src")
        rule_id = _create_rule(
            client, hdr_web, "lgx12", reward_project_id="proj_reward",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx12", "tx-1", _utc(2026, 3, 12), 100.0, rule_id, project_id="proj_src")
        assert _tick(TS, _utc(2026, 4, 1))["tx_payouts"] == 1
        (income,) = _txs(TS, "acc-wallet", "income")
        assert income.project_sync_id == "proj_reward"
    finally:
        app.dependency_overrides.clear()


def test_per_tx_payout_inherits_source_tx_project_when_rule_has_none():
    client, TS = _make_client()
    try:
        email = "x13@t.com"
        hdr_app, hdr_web = _seed(client, "lgx13", email)
        _project(client, hdr_app, "lgx13", "proj_src")
        rule_id = _create_rule(
            client, hdr_web, "lgx13",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx13", "tx-1", _utc(2026, 3, 12), 100.0, rule_id, project_id="proj_src")
        _spend(client, hdr_app, "lgx13", "tx-2", _utc(2026, 3, 13), 100.0, rule_id)  # 來源沒專案
        assert _tick(TS, _utc(2026, 4, 1))["tx_payouts"] == 2
        incomes = _txs(TS, "acc-wallet", "income")
        assert [t.project_sync_id for t in incomes] == ["proj_src", None]
    finally:
        app.dependency_overrides.clear()


def test_period_end_payout_has_no_project_unless_rule_specifies_one():
    client, TS = _make_client()
    try:
        email = "x14@t.com"
        hdr_app, hdr_web = _seed(client, "lgx14", email)
        _project(client, hdr_app, "lgx14", "proj_src")
        _project(client, hdr_app, "lgx14", "proj_reward")
        no_proj = _create_rule(
            client, hdr_web, "lgx14", label="未指定", interval="custom_range", **CAMPAIGN,
            settlement_type="period_end", reward_account_id="acc-wallet",
        )
        with_proj = _create_rule(
            client, hdr_web, "lgx14", label="指定", interval="custom_range", **CAMPAIGN,
            settlement_type="period_end", reward_account_id="acc-wallet", reward_project_id="proj_reward",
        )
        _spend(client, hdr_app, "lgx14", "tx-1", _utc(2026, 3, 12), 100.0, no_proj, project_id="proj_src")
        _spend(client, hdr_app, "lgx14", "tx-2", _utc(2026, 3, 13), 200.0, with_proj, project_id="proj_src")
        assert _tick(TS, _utc(2026, 3, 21))["period_payouts"] == 2
        by_amount = {t.amount: t for t in _txs(TS, "acc-wallet", "income")}
        assert by_amount[10.0].project_sync_id is None            # 整期彙總:不沿用來源專案
        assert by_amount[20.0].project_sync_id == "proj_reward"   # 規則明確指定才帶
    finally:
        app.dependency_overrides.clear()


def test_payout_degrades_to_no_project_when_explicit_project_is_deleted():
    client, TS = _make_client()
    try:
        email = "x15@t.com"
        hdr_app, hdr_web = _seed(client, "lgx15", email)
        _project(client, hdr_app, "lgx15", "proj_gone")
        _project(client, hdr_app, "lgx15", "proj_src")
        rule_id = _create_rule(
            client, hdr_web, "lgx15", reward_project_id="proj_gone",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx15", "tx-1", _utc(2026, 3, 12), 100.0, rule_id, project_id="proj_src")
        _push(client, hdr_app, "lgx15", "project", "proj_gone", {}, action="delete")
        # 專案沒了:入帳不能失敗,降級為不帶專案(也不改掛來源專案)。
        assert _tick(TS, _utc(2026, 4, 1))["tx_payouts"] == 1
        (income,) = _txs(TS, "acc-wallet", "income")
        assert income.amount == 10.0
        assert income.project_sync_id is None
        assert len(_payouts(TS, email, rule_id)) == 1
    finally:
        app.dependency_overrides.clear()


def _refund(client, hdr_web, ledger_id, tx_id):
    base = int(client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr_web).json()["source_change_id"])
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr_web,
        json={
            "base_change_id": base, "tx_type": "income", "amount": 100.0,
            "happened_at": _iso(), "account_id": "acc-card", "refund_of_id": tx_id,
        },
    )
    assert r.status_code == 200, r.text


def test_refund_reversal_carries_same_project_as_reward_tx():
    client, TS = _make_client()
    try:
        email = "x16@t.com"
        hdr_app, hdr_web = _seed(client, "lgx16", email)
        _project(client, hdr_app, "lgx16", "proj_reward")
        _project(client, hdr_app, "lgx16", "proj_src")
        explicit = _create_rule(
            client, hdr_web, "lgx16", label="明確", reward_project_id="proj_reward",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        inherit = _create_rule(
            client, hdr_web, "lgx16", label="沿用",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx16", "tx-e", _utc(2026, 3, 12), 100.0, explicit, project_id="proj_src")
        _spend(client, hdr_app, "lgx16", "tx-i", _utc(2026, 3, 13), 100.0, inherit, project_id="proj_src")
        assert _tick(TS, _utc(2026, 4, 1))["tx_payouts"] == 2

        _refund(client, hdr_web, "lgx16", "tx-e")
        _refund(client, hdr_web, "lgx16", "tx-i")
        reversals = {t.reward_source_tx_sync_id: t for t in _txs(TS, "acc-wallet", "expense")}
        assert set(reversals) == {"tx-e", "tx-i"}
        assert reversals["tx-e"].project_sync_id == "proj_reward"
        assert reversals["tx-i"].project_sync_id == "proj_src"
    finally:
        app.dependency_overrides.clear()


def test_refund_reversal_matches_reward_tx_even_if_rule_project_changed_later():
    """沖銷取被沖銷的回饋交易「當時實際掛的專案」,而不是重讀規則現在的設定。"""
    client, TS = _make_client()
    try:
        email = "x17@t.com"
        hdr_app, hdr_web = _seed(client, "lgx17", email)
        _project(client, hdr_app, "lgx17", "proj_old")
        _project(client, hdr_app, "lgx17", "proj_new")
        rule_id = _create_rule(
            client, hdr_web, "lgx17", reward_project_id="proj_old",
            settlement_type="immediate_after_tx", settlement_days=0, reward_account_id="acc-wallet",
        )
        _spend(client, hdr_app, "lgx17", "tx-1", _utc(2026, 3, 12), 100.0, rule_id)
        assert _tick(TS, _utc(2026, 4, 1))["tx_payouts"] == 1
        assert _patch_rule(client, hdr_web, "lgx17", rule_id, reward_project_id="proj_new").status_code == 200
        _refund(client, hdr_web, "lgx17", "tx-1")
        (reversal,) = _txs(TS, "acc-wallet", "expense")
        assert reversal.project_sync_id == "proj_old"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 7. alembic 0065                                                              #
# --------------------------------------------------------------------------- #


def _alembic(db_path, *args):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_path}"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_alembic_0065_adds_columns_with_defaults_and_downgrades(tmp_path):
    db_path = tmp_path / "m.db"
    _alembic(db_path, "upgrade", "0064_holidays")
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(read_card_reward_rule_projection)")}
        assert "is_basic" not in cols and "reward_project_id" not in cols
        # 升級前就存在的舊規則
        conn.execute(
            "INSERT INTO read_card_reward_rule_projection "
            "(user_id, sync_id, account_sync_id, label, rate_type, rate_value, rounding, "
            "total_rounding, calc_basis, interval, enabled, settlement_type, source_change_id) "
            "VALUES ('u1','crr_old','acc','舊規則','percentage',1.0,'round','round',"
            "'transaction_date','billing_cycle',1,'manual',0)"
        )
        conn.commit()
    finally:
        conn.close()

    _alembic(db_path, "upgrade", "head")
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(read_card_reward_rule_projection)")}
        assert "is_basic" in cols and "reward_project_id" in cols
        row = conn.execute(
            "SELECT is_basic, reward_project_id FROM read_card_reward_rule_projection "
            "WHERE sync_id='crr_old'"
        ).fetchone()
        assert row == (0, None)  # 既有規則升級後:非基本回饋、不指定專案
    finally:
        conn.close()

    _alembic(db_path, "downgrade", "0064_holidays")
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(read_card_reward_rule_projection)")}
        assert "is_basic" not in cols and "reward_project_id" not in cols
    finally:
        conn.close()
