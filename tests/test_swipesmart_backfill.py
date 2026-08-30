"""services/swipesmart_backfill.py::run_swipesmart_usage_backfill(Phase 14
§3.3.4;Phase 16 改版:直接推送 BeeCount 自己算好的已用回饋金額給 SwipeSmart
的 `/api/user/usages/direct`,不再組裝交易明細丟給 SwipeSmart 猜類別)。

mocked swipesmart_client,不打真實外部服務。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.services import swipesmart_backfill


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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text


def _push_rule(client, hdr, ledger_id, sync_id, *, account_id, cap_amount, rate_value,
                cap_shared_key=None, label=""):
    payload = {
        "syncId": sync_id, "accountId": account_id, "label": label,
        "rateType": "percentage", "rateValue": rate_value,
        "rounding": "keep", "totalRounding": "keep",
        "interval": "calendar_month", "capAmount": cap_amount, "enabled": True,
    }
    if cap_shared_key is not None:
        payload["capSharedKey"] = cap_shared_key
    _push(client, hdr, ledger_id, "card_reward_rule", sync_id, payload, device_id="d-app")


def test_backfill_skips_users_without_key():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf1@t.com", device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)
        assert result == {"users": 0, "accounts_attempted": 0, "accounts_succeeded": 0}
        mock_set.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_backfill_pushes_zero_usages_when_no_reward_rules_configured():
    """對照好 swipesmartCardId,但使用者還沒在 BeeCount 建任何回饋規則——送
    空 usages(不是不送),等於「目前沒有可回填的東西」,不強迫兩邊都要設定。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "信用卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        mock_set.assert_awaited_once()
        _, kwargs = mock_set.call_args
        assert kwargs["card_id"] == "CARD_A"
        assert kwargs["usages"] == {}
    finally:
        app.dependency_overrides.clear()


def test_backfill_merges_rules_sharing_cap_amount_without_shared_key(caplog):
    """遠東卡真實場景:服飾/LINE Pay 兩條規則各自獨立(沒設 cap_shared_key),
    但上限金額剛好都是 300——SwipeSmart 的 CapGroupId 只認 CapAmount,兩邊
    只能合併推送同一個數字,並且要記一筆 warning 供之後排查。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "遠東快樂卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "FEIB_HAPPY_CARD"}, device_id="d-app")

        _push_rule(client, hdr_app, "lg1", "rule-apparel", account_id="acc1",
                   cap_amount=300.0, rate_value=5.0, label="服飾")
        _push_rule(client, hdr_app, "lg1", "rule-linepay", account_id="acc1",
                   cap_amount=300.0, rate_value=5.0, label="LINE Pay")

        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "遠東快樂卡",
               "rewardRuleIds": ["rule-apparel"]}, device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx2",
              {"syncId": "tx2", "type": "expense", "amount": 2000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "遠東快樂卡",
               "rewardRuleIds": ["rule-linepay"]}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                with caplog.at_level(logging.WARNING, logger="src.services.swipesmart_backfill"):
                    result = swipesmart_backfill.run_swipesmart_usage_backfill(db, now=now)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        mock_set.assert_awaited_once()
        _, kwargs = mock_set.call_args
        assert kwargs["card_id"] == "FEIB_HAPPY_CARD"
        # 1000*5% + 2000*5% = 50 + 100 = 150,合併推到同一個 "300" key。
        assert kwargs["usages"] == {"300": 150.0}
        assert any("cap_amount=300" in r.message for r in caplog.records)
    finally:
        app.dependency_overrides.clear()


def test_backfill_keeps_different_cap_amounts_separate():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "遠東快樂卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "FEIB_HAPPY_CARD"}, device_id="d-app")

        _push_rule(client, hdr_app, "lg1", "rule-easycard", account_id="acc1",
                   cap_amount=50.0, rate_value=5.0, label="悠遊卡自動加值")
        _push_rule(client, hdr_app, "lg1", "rule-linepay", account_id="acc1",
                   cap_amount=300.0, rate_value=5.0, label="LINE Pay")

        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "expense", "amount": 200.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "遠東快樂卡",
               "rewardRuleIds": ["rule-easycard"]}, device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx2",
              {"syncId": "tx2", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "遠東快樂卡",
               "rewardRuleIds": ["rule-linepay"]}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db, now=now)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        _, kwargs = mock_set.call_args
        assert kwargs["usages"] == {"50": 10.0, "300": 50.0}
    finally:
        app.dependency_overrides.clear()


def test_backfill_merges_rules_sharing_cap_shared_key():
    """跟前一個測試對照:兩條規則本來就用 `cap_shared_key` 明確共用同一個
    真實上限(玉山 U Bear「網購」+「一般消費」那種既有場景),加總後一起套
    上限,結果應該跟合併推送的數字一致。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "玉山UBear卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "ESUN_UBEAR"}, device_id="d-app")

        _push_rule(client, hdr_app, "lg1", "rule-a", account_id="acc1",
                   cap_amount=300.0, rate_value=5.0, cap_shared_key="grp1", label="網購")
        _push_rule(client, hdr_app, "lg1", "rule-b", account_id="acc1",
                   cap_amount=300.0, rate_value=3.0, cap_shared_key="grp1", label="一般消費")

        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "玉山UBear卡",
               "rewardRuleIds": ["rule-a"]}, device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx2",
              {"syncId": "tx2", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "玉山UBear卡",
               "rewardRuleIds": ["rule-b"]}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db, now=now)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        _, kwargs = mock_set.call_args
        # 1000*5% + 1000*3% = 50 + 30 = 80,都在 300 上限內,不受截斷。
        assert kwargs["usages"] == {"300": 80.0}
    finally:
        app.dependency_overrides.clear()


def test_backfill_isolates_per_account_failures():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf6@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf6@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡A", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc2",
              {"syncId": "acc2", "name": "卡B", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_B"}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        async def _side_effect(api_key, *, card_id, usages):
            return card_id != "CARD_A"  # CARD_A 模擬失敗,CARD_B 成功

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(side_effect=_side_effect),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)

        assert result == {"users": 1, "accounts_attempted": 2, "accounts_succeeded": 1}
        assert mock_set.await_count == 2
    finally:
        app.dependency_overrides.clear()


def test_backfill_still_attempts_account_when_billing_cycle_rule_unresolvable():
    """帳戶沒設定 billingDay/paymentDueDay 時,`billing_cycle` interval 的規則
    算不出期間(`status="no_billing_schedule"`),那條規則不計入 usages,但
    帳戶本身依然照常回填(不像改版前那樣整張卡直接跳過)——因為期間解析已經
    下放到 `card_rewards` 逐條規則處理,不再需要帳戶層級的預先擋關。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf7@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf7@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        # 沒有 billingDay/paymentDueDay,也沒有掛靠任何群組。
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "card_reward_rule", "rule-a",
              {"syncId": "rule-a", "accountId": "acc1", "label": "一般",
               "rateType": "percentage", "rateValue": 5.0, "rounding": "keep",
               "totalRounding": "keep", "interval": "billing_cycle", "capAmount": 300.0,
               "enabled": True}, device_id="d-app")

        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "expense", "amount": 1000.0, "happenedAt": _iso(now),
               "accountId": "acc1", "accountName": "卡",
               "rewardRuleIds": ["rule-a"]}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.set_usages_direct",
                new=AsyncMock(return_value=True),
            ) as mock_set:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db, now=now)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        mock_set.assert_awaited_once()
        _, kwargs = mock_set.call_args
        assert kwargs["usages"] == {}
    finally:
        app.dependency_overrides.clear()
