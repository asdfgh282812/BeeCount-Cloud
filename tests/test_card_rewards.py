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

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    CardRewardPayout,
    Ledger,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    User,
    UserAccountProjection,
)
from src.services import card_reward_payout, card_rewards


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


def _latest_change_id(client, hdr, ledger_id) -> int:
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr)
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _flat_usage(item):
    """Phase 22(2026-08 使用者反饋):`GET .../card-rewards` 的 `items[i]`
    不再是扁平的單期間物件,改成 `items[i]["periods"]` 陣列(`calendar_month`
    規則橫跨帳單週期時會有 1~2 筆)。既有測試大多只關心「單一期間」的既有
    行為(沒特別構造跨月情境),用這個 helper 把 `periods[0]` 攤平回舊測試
    慣用的扁平 dict 形狀,沿用既有斷言,不用整批改寫。"""
    period = item["periods"][0]
    return {**item, **period}


def _flat_detail(data):
    """`_flat_usage` 的明細彈窗版本,對應 `GET .../card-reward-rules/
    {rule_id}/transactions` 的 `periods` 陣列。"""
    period = data["periods"][0]
    return {**data, **period}


def _income_tx_to(TS, account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.tx_type == "income",
                ReadTxProjection.account_sync_id == account_id,
            ).order_by(ReadTxProjection.happened_at.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _payout_rows(TS, email, rule_id):
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(CardRewardPayout).where(
                CardRewardPayout.user_id == user_id, CardRewardPayout.rule_sync_id == rule_id,
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


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
                # 这条测试关注门槛判定逻辑,不是取整——total_rounding 明确指定
                # "keep"(不取整到整数),隔离掉 Phase 8 #4 两段式取整改版对
                # 这里断言的影响。
                "total_rounding": "keep",
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
        item1 = _flat_usage(rr1.json()["items"][0])
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
        item2 = _flat_usage(rr2.json()["items"][0])
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
        item = _flat_usage(rr.json()["items"][0])
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
        item = _flat_usage(rr.json()["items"][0])
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
        by_label = {i["label"]: _flat_usage(i) for i in data["items"]}
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
        item = _flat_usage(rr.json()["items"][0])
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
        item = _flat_usage(rr.json()["items"][0])
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
        item = _flat_usage(rr.json()["items"][0])
        assert item["status"] == "ok"
        assert item["raw_reward"] == 5.0
        period_start = datetime.fromisoformat(item["period_start"])
        period_end = datetime.fromisoformat(item["period_end"])
        assert period_start.day == 1
        assert period_start.month == now.month
        assert period_end.month == now.month
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_calendar_month_splits_billing_cycle_into_two_months():
    """Phase 22(2026-08 使用者反饋):`calendar_month` 規則橫跨帳單週期時,
    要能同時看到兩個自然月各自的計算結果,不能只看得到 offset 對應到的
    那一個月(使用者截圖情境:帳單週期 2026/08/12~2026/09/11,應該同時看到
    8 月、9 月兩張回饋卡片,金額/上限各自獨立不合併)。

    直接呼叫 service 層函式帶入固定的 `now`,不透過 HTTP 端點(端點內部
    固定用 `datetime.now()`,測試斷言「哪兩個月」會依賴測試實際執行當下的
    真實日期,可能剛好落在月底夾斷的邊界情況而不穩定,見
    `card_rewards._resolve_periods` docstring)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr_ph22a@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr_ph22a@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        # 帳單日 11 號,比照使用者截圖(2026/08/12~2026/09/11)。
        _seed_ledger_and_card(client, hdr_app, "lgr_ph22a", billing_day=11, payment_due_day=25)

        r = client.post(
            "/api/v1/write/ledgers/lgr_ph22a/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "星展加碼", "rate_type": "percentage",
                "rate_value": 9.0, "cap_amount": 500.0, "cap_shared_key": "grp",
                "interval": "calendar_month",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        # 8 月一筆大額(超過上限,驗證 remaining_reward_room/remaining_spend_room
        # 歸零)、9 月一筆小額(驗證兩個月各自獨立算,8 月超額不會污染 9 月的
        # 剩餘額度——即使兩者共用同一個 cap_shared_key)。
        _push(client, hdr_app, "lgr_ph22a", "transaction", "tx-aug",
              {"syncId": "tx-aug", "type": "expense", "amount": 10000.0,
               "happenedAt": "2026-08-20T12:00:00+00:00",
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")
        _push(client, hdr_app, "lgr_ph22a", "transaction", "tx-sep",
              {"syncId": "tx-sep", "type": "expense", "amount": 100.0,
               "happenedAt": "2026-09-02T12:00:00+00:00",
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        as_of = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lgr_ph22a"))
            account = db.scalar(
                select(UserAccountProjection).where(
                    UserAccountProjection.user_id == ledger.user_id,
                    UserAccountProjection.sync_id == "acc-card1",
                )
            )
            rule = db.scalar(
                select(ReadCardRewardRuleProjection).where(
                    ReadCardRewardRuleProjection.sync_id == rule_id,
                )
            )
            results = card_rewards.compute_account_card_rewards(
                db, ledger_id=ledger.id, account=account, rules=[rule], now=as_of, period_offset=0,
            )
            card_rewards.apply_caps(results)

        assert len(results) == 2
        aug, sep = results
        assert aug["period_start"] == date(2026, 8, 1)
        assert aug["period_end"] == date(2026, 8, 31)
        assert sep["period_start"] == date(2026, 9, 1)
        assert sep["period_end"] == date(2026, 9, 30)

        # 8 月:raw = 10000*9% = 900,超過上限 500 -> capped=500,剩餘額度歸零。
        assert aug["raw_reward"] == 900.0
        assert aug["capped_reward"] == 500.0
        assert aug["remaining_reward_room"] == 0.0
        assert aug["remaining_spend_room"] == 0.0
        # 9 月:raw = 100*9% = 9,未超過上限,不受 8 月超額影響
        # (同一個 cap_shared_key 但不同月份,各自獨立算)。
        assert sep["raw_reward"] == 9.0
        assert sep["capped_reward"] == 9.0
        assert sep["remaining_reward_room"] == 491.0
        assert sep["remaining_spend_room"] == 5455.56  # 491 / 9%
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_fixed_amount_rate_type_has_no_remaining_spend_room():
    """需求 #2 後半(2026-08 使用者反饋):「還可以刷多少錢」只對百分比類
    規則有意義,固定金額類規則(每筆固定拿一筆錢,回饋上限是筆數概念,跟
    消費金額無關)`remaining_spend_room` 固定 `None`,不硬湊一個誤導數字。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr_ph22b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr_ph22b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr_ph22b", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr_ph22b/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "滿百送15", "rate_type": "fixed_amount",
                "rate_value": 15.0, "min_tx_amount": 100.0, "cap_amount": 100.0,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]
        _push(client, hdr_app, "lgr_ph22b", "transaction", "tx-fixed",
              {"syncId": "tx-fixed", "type": "expense", "amount": 150.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr_ph22b/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = _flat_usage(rr.json()["items"][0])
        assert item["raw_reward"] == 15.0
        assert item["remaining_reward_room"] == 85.0
        assert item["remaining_spend_room"] is None

        detail = client.get(
            f"/api/v1/read/ledgers/lgr_ph22b/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        )
        assert detail.status_code == 200, detail.text
        data = _flat_detail(detail.json())
        assert data["remaining_reward_room"] == 85.0
        assert data["remaining_spend_room"] is None
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_excludes_tx_before_rule_starts_at_even_in_same_period():
    """2026-08 使用者反饋:消費發生在 8/5,規則活動期間是 8/6 起,卻依舊被
    算出回饋——根因是 `_rule_active_in_period` 只檢查規則生效窗跟「整個
    帳單週期」(這裡是 8/1~8/31 的 calendar_month)有沒有重疊,判定「這期
    規則有效」之後,`_qualifying_transactions` 從沒逐筆比對交易當下規則
    是否真的已經生效,只要交易落在同一個週期內、且勾了這條規則就照算。
    修好後:同一週期內,規則生效日之前的交易不該再被算入回饋。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr10b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr10b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        _seed_ledger_and_card(client, hdr_app, "lgr10b")

        r = client.post(
            "/api/v1/write/ledgers/lgr10b/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "隔天才開始", "rate_type": "percentage",
                "rate_value": 5.0, "interval": "calendar_month",
                "starts_at": _iso(now + timedelta(days=1)),
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        # 交易發生在規則生效日的前一刻,但跟規則生效日仍落在同一個 calendar
        # month 週期內。
        _push(client, hdr_app, "lgr10b", "transaction", "tx-before-start",
              {"syncId": "tx-before-start", "type": "expense", "amount": 279.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr10b/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = _flat_usage(rr.json()["items"][0])
        # 修好之前:status == "ok" 且 raw_reward == 13.95(279 * 5%),規則
        # 都還沒生效卻已經算出回饋。
        assert item["raw_reward"] == 0.0
        assert item["qualifying_spend"] == 0.0

        detail = client.get(
            f"/api/v1/read/ledgers/lgr10b/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        )
        assert detail.status_code == 200, detail.text
        assert _flat_detail(detail.json())["items"] == []
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
        by_id = {i["rule_id"]: _flat_usage(i) for i in rr.json()["items"]}
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
        item1 = _flat_usage(rr1.json()["items"][0])
        assert item1["raw_reward"] == 200.0
        # raw 合计 400 > 共享上限 150,主卡副卡各半 200/400*150=75。
        assert item1["capped_reward"] == 75.0

        rr2 = client.get(
            "/api/v1/read/ledgers/lgr21/accounts/acc-card2/card-rewards", headers=hdr_web,
        )
        assert rr2.status_code == 200, rr2.text
        item2 = _flat_usage(rr2.json()["items"][0])
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
        data = _flat_detail(detail.json())
        assert data["rule_id"] == rule_id
        tx_ids = {item["tx_id"] for item in data["items"]}
        assert tx_ids == {"tx-detail1", "tx-detail2"}
        # raw = 100*5% + 200*5% = 15,未超過 cap_amount=20。
        assert data["raw_reward"] == 15.0
        assert data["capped_reward"] == 15.0
        assert data["remaining_reward_room"] == 5.0
    finally:
        app.dependency_overrides.clear()


def test_get_card_reward_rule_transactions_still_lists_items_when_rule_inactive_for_period():
    """2026-08 使用者反饋 bug #2:規則在使用者查詢的這個週期未生效(例如
    `ends_at` 已經過了那個週期)時,原本明細彈窗連交易清單都被清空,使用者
    完全查不到「這期到底有什麼消費」——即使規則本身沒有停用、也還沒真的
    整體過期。改成規則在該週期未生效時,仍列出這個週期符合分類條件的交易
    (`reward_amount` 一律 0,因為當時規則沒生效沒真的賺到回饋),`status`
    維持 `expired` 讓前端照舊顯示提示文案。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr23b@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr23b@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        # ends_at 要落在「本月」開始之前(比照 calendar_month 週期解析,見
        # `card_rewards._calendar_month_containing`),確保這條規則在本次
        # 查詢的預設週期(period_offset=0 = 本月)裡「不在生效期間」,不受
        # `now` 落在月初/月底的邊界影響。
        first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        _seed_ledger_and_card(client, hdr_app, "lgr23b")

        r = client.post(
            "/api/v1/write/ledgers/lgr23b/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "已過期規則", "rate_type": "percentage", "rate_value": 5.0,
                "interval": "calendar_month",
                "ends_at": _iso(first_of_month - timedelta(seconds=1)),
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr23b", "transaction", "tx-inactive1",
              {"syncId": "tx-inactive1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡", "note": "過期規則消費",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        detail = client.get(
            f"/api/v1/read/ledgers/lgr23b/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        )
        assert detail.status_code == 200, detail.text
        data = _flat_detail(detail.json())
        assert data["status"] == "expired"
        # 修好之前:items 一律清空,連查了什麼消費都看不到。
        tx_ids = {item["tx_id"] for item in data["items"]}
        assert tx_ids == {"tx-inactive1"}
        assert data["items"][0]["reward_amount"] == 0.0
        assert data["raw_reward"] == 0.0
        assert data["capped_reward"] == 0.0
    finally:
        app.dependency_overrides.clear()


def test_card_reward_rule_transactions_payout_tx_id_and_editable_amount():
    """使用者反饋(2026-08,對帳明細可逐筆編輯回饋金額):逐筆結算且已到期
    入帳的項目,明細裡要帶出實際回饋交易的 `payout_tx_id`;還沒入帳的規則
    (`period_end` 沒有逐筆對應)固定 `None`。編輯過那筆回饋交易的金額後,
    再打一次明細端點要看到編輯後的實際值,不能被公式重算蓋回去
    (見 schemas.ReadCardRewardQualifyingTxOut docstring)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr24@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr24@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr24", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr24/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "可編輯測試", "rate_type": "percentage", "rate_value": 10.0,
                "settlement_type": "immediate_after_tx", "settlement_days": 0,
                "reward_account_id": "acc-card1",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr24", "transaction", "tx-payable1",
              {"syncId": "tx-payable1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡", "note": "午餐",
               "rewardRuleIds": [rule_id]}, device_id="d-app")

        # 還沒入帳前,payout_tx_id 固定 None。
        before = _flat_detail(client.get(
            f"/api/v1/read/ledgers/lgr24/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        ).json())
        assert before["items"][0]["payout_tx_id"] is None
        assert before["items"][0]["reward_amount"] == 10.0

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result["tx_payouts"] == 1

        after = _flat_detail(client.get(
            f"/api/v1/read/ledgers/lgr24/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        ).json())
        item = after["items"][0]
        payout_tx_id = item["payout_tx_id"]
        assert payout_tx_id is not None
        assert payout_tx_id != "tx-payable1"
        assert item["reward_amount"] == 10.0

        # 銀行實際入帳的回饋金跟系統算出來的不一樣(比如有取整差異),使用者
        # 在對帳明細裡把這筆回饋金額改成銀行實際入帳的數字。
        patch = client.patch(
            f"/api/v1/write/ledgers/lgr24/transactions/{payout_tx_id}",
            headers=hdr_web,
            json={"base_change_id": 0, "amount": 12.0},
        )
        assert patch.status_code == 200, patch.text

        edited = _flat_detail(client.get(
            f"/api/v1/read/ledgers/lgr24/accounts/acc-card1/card-reward-rules/{rule_id}/transactions",
            headers=hdr_web,
        ).json())
        # 明細要顯示編輯後的實際金額(12.0),不是重算回去的公式值(10.0)。
        assert edited["items"][0]["reward_amount"] == 12.0
        assert edited["items"][0]["payout_tx_id"] == payout_tx_id
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
            # §2.9.5.4 補強(2026-08-04 使用者反饋):自動帶固定的「回饋金」
            # income 分類,不然交易列表顯示空白分類很奇怪。
            assert tx.category_name == "回饋金"
            assert tx.category_kind == "income"
            assert tx.category_sync_id is not None

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


# --------------------------------------------------------------------------- #
# Phase 8(docs/PH6_USER_FEEDBACK_2026-08_SD.md):#4 兩段式取整 / #5 事後修改   #
# 重算 / #5-1 時間對齊 / #15 週期結束回饋日期可設 / #16 規則鎖定+軟刪除       #
# --------------------------------------------------------------------------- #


def test_total_rounding_rounds_aggregate_to_integer_independently_per_rule():
    """#4:單筆「保留小數」(keep)+ 總額分別用 round/floor,驗證兩條規則各自
    的總額取整結果不同(同一批交易,證明 total_rounding 是逐規則獨立生效,
    不是全域設定)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr27@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr27@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr27")

        def _create(label, total_rounding):
            r = client.post(
                "/api/v1/write/ledgers/lgr27/accounts/acc-card1/card-reward-rules",
                headers=hdr_web,
                json={
                    "base_change_id": 0, "label": label, "rate_type": "percentage", "rate_value": 2.0,
                    "rounding": "keep", "total_rounding": total_rounding,
                    # calendar_month 不需要帳戶配置 billing_day 就能算,避開
                    # 這個測試無關的 no_billing_schedule 狀態。
                    "interval": "calendar_month",
                },
            )
            assert r.status_code == 200, r.text
            return r.json()["entity_id"]

        rule_round = _create("四捨五入總額", "round")
        rule_floor = _create("無條件捨去總額", "floor")

        now = datetime.now(timezone.utc)
        # 100*2% + 30*2% = 2.0 + 0.6 = 2.6 -> round=3, floor=2。
        _push(client, hdr_app, "lgr27", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_round, rule_floor]},
              device_id="d-app")
        _push(client, hdr_app, "lgr27", "transaction", "tx-2",
              {"syncId": "tx-2", "type": "expense", "amount": 30.0, "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_round, rule_floor]},
              device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr27/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        by_rule = {item["rule_id"]: _flat_usage(item) for item in rr.json()["items"]}
        assert by_rule[rule_round]["raw_reward"] == 3.0
        assert by_rule[rule_round]["capped_reward"] == 3.0
        assert by_rule[rule_floor]["raw_reward"] == 2.0
        assert by_rule[rule_floor]["capped_reward"] == 2.0
    finally:
        app.dependency_overrides.clear()


def test_compute_settlement_date_period_end_with_month_offset_and_day_of_month():
    """#15:週期結束後一次結算,`settlement_month_offset`/`settlement_day_of_month`
    皆設定時,依「期末日期所在月 + offset 個月」換算出目標月份,日期換成
    `settlement_day_of_month`(含跨年份進位)。兩者皆為 None 時維持現況行為
    (期間結束當天)。純函式,不需要 DB,直接用 SimpleNamespace 假造 rule。"""
    period_end = date(2026, 1, 31)

    # 當月第 5 天。
    rule = SimpleNamespace(
        settlement_type="period_end", settlement_month_offset=0, settlement_day_of_month=5,
    )
    assert card_rewards.compute_settlement_date(rule, period_end=period_end) == date(2026, 1, 5)

    # 次月第 5 天。
    rule = SimpleNamespace(
        settlement_type="period_end", settlement_month_offset=1, settlement_day_of_month=5,
    )
    assert card_rewards.compute_settlement_date(rule, period_end=period_end) == date(2026, 2, 5)

    # 跨年份進位:12 月 + 1 個月 = 次年 1 月。
    rule = SimpleNamespace(
        settlement_type="period_end", settlement_month_offset=1, settlement_day_of_month=5,
    )
    assert card_rewards.compute_settlement_date(
        rule, period_end=date(2026, 12, 20),
    ) == date(2027, 1, 5)

    # 兩者皆為 None:維持現況行為(期間結束當天)。
    rule = SimpleNamespace(
        settlement_type="period_end", settlement_month_offset=None, settlement_day_of_month=None,
    )
    assert card_rewards.compute_settlement_date(rule, period_end=period_end) == period_end


def test_combine_settlement_date_with_source_time_uses_source_time_of_day():
    """#5-1:逐筆結算的回饋交易 happened_at 改用「結算日期 + 來源交易的
    時:分:秒」,不再固定補 00:00:00(換算 UTC+8 顯示固定 08:00 的根因)。"""
    settlement_date = date(2026, 3, 10)
    source = datetime(2026, 3, 5, 21, 43, 7, tzinfo=timezone.utc)
    combined = card_rewards.combine_settlement_date_with_source_time(settlement_date, source)
    assert combined == datetime(2026, 3, 10, 21, 43, 7, tzinfo=timezone.utc)


def test_settlement_month_offset_and_day_of_month_round_trip_and_validation():
    """#15:建立/更新規則時 `settlement_month_offset`/`settlement_day_of_month`
    正確落庫回傳;`settlement_day_of_month` 超出 1~28 範圍要被拒絕(避免月底
    日期溢出)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr28@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr28@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr28")

        r = client.post(
            "/api/v1/write/ledgers/lgr28/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "次月5號結算", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end", "reward_account_id": "acc-card1",
                "settlement_month_offset": 1, "settlement_day_of_month": 5,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        lst = client.get(
            "/api/v1/read/ledgers/lgr28/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        item = next(i for i in lst.json() if i["id"] == rule_id)
        assert item["settlement_month_offset"] == 1
        assert item["settlement_day_of_month"] == 5

        # 超出 1~28 範圍要被拒絕(pydantic Field 校驗,422)。
        r_bad = client.post(
            "/api/v1/write/ledgers/lgr28/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "非法日期", "rate_type": "percentage", "rate_value": 1.0,
                "settlement_type": "period_end", "reward_account_id": "acc-card1",
                "settlement_month_offset": 0, "settlement_day_of_month": 29,
            },
        )
        assert r_bad.status_code == 422, r_bad.text
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_settlement_date_fields_partial_update_keeps_existing():
    """CLAUDE.md SOP 第 7 點:partial update 只带其它字段(不带
    `settlementMonthOffset`/`settlementDayOfMonth`/`totalRounding`)時,既有值
    要保留,不能被静默沖成 null/默認值。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr29@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr29@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr29")

        r = client.post(
            "/api/v1/write/ledgers/lgr29/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "既有設定", "rate_type": "percentage", "rate_value": 1.0,
                "total_rounding": "floor", "settlement_type": "period_end",
                "reward_account_id": "acc-card1",
                "settlement_month_offset": 2, "settlement_day_of_month": 10,
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        # mobile push:只帶 label,不帶其它任何欄位。
        _push(client, hdr_app, "lgr29", "card_reward_rule", rule_id,
              {"syncId": rule_id, "label": "改個名字"}, device_id="d-app")

        row = _rule_row(TS, "crr29@t.com", rule_id)
        assert row.label == "改個名字"
        assert row.total_rounding == "floor"
        assert row.settlement_month_offset == 2
        assert row.settlement_day_of_month == 10
    finally:
        app.dependency_overrides.clear()


def test_card_reward_rule_locks_calc_fields_once_it_has_history():
    """#16:規則有交易掛著之後,計算相關欄位 PATCH 一律 422;label/note/
    enabled/starts_at/ends_at 仍可正常編輯。GET 回傳的 `locked` 旗標也要
    正確反映有/沒有歷史兩種狀態。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr30@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr30@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr30")

        r = client.post(
            "/api/v1/write/ledgers/lgr30/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "鎖定測試", "rate_type": "percentage", "rate_value": 10.0,
                "settlement_type": "immediate_after_tx", "settlement_days": 0,
                "reward_account_id": "acc-cash1",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        lst_before = client.get(
            "/api/v1/read/ledgers/lgr30/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        item_before = next(i for i in lst_before.json() if i["id"] == rule_id)
        assert item_before["locked"] is False

        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgr30", "transaction", "tx-lock-1",
              {"syncId": "tx-lock-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card1", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()

        lst_after = client.get(
            "/api/v1/read/ledgers/lgr30/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        )
        item_after = next(i for i in lst_after.json() if i["id"] == rule_id)
        assert item_after["locked"] is True

        base = _latest_change_id(client, hdr_web, "lgr30")
        r_locked_field = client.patch(
            f"/api/v1/write/ledgers/lgr30/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={"base_change_id": base, "rate_value": 20.0},
        )
        assert r_locked_field.status_code == 422, r_locked_field.text

        base = _latest_change_id(client, hdr_web, "lgr30")
        r_editable = client.patch(
            f"/api/v1/write/ledgers/lgr30/accounts/acc-card1/card-reward-rules/{rule_id}",
            headers=hdr_web,
            json={"base_change_id": base, "label": "改個名字沒問題", "note": "備註"},
        )
        assert r_editable.status_code == 200, r_editable.text
    finally:
        app.dependency_overrides.clear()


def test_card_reward_rule_delete_soft_deletes_when_it_has_history_else_hard_deletes():
    """#16:規則有交易/入帳紀錄掛著時,刪除改成軟刪除(enabled=false,規則
    仍在清單裡);完全沒有歷史的規則,刪除維持既有的物理刪除行為(從清單
    消失),避免歷史交易的規則參照斷鏈,同時不改變無歷史規則的既有體驗。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr31@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr31@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr31")

        r_with_history = client.post(
            "/api/v1/write/ledgers/lgr31/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "有歷史", "rate_type": "percentage", "rate_value": 10.0,
                "settlement_type": "immediate_after_tx", "settlement_days": 0,
                "reward_account_id": "acc-cash1",
            },
        )
        assert r_with_history.status_code == 200, r_with_history.text
        rule_with_history = r_with_history.json()["entity_id"]

        r_no_history = client.post(
            "/api/v1/write/ledgers/lgr31/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={"base_change_id": 0, "label": "無歷史", "rate_type": "percentage", "rate_value": 5.0},
        )
        assert r_no_history.status_code == 200, r_no_history.text
        rule_no_history = r_no_history.json()["entity_id"]

        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgr31", "transaction", "tx-soft-1",
              {"syncId": "tx-soft-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card1", "accountName": "信用卡", "rewardRuleIds": [rule_with_history]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()

        base = _latest_change_id(client, hdr_web, "lgr31")
        del_with_history = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/lgr31/accounts/acc-card1/card-reward-rules/{rule_with_history}",
            headers=hdr_web,
            json={"base_change_id": base},
        )
        assert del_with_history.status_code == 200, del_with_history.text

        base = _latest_change_id(client, hdr_web, "lgr31")
        del_no_history = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/lgr31/accounts/acc-card1/card-reward-rules/{rule_no_history}",
            headers=hdr_web,
            json={"base_change_id": base},
        )
        assert del_no_history.status_code == 200, del_no_history.text

        lst = client.get(
            "/api/v1/read/ledgers/lgr31/accounts/acc-card1/card-reward-rules", headers=hdr_web,
        ).json()
        by_id = {i["id"]: i for i in lst}
        assert by_id[rule_with_history]["enabled"] is False
        assert rule_no_history not in by_id
    finally:
        app.dependency_overrides.clear()


def test_reward_tx_happened_at_aligns_with_source_tx_time_of_day():
    """#5-1:逐筆結算的回饋交易 `happened_at` 時分秒對齊來源交易,不是固定
    00:00:00(換算 UTC+8 顯示固定 08:00 的既有 bug)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr32@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr32@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr32")

        r = client.post(
            "/api/v1/write/ledgers/lgr32/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "時間對齊", "rate_type": "percentage", "rate_value": 10.0,
                "settlement_type": "immediate_after_tx", "settlement_days": 0,
                "reward_account_id": "acc-cash1",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        tx_day = (datetime.now(timezone.utc) - timedelta(days=1)).replace(
            hour=21, minute=43, second=7, microsecond=0,
        )
        _push(client, hdr_app, "lgr32", "transaction", "tx-time-1",
              {"syncId": "tx-time-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card1", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db, now=tx_day)
            db.commit()

        incomes = _income_tx_to(TS, "acc-cash1")
        assert len(incomes) == 1
        reward_happened_at = incomes[0].happened_at
        if reward_happened_at.tzinfo is None:
            reward_happened_at = reward_happened_at.replace(tzinfo=timezone.utc)
        assert reward_happened_at.hour == 21
        assert reward_happened_at.minute == 43
        assert reward_happened_at.second == 7
        assert reward_happened_at.date() == tx_day.date()
    finally:
        app.dependency_overrides.clear()


def test_editing_source_tx_impactful_field_reverses_and_recomputes_reward():
    """#5:交易日期/金額事後修改,已入帳的回饋要被沖銷(刪除),下一輪排程
    用新欄位值重新計算補上正確的回饋;只改備註等不影響計算的欄位不觸發
    沖銷。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr33@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr33@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _seed_ledger_and_card(client, hdr_app, "lgr33")

        r = client.post(
            "/api/v1/write/ledgers/lgr33/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "事後修改測試", "rate_type": "percentage", "rate_value": 10.0,
                "settlement_type": "immediate_after_tx", "settlement_days": 0,
                "reward_account_id": "acc-cash1",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        tx_day = datetime.now(timezone.utc) - timedelta(days=1)
        _push(client, hdr_app, "lgr33", "transaction", "tx-edit-1",
              {"syncId": "tx-edit-1", "type": "expense", "amount": 100.0, "happenedAt": _iso(tx_day),
               "accountId": "acc-card1", "accountName": "信用卡", "rewardRuleIds": [rule_id]},
              device_id="d-app")
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()

        incomes = _income_tx_to(TS, "acc-cash1")
        assert len(incomes) == 1
        assert incomes[0].amount == 10.0  # 100 * 10%
        reward_tx_id = incomes[0].sync_id
        payouts = _payout_rows(TS, "crr33@t.com", rule_id)
        assert len(payouts) == 1

        # 只改備註,不影響回饋計算欄位:不應觸發沖銷。
        base = _latest_change_id(client, hdr_web, "lgr33")
        res_note = client.patch(
            "/api/v1/write/ledgers/lgr33/transactions/tx-edit-1",
            headers=hdr_web,
            json={"base_change_id": base, "note": "改個備註"},
        )
        assert res_note.status_code == 200, res_note.text
        assert len(_payout_rows(TS, "crr33@t.com", rule_id)) == 1
        assert len(_income_tx_to(TS, "acc-cash1")) == 1

        # 改金額(影響回饋計算):應該沖銷(刪除)舊的回饋交易 + 去重記錄。
        base = _latest_change_id(client, hdr_web, "lgr33")
        res_amount = client.patch(
            "/api/v1/write/ledgers/lgr33/transactions/tx-edit-1",
            headers=hdr_web,
            json={"base_change_id": base, "amount": 200.0},
        )
        assert res_amount.status_code == 200, res_amount.text

        assert len(_payout_rows(TS, "crr33@t.com", rule_id)) == 0
        assert len(_income_tx_to(TS, "acc-cash1")) == 0
        with TS() as db:
            reward_tx_still_exists = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == reward_tx_id)
            )
        assert reward_tx_still_exists is None

        # 下一輪排程用新金額(200)重新計算補發:100*10%=10 -> 200*10%=20。
        with TS() as db:
            card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        incomes_after = _income_tx_to(TS, "acc-cash1")
        assert len(incomes_after) == 1
        assert incomes_after[0].amount == 20.0
    finally:
        app.dependency_overrides.clear()


def test_card_rewards_excludes_fee_discount_from_qualifying_base():
    """2026-08 使用者需求(比照 Moze record/introduction 手續費/折扣欄位):
    回饋計算一律用 base_amount(調整前原始金額),不受 fee_amount/
    discount_amount 影響。tx 的 amount(實際入帳總額)是 790 - 100 = 690
    (折扣後),但 base_amount 是 790——qualifying_spend/raw_reward 都應該
    用 790 算,不是 690。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "crr_fd1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "crr_fd1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() + timedelta(days=5)).day
        _seed_ledger_and_card(client, hdr_app, "lgr_fd1", billing_day=billing_day, payment_due_day=10)

        r = client.post(
            "/api/v1/write/ledgers/lgr_fd1/accounts/acc-card1/card-reward-rules",
            headers=hdr_web,
            json={
                "base_change_id": 0, "label": "折扣排除測試",
                "rate_type": "percentage", "rate_value": 2.0,
                "rounding": "keep", "total_rounding": "keep",
            },
        )
        assert r.status_code == 200, r.text
        rule_id = r.json()["entity_id"]

        _push(client, hdr_app, "lgr_fd1", "transaction", "tx-fd-1",
              {"syncId": "tx-fd-1", "type": "expense", "amount": 690.0,
               "baseAmount": 790.0, "feeAmount": 0.0,
               "discountAmount": 100.0, "discountLabel": "滿千送百",
               "happenedAt": _iso(now),
               "accountId": "acc-card1", "accountName": "信用卡",
               "rewardRuleIds": [rule_id]},
              device_id="d-app")

        rr = client.get(
            "/api/v1/read/ledgers/lgr_fd1/accounts/acc-card1/card-rewards", headers=hdr_web,
        )
        assert rr.status_code == 200, rr.text
        item = _flat_usage(rr.json()["items"][0])
        # qualifying_spend/raw_reward 用 base_amount(790)算,不是 amount(690)。
        assert item["qualifying_spend"] == 790.0
        assert item["raw_reward"] == 15.8  # 790 * 2%
    finally:
        app.dependency_overrides.clear()
