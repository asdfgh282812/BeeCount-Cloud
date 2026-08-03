"""信用卡紅利回饋(§2.9.5 Phase 4.5 MOZE_FEATURE_GAP_SD.md)契约测试。

覆盖:
1. `card_reward_rule` 是 user-global sync entity(PK=user_id+sync_id,不掛
   ledger)—— CRUD(owner-only)+ mobile `/sync/push` merge 契约(partial
   update 保留旧值,CLAUDE.md 要求的新增字段模板)
2. write 校验:`account_id` 必须是真实存在的 `credit_card` 帐户,不能是
   `cash`/`account_group`
3. `GET .../accounts/{id}/card-rewards` 计算(2026-08-06 改版:哪笔交易算
   哪条规则改成使用者手动勾选 `reward_rule_ids`,不再靠 category 自动比对):
   percentage/fixed_amount 两种 rate_type、单笔取整、未勾选规则的交易一律不
   计入任何回饋、min_tx_amount/min_spend_threshold 门槛(勾选后仍由系统判断)、
   一笔交易可複选多条规则、cap_amount 单规则上限、cap_shared_key 跨规则共享
   上限(先加总再一起套上限)、billing_cycle(含掛靠群组时借用群组
   billing_day)vs calendar_month 两种 interval、account 没配置 billing_day
   时的 no_billing_schedule 状态
4. write 校验:交易的 `reward_rule_ids` 每个 id 必须是使用者名下、掛在该
   笔交易 account_id 上的真实规则
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import CardRewardPayout, ReadCardRewardRuleProjection, ReadTxProjection, User


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


def _dt(d, hour=12):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).isoformat()


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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _rule_row(TS, email, sync_id) -> ReadCardRewardRuleProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(ReadCardRewardRuleProjection).where(
                ReadCardRewardRuleProjection.user_id == user_id,
                ReadCardRewardRuleProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def _seed_ledger_and_card(client, hdr_app, ledger_id, *, billing_day=None, payment_due_day=None):
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    card_payload = {"syncId": "acc-card1", "name": "信用卡", "type": "credit_card", "currency": "CNY"}
    if billing_day is not None:
        card_payload["billingDay"] = billing_day
    if payment_due_day is not None:
        card_payload["paymentDueDay"] = payment_due_day
    _push(client, hdr_app, ledger_id, "account", "acc-card1", card_payload, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-cash1",
          {"syncId": "acc-cash1", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")


# --------------------------------------------------------------------------- #
# 1. CRUD + owner-only write 校验                                             #
# --------------------------------------------------------------------------- #


def test_create_list_update_delete_card_reward_rule():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr1")

        r = client.post(
            "/api/v1/write/ledgers/lgr1/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "网购 2%", "category_ids": ["cat-shopping"],
                "rate_type": "percentage", "rate_value": 2.0, "rounding": "round",
                "min_spend_threshold": 100.0, "min_tx_amount": 10.0, "cap_amount": 150.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        lst = client.get(
            "/api/v1/read/ledgers/lgr1/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        assert lst.status_code == 200, lst.text
        items = lst.json()
        assert len(items) == 1
        assert items[0]["id"] == rule_id
        assert items[0]["label"] == "网购 2%"
        assert items[0]["category_ids"] == ["cat-shopping"]
        assert items[0]["rate_value"] == 2.0
        assert items[0]["cap_amount"] == 150.0
        assert items[0]["enabled"] is True

        upd = client.patch(
            f"/api/v1/write/ledgers/lgr1/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={"base_change_id": r.json()["new_change_id"], "rate_value": 3.0, "cap_amount": None},
        )
        assert upd.status_code == 200, upd.text
        row = _rule_row(TS, "crr1@t.com", rule_id)
        assert row.rate_value == 3.0
        assert row.cap_amount is None

        dele = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/lgr1/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web, json={"base_change_id": upd.json()["new_change_id"]},
        )
        assert dele.status_code == 200, dele.text
        lst2 = client.get(
            "/api/v1/read/ledgers/lgr1/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        assert lst2.json() == []
    finally:
        app.dependency_overrides.clear()


def test_create_card_reward_rule_rejects_non_credit_card_account():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr2")
        _push(client, hdr_app, "lgr2", "account", "acc-group2",
              {"syncId": "acc-group2", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r_cash = client.post(
            "/api/v1/write/ledgers/lgr2/accounts/acc-cash1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "现金规则", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r_cash.status_code == 400, r_cash.text

        r_group = client.post(
            "/api/v1/write/ledgers/lgr2/accounts/acc-group2/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "群组规则", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r_group.status_code == 400, r_group.text
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_card_reward_rule_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr3@t.com", device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        _seed_ledger_and_card(client, hdr_app, "lgr3")

        _push(client, hdr_app, "lgr3", "card_reward_rule", "crr-1", {
            "syncId": "crr-1", "accountId": "acc-card1", "label": "网购",
            "categoryIds": ["cat-shopping"], "rateType": "percentage", "rateValue": 2.0,
            "rounding": "round", "calcBasis": "transaction_date", "interval": "billing_cycle",
            "minSpendThreshold": 100.0, "minTxAmount": 10.0, "capAmount": 150.0,
            "capSharedKey": "grp", "note": "备注", "enabled": True,
        }, device_id="d-app")

        # partial update:只带 label,其它字段缺键,merge 应该保留旧值。
        _push(client, hdr_app, "lgr3", "card_reward_rule", "crr-1", {
            "syncId": "crr-1", "label": "网购 v2",
        }, device_id="d-app")

        row = _rule_row(TS, "crr3@t.com", "crr-1")
        assert row.label == "网购 v2"
        assert row.account_sync_id == "acc-card1"
        assert row.rate_type == "percentage"
        assert row.rate_value == 2.0
        assert row.rounding == "round"
        assert row.min_spend_threshold == 100.0
        assert row.min_tx_amount == 10.0
        assert row.cap_amount == 150.0
        assert row.cap_shared_key == "grp"
        assert row.note == "备注"
        assert row.enabled is True
        import json
        assert json.loads(row.category_sync_ids_json) == ["cat-shopping"]
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 2. card-rewards 計算                                                        #
# --------------------------------------------------------------------------- #


def test_card_rewards_manual_tagging_and_threshold_gating():
    """2026-08-06 改版:哪笔交易算哪条规则由使用者手动勾选
    (`rewardRuleIds`),系统不再靠 category 自动比对——tx-3 金额/日期都
    符合规则,但没被勾选,理应完全不计入任何回饋。min_tx_amount/
    min_spend_threshold 这两个"金额"门槛,勾选后仍由系统判断。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        now = datetime.now(timezone.utc)
        # billing_day 设在今天之后几天,让"今天"落在还在累积的 open cycle 内。
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr4", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr4/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "餐饮 2%",
                "rate_type": "percentage", "rate_value": 2.0, "rounding": "round",
                "min_tx_amount": 50.0, "min_spend_threshold": 100.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        # tx1: 勾选了规则,80 >= min_tx_amount 符合;tx2: 勾选了规则但金额太小
        # 被 min_tx_amount 挡;tx3: 金额/时间都符合但**没有勾选任何规则**,
        # 完全不计入(即使旧版 category 逻辑下它会被过滤掉的分类现在也一致)。
        _push(client, hdr_app, "lgr4", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 80.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgr4", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 30.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgr4", "transaction", "tx-3",
              {"syncId": "tx-3", "type": "expense", "amount": 200.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡"},
              device_id="d-app")

        rr1 = client.get(
            "/api/v1/read/ledgers/lgr4/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr1.status_code == 200, rr1.text
        item1 = rr1.json()["items"][0]
        assert item1["qualifying_spend"] == 80.0
        assert item1["threshold_met"] is False
        assert item1["raw_reward"] == 0.0
        assert item1["capped_reward"] == 0.0

        # 再加一笔 60(有勾选),累积到 140 >= 100 门槛达标。
        _push(client, hdr_app, "lgr4", "transaction", "tx-4",
              {"syncId": "tx-4", "type": "expense", "amount": 60.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]},
              device_id="d-app")
        rr2 = client.get(
            "/api/v1/read/ledgers/lgr4/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        item2 = rr2.json()["items"][0]
        assert item2["qualifying_spend"] == 140.0
        assert item2["threshold_met"] is True
        assert item2["raw_reward"] == 2.8  # 80*2% + 60*2% = 1.6 + 1.2
        assert item2["capped_reward"] == 2.8
        assert rr2.json()["total_reward"] == 2.8
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_fixed_amount_rate_type():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr5", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr5/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "滿百送15", "rate_type": "fixed_amount",
                "rate_value": 15.0, "min_tx_amount": 100.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr5", "transaction", "tx-a",
              {"syncId": "tx-a", "type": "expense", "amount": 150.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")
        _push(client, hdr_app, "lgr5", "transaction", "tx-b",
              {"syncId": "tx-b", "type": "expense", "amount": 50.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr5/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = rr.json()["items"][0]
        assert item["qualifying_spend"] == 150.0
        assert item["raw_reward"] == 15.0
        assert item["capped_reward"] == 15.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_cap_amount_truncates_single_rule():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr6@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr6@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr6", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr6/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "高倍回饋", "rate_type": "percentage",
                "rate_value": 10.0, "cap_amount": 5.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        _push(client, hdr_app, "lgr6", "transaction", "tx-big",
              {"syncId": "tx-big", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr6/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        item = rr.json()["items"][0]
        assert item["raw_reward"] == 100.0
        assert item["capped_reward"] == 5.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_cap_shared_key_pools_multiple_rules():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr7@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr7@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr7", billing_day=billing_day, payment_due_day=10)

        r1 = client.post(
            "/api/v1/write/ledgers/lgr7/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "网购",
                "rate_type": "percentage", "rate_value": 2.0, "cap_amount": 150.0,
                "cap_shared_key": "grp",
            },
        )
        assert r1.status_code == 200, r1.text
        rule1_id = r1.json()["entity_id"]
        r2 = client.post(
            "/api/v1/write/ledgers/lgr7/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "一般消費",
                "rate_type": "percentage", "rate_value": 1.0,
                "cap_amount": 150.0, "cap_shared_key": "grp",
            },
        )
        assert r2.status_code == 200, r2.text
        rule2_id = r2.json()["entity_id"]

        _push(client, hdr_app, "lgr7", "transaction", "tx-shop",
              {"syncId": "tx-shop", "type": "expense", "amount": 10000.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule1_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgr7", "transaction", "tx-other",
              {"syncId": "tx-other", "type": "expense", "amount": 10000.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule2_id]},
              device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr7/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        data = rr.json()
        by_label = {i["label"]: i for i in data["items"]}
        assert by_label["网购"]["raw_reward"] == 200.0
        assert by_label["一般消費"]["raw_reward"] == 100.0
        # raw 合计 300 > 共享上限 150,按比例分摊:200/300*150=100, 剩余 50。
        assert by_label["网购"]["capped_reward"] == 100.0
        assert by_label["一般消費"]["capped_reward"] == 50.0
        assert data["total_reward"] == 150.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_no_billing_schedule_status_when_account_unconfigured():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr8@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr8@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        # 没设 billing_day/payment_due_day。
        _seed_ledger_and_card(client, hdr_app, "lgr8")

        r = client.post(
            "/api/v1/write/ledgers/lgr8/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "预设周期规则", "rate_type": "percentage",
                "rate_value": 1.0, "interval": "billing_cycle",
            },
        )
        assert r.status_code == 200, r.text

        rr = client.get(
            "/api/v1/read/ledgers/lgr8/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = rr.json()["items"][0]
        assert item["status"] == "no_billing_schedule"
        assert item["capped_reward"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_billing_cycle_resolves_via_parent_group_schedule():
    """§2.9 群组模型:掛靠群组的子卡自己没有 billing_day,回饋计算要能借用
    它掛靠的群组的 billing_day/payment_due_day(跟 billing-summary 同一套
    取舍)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr9@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr9@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _push(client, hdr_app, "lgr9", "ledger", "lgr9",
              {"syncId": "lgr9", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgr9", "account", "acc-group9",
              {"syncId": "acc-group9", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 10}, device_id="d-app")
        _push(client, hdr_app, "lgr9", "account", "acc-child9",
              {"syncId": "acc-child9", "name": "子卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group9"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgr9/accounts/acc-child9/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "子卡规则", "rate_type": "percentage", "rate_value": 5.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr9", "transaction", "tx-child",
              {"syncId": "tx-child", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-child9", "accountName": "子卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr9/accounts/acc-child9/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = rr.json()["items"][0]
        assert item["status"] == "ok"
        assert item["raw_reward"] == 5.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_calendar_month_interval():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr10@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr10@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        # 没设 billing_day 也没关系,calendar_month 不需要。
        _seed_ledger_and_card(client, hdr_app, "lgr10")

        r = client.post(
            "/api/v1/write/ledgers/lgr10/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "月結 1%", "rate_type": "percentage",
                "rate_value": 1.0, "interval": "calendar_month",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr10", "transaction", "tx-m1",
              {"syncId": "tx-m1", "type": "expense", "amount": 500.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr10/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = rr.json()["items"][0]
        assert item["status"] == "ok"
        assert item["raw_reward"] == 5.0
        period_start = datetime.fromisoformat(item["period_start"])
        period_end = datetime.fromisoformat(item["period_end"])
        assert period_start.day == 1
        assert period_start.month == now.month
        assert period_end.month == now.month
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_single_tx_can_tag_multiple_rules():
    """使用者反馈:一笔交易可以複选多条回饋规则(例如「網購 2%」+
    「滿百送15」疊加),不是单选一条。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr12@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr12@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr12", billing_day=billing_day, payment_due_day=10)

        r1 = client.post(
            "/api/v1/write/ledgers/lgr12/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "網購 2%", "rate_type": "percentage", "rate_value": 2.0},
        )
        assert r1.status_code == 200, r1.text
        rule1_id = r1.json()["entity_id"]
        r2 = client.post(
            "/api/v1/write/ledgers/lgr12/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "滿百送15",
                "rate_type": "fixed_amount", "rate_value": 15.0, "min_tx_amount": 100.0,
            },
        )
        assert r2.status_code == 200, r2.text
        rule2_id = r2.json()["entity_id"]

        # 同一笔交易同时勾选两条规则。
        _push(client, hdr_app, "lgr12", "transaction", "tx-both",
              {"syncId": "tx-both", "type": "expense", "amount": 500.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule1_id, rule2_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr12/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        by_id = {i["rule_id"]: i for i in rr.json()["items"]}
        assert by_id[rule1_id]["raw_reward"] == 10.0  # 500 * 2%
        assert by_id[rule2_id]["raw_reward"] == 15.0  # 满百固定 15
        assert rr.json()["total_reward"] == 25.0
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_tx_reward_rule_ids_partial_update_keeps_existing_value():
    """跟 §2.4 拆帳/§2.5 debtId 同款契约:partial update 只带其它字段时,
    `rewardRuleIds` 缺键要保留既有勾选,不能被静默清空。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr13@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr13@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr13")

        r = client.post(
            "/api/v1/write/ledgers/lgr13/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "规则A", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr13", "transaction", "tx-p1",
              {"syncId": "tx-p1", "type": "expense", "amount": 100.0, "happenedAt": _iso(),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        # partial update:只带 note,不带 rewardRuleIds。
        _push(client, hdr_app, "lgr13", "transaction", "tx-p1",
              {"syncId": "tx-p1", "note": "更新备注"}, device_id="d-app")

        with TS() as db:
            row = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-p1")
            )
            assert row is not None
            assert row.note == "更新备注"
            import json
            assert json.loads(row.reward_rule_sync_ids_json) == [rule_id]
    finally:
        app.dependency_overrides.clear()


def test_write_tx_rejects_unknown_or_wrong_account_reward_rule_id():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr14@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr14@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr14")
        _push(client, hdr_app, "lgr14", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "第二張卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgr14/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "卡一規則", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        base_change_id = r.json()["new_change_id"]

        # 不存在的规则 id ——失败不写入,base_change_id 不需要推进。
        r_unknown = client.post(
            "/api/v1/write/ledgers/lgr14/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base_change_id, "tx_type": "expense", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card1",
                "reward_rule_ids": ["not-a-real-rule"],
            },
        )
        assert r_unknown.status_code == 400, r_unknown.text

        # 规则存在,但掛在另一張卡上,不属于这笔交易的 account_id。
        r_wrong_account = client.post(
            "/api/v1/write/ledgers/lgr14/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base_change_id, "tx_type": "expense", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card2",
                "reward_rule_ids": [rule_id],
            },
        )
        assert r_wrong_account.status_code == 400, r_wrong_account.text

        # 合法:规则归属正确的帐户。
        r_ok = client.post(
            "/api/v1/write/ledgers/lgr14/transactions",
            headers=hdr_web,
            json={
                "base_change_id": base_change_id, "tx_type": "expense", "amount": 100.0,
                "happened_at": _iso(), "account_id": "acc-card1",
                "reward_rule_ids": [rule_id],
            },
        )
        assert r_ok.status_code == 200, r_ok.text
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_rejects_account_group_target():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr11@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr11@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgr11", "ledger", "lgr11",
              {"syncId": "lgr11", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgr11", "account", "acc-group11",
              {"syncId": "acc-group11", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr11/accounts/acc-group11/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 400, rr.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 3. 驗證期間 + 自動入帳結算欄位(§2.9.5.4)                                    #
# --------------------------------------------------------------------------- #


def test_create_card_reward_rule_settlement_fields_round_trip():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr15@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr15@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr15")
        now = datetime.now(timezone.utc)

        r = client.post(
            "/api/v1/write/ledgers/lgr15/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "逐筆入帳", "rate_type": "percentage", "rate_value": 1.0,
                "starts_at": _iso(now - timedelta(days=1)), "ends_at": _iso(now + timedelta(days=30)),
                "settlement_type": "immediate_after_tx", "settlement_days": 3,
                "reward_account_id": "acc-card1",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        lst = client.get(
            "/api/v1/read/ledgers/lgr15/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        item = lst.json()[0]
        assert item["id"] == rule_id
        assert item["settlement_type"] == "immediate_after_tx"
        assert item["settlement_days"] == 3
        assert item["reward_account_id"] == "acc-card1"
        assert item["starts_at"] is not None
        assert item["ends_at"] is not None
    finally:
        app.dependency_overrides.clear()


def test_create_card_reward_rule_rejects_missing_settlement_days():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr16@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr16@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr16")

        r = client.post(
            "/api/v1/write/ledgers/lgr16/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "缺天數", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "immediate_after_tx", "reward_account_id": "acc-card1",
            },
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_create_card_reward_rule_rejects_missing_reward_account():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr17@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr17@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr17")

        r = client.post(
            "/api/v1/write/ledgers/lgr17/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "缺目的帳戶", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end",
            },
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_create_card_reward_rule_rejects_reward_account_group_or_unknown():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr18@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr18@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr18")
        _push(client, hdr_app, "lgr18", "account", "acc-group18",
              {"syncId": "acc-group18", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r_group = client.post(
            "/api/v1/write/ledgers/lgr18/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "指向群組", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end", "reward_account_id": "acc-group18",
            },
        )
        assert r_group.status_code == 400, r_group.text

        r_unknown = client.post(
            "/api/v1/write/ledgers/lgr18/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "指向未知帳戶", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end", "reward_account_id": "not-a-real-account",
            },
        )
        assert r_unknown.status_code == 400, r_unknown.text

        # 指向自己這張卡(最常見用例——直接折抵當期帳單)要放行。
        r_self = client.post(
            "/api/v1/write/ledgers/lgr18/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "指向自己", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end", "reward_account_id": "acc-card1",
            },
        )
        assert r_self.status_code == 200, r_self.text
    finally:
        app.dependency_overrides.clear()


def test_update_card_reward_rule_settlement_fields_merged_state_validated():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr19@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr19@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr19")

        r = client.post(
            "/api/v1/write/ledgers/lgr19/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "手動規則", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        base = r.json()["new_change_id"]

        # 只切結算類型,不帶 settlement_days ——merge 後狀態不完整,要擋。
        upd_bad = client.patch(
            f"/api/v1/write/ledgers/lgr19/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={"base_change_id": base, "settlement_type": "immediate_after_tx"},
        )
        assert upd_bad.status_code == 400, upd_bad.text

        # 一次帶齊 settlement_days + reward_account_id 才成功。
        upd_ok = client.patch(
            f"/api/v1/write/ledgers/lgr19/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={
                "base_change_id": base, "settlement_type": "immediate_after_tx",
                "settlement_days": 5, "reward_account_id": "acc-card1",
            },
        )
        assert upd_ok.status_code == 200, upd_ok.text
        row = _rule_row(TS, "crr19@t.com", rule_id)
        assert row.settlement_type == "immediate_after_tx"
        assert row.settlement_days == 5
        assert row.reward_account_id == "acc-card1"

        # 切回 period_end:settlement_days 應該被清掉(不再需要)。
        upd_period = client.patch(
            f"/api/v1/write/ledgers/lgr19/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={"base_change_id": upd_ok.json()["new_change_id"], "settlement_type": "period_end"},
        )
        assert upd_period.status_code == 200, upd_period.text
        row2 = _rule_row(TS, "crr19@t.com", rule_id)
        assert row2.settlement_type == "period_end"
        assert row2.settlement_days is None
        assert row2.reward_account_id == "acc-card1"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_settlement_fields_partial_update_keeps_existing():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr20@t.com", device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        _seed_ledger_and_card(client, hdr_app, "lgr20")

        _push(client, hdr_app, "lgr20", "card_reward_rule", "crr-20", {
            "syncId": "crr-20", "accountId": "acc-card1", "label": "逐筆",
            "rateType": "percentage", "rateValue": 1.0,
            "settlementType": "immediate_after_tx", "settlementDays": 2,
            "rewardAccountId": "acc-card1", "enabled": True,
        }, device_id="d-app")

        # partial update:只带 label,結算欄位缺鍵要保留舊值。
        _push(client, hdr_app, "lgr20", "card_reward_rule", "crr-20", {
            "syncId": "crr-20", "label": "逐筆 v2",
        }, device_id="d-app")

        row = _rule_row(TS, "crr20@t.com", "crr-20")
        assert row.label == "逐筆 v2"
        assert row.settlement_type == "immediate_after_tx"
        assert row.settlement_days == 2
        assert row.reward_account_id == "acc-card1"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 4. 跨卡共用上限群組 + 交易明細彈窗(§2.9.5.3/§2.9.5.4)                       #
# --------------------------------------------------------------------------- #


def test_card_rewards_cross_card_cap_shared_key_pools_rules():
    """使用者反饋:共用上限群組要跨卡(同一家銀行的正副卡共用一個回饋額度),
    不是只有同一張卡上的多條規則。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr21@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr21@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr21", billing_day=billing_day, payment_due_day=10)
        _push(client, hdr_app, "lgr21", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "副卡", "type": "credit_card", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": 10}, device_id="d-app")

        r1 = client.post(
            "/api/v1/write/ledgers/lgr21/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "主卡規則", "rate_type": "percentage", "rate_value": 2.0,
                "cap_amount": 150.0, "cap_shared_key": "family-grp",
            },
        )
        assert r1.status_code == 200, r1.text
        rule1_id = r1.json()["entity_id"]
        r2 = client.post(
            "/api/v1/write/ledgers/lgr21/accounts/acc-card2/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "副卡規則",
                "rate_type": "percentage", "rate_value": 2.0,
                "cap_amount": 150.0, "cap_shared_key": "family-grp",
            },
        )
        assert r2.status_code == 200, r2.text
        rule2_id = r2.json()["entity_id"]

        _push(client, hdr_app, "lgr21", "transaction", "tx-card1",
              {"syncId": "tx-card1", "type": "expense", "amount": 10000.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡", "rewardRuleIds": [rule1_id]},
              device_id="d-app")
        _push(client, hdr_app, "lgr21", "transaction", "tx-card2",
              {"syncId": "tx-card2", "type": "expense", "amount": 10000.0, "happenedAt": _iso(now),
               "accountId": "acc-card2", "accountName": "副卡", "rewardRuleIds": [rule2_id]},
              device_id="d-app")

        rr1 = client.get(
            "/api/v1/read/ledgers/lgr21/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr1.status_code == 200, rr1.text
        item1 = rr1.json()["items"][0]
        assert item1["raw_reward"] == 200.0
        # raw 合计 400 > 共享上限 150,主卡副卡各半 200/400*150=75。
        assert item1["capped_reward"] == 75.0

        rr2 = client.get(
            "/api/v1/read/ledgers/lgr21/accounts/acc-card2/card-rewards", headers=hdr_web,
        )
        assert rr2.status_code == 200, rr2.text
        item2 = rr2.json()["items"][0]
        assert item2["raw_reward"] == 200.0
        assert item2["capped_reward"] == 75.0
        assert rule1_id != rule2_id
    finally:
        app.dependency_overrides.clear()


def test_list_all_card_reward_rules_returns_rules_across_cards():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr22@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr22@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr22")
        _push(client, hdr_app, "lgr22", "account", "acc-card2",
              {"syncId": "acc-card2", "name": "副卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r1 = client.post(
            "/api/v1/write/ledgers/lgr22/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "主卡規則", "rate_type": "percentage", "rate_value": 1.0},
        )
        assert r1.status_code == 200, r1.text
        r2 = client.post(
            "/api/v1/write/ledgers/lgr22/accounts/acc-card2/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": r1.json()["new_change_id"], "label": "副卡規則",
                "rate_type": "percentage", "rate_value": 1.0,
            },
        )
        assert r2.status_code == 200, r2.text

        lst = client.get("/api/v1/read/ledgers/lgr22/card-reward-rules", headers=hdr_web)
        assert lst.status_code == 200, lst.text
        account_ids = {item["account_id"] for item in lst.json()}
        assert account_ids == {"acc-card1", "acc-card2"}
    finally:
        app.dependency_overrides.clear()


def test_get_card_reward_rule_transactions_endpoint():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr23@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr23@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr23", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr23/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "明細測試", "rate_type": "percentage", "rate_value": 5.0,
                "cap_amount": 20.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr23", "transaction", "tx-detail1",
              {"syncId": "tx-detail1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡", "note": "午餐",
               "rewardRuleIds": [rule_id]}, device_id="d-app")
        _push(client, hdr_app, "lgr23", "transaction", "tx-detail2",
              {"syncId": "tx-detail2", "type": "expense", "amount": 200.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡", "note": "購物",
               "rewardRuleIds": [rule_id]}, device_id="d-app")
        # 沒有勾選的交易不應該出現在明細裡。
        _push(client, hdr_app, "lgr23", "transaction", "tx-detail3",
              {"syncId": "tx-detail3", "type": "expense", "amount": 500.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡"}, device_id="d-app")

        detail = client.get(
            f"/api/v1/read/ledgers/lgr23/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        )
        assert detail.status_code == 200, detail.text
        data = detail.json()
        assert data["rule_id"] == rule_id
        tx_ids = {item["tx_id"] for item in data["items"]}
        assert tx_ids == {"tx-detail1", "tx-detail2"}
        # raw = 100*5% + 200*5% = 15,未超過 cap_amount=20。
        assert data["raw_reward"] == 15.0
        assert data["capped_reward"] == 15.0
        assert data["remaining_reward_room"] == 5.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 5. 手動入帳(§2.9.5.4 補強,2026-08-03 使用者反饋):settlement_type=manual  #
#    的規則不進自動掃描範圍,使用者自己按按鈕臨時指定金額/目的帳戶入帳。      #
# --------------------------------------------------------------------------- #


def test_manual_payout_creates_income_tx_and_dedup_row():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr24@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr24@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr24")

        r = client.post(
            "/api/v1/write/ledgers/lgr24/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "手動測試", "rate_type": "percentage", "rate_value": 5.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        r2 = client.post(
            f"/api/v1/write/ledgers/lgr24/accounts/acc-card1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={
                "base_change_id": r.json()["new_change_id"], "amount": 30.0,
                "reward_account_id": "acc-cash1", "note": "手動入帳測試",
            },
        )
        assert r2.status_code == 200, r2.text
        tx_id = r2.json()["entity_id"]

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "crr24@t.com"))
            tx = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert tx is not None
            assert tx.tx_type == "income"
            assert tx.amount == 30.0
            assert tx.account_sync_id == "acc-cash1"
            assert tx.note == "手動入帳測試"

            payout = db.scalar(
                select(CardRewardPayout).where(
                    CardRewardPayout.user_id == user_id,
                    CardRewardPayout.rule_sync_id == rule_id,
                )
            )
            assert payout is not None
            assert payout.dedup_key == f"manual:{tx_id}"
            assert payout.amount == 30.0
            assert payout.payout_tx_sync_id == tx_id

        # 手動觸發不受自動入帳引擎 settlement_type != "manual" 過濾影響,
        # 自動掃描不應該再重複處理它(rule 本身還是 manual,掃描器直接跳過)。
        from src.services.card_reward_payout import materialize_due_card_reward_payouts

        with TS() as db:
            counts = materialize_due_card_reward_payouts(db, now=datetime.now(timezone.utc))
            assert counts == {"tx_payouts": 0, "period_payouts": 0}
    finally:
        app.dependency_overrides.clear()


def test_manual_payout_self_credit_offsets_billing():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr25@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr25@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr25", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr25/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "自抵測試", "rate_type": "percentage", "rate_value": 5.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        change_id = r.json()["new_change_id"]

        before = client.get(
            "/api/v1/read/ledgers/lgr25/accounts/acc-card1/billing-summary", headers=hdr_web,
        )
        assert before.status_code == 200, before.text
        baseline_open_spend = before.json()["open_cycle_spend"]

        r2 = client.post(
            f"/api/v1/write/ledgers/lgr25/accounts/acc-card1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={"base_change_id": change_id, "amount": 20.0, "reward_account_id": "acc-card1"},
        )
        assert r2.status_code == 200, r2.text

        after = client.get(
            "/api/v1/read/ledgers/lgr25/accounts/acc-card1/billing-summary", headers=hdr_web,
        )
        assert after.status_code == 200, after.text
        assert after.json()["open_cycle_spend"] == baseline_open_spend - 20.0
    finally:
        app.dependency_overrides.clear()


def test_manual_payout_rejects_account_group_and_unknown_account():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr26@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr26@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr26")
        _push(client, hdr_app, "lgr26", "account", "acc-group26",
              {"syncId": "acc-group26", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgr26/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "拒絕測試", "rate_type": "percentage", "rate_value": 5.0},
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        change_id = r.json()["new_change_id"]

        bad_group = client.post(
            f"/api/v1/write/ledgers/lgr26/accounts/acc-card1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={"base_change_id": change_id, "amount": 10.0, "reward_account_id": "acc-group26"},
        )
        assert bad_group.status_code == 400, bad_group.text

        bad_unknown = client.post(
            f"/api/v1/write/ledgers/lgr26/accounts/acc-card1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={"base_change_id": change_id, "amount": 10.0, "reward_account_id": "does-not-exist"},
        )
        assert bad_unknown.status_code == 400, bad_unknown.text

        bad_amount = client.post(
            f"/api/v1/write/ledgers/lgr26/accounts/acc-card1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={"base_change_id": change_id, "amount": 0, "reward_account_id": "acc-cash1"},
        )
        assert bad_amount.status_code == 422, bad_amount.text

        wrong_account = client.post(
            f"/api/v1/write/ledgers/lgr26/accounts/acc-cash1/card-reward-rules/{rule_id}/manual-payout",
            headers=hdr_web,
            json={"base_change_id": change_id, "amount": 10.0, "reward_account_id": "acc-cash1"},
        )
        assert wrong_account.status_code == 400, wrong_account.text
    finally:
        app.dependency_overrides.clear()
