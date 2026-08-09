"""services/swipesmart_backfill.py::run_swipesmart_usage_backfill(Phase 14,
§3.3.4)—— mocked swipesmart_client,不打真實外部服務。"""
from __future__ import annotations

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


def _iso():
    return datetime.now(timezone.utc).isoformat()


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


def test_backfill_skips_users_without_key():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf1@t.com", device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡", "type": "credit_card", "currency": "CNY",
               "billingDay": 1, "paymentDueDay": 15, "swipesmartCardId": "CARD_A"}, device_id="d-app")

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.recompute_usage",
                new=AsyncMock(return_value=True),
            ) as mock_recompute:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)
        assert result == {"users": 0, "accounts_attempted": 0, "accounts_succeeded": 0}
        mock_recompute.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_backfill_collects_current_cycle_expense_transactions():
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
               "billingDay": 1, "paymentDueDay": 15, "swipesmartCardId": "CARD_A"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx1",
              {"syncId": "tx1", "type": "expense", "amount": 500.0, "happenedAt": _iso(),
               "accountId": "acc1", "accountName": "信用卡", "merchant": "商店甲"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx2",
              {"syncId": "tx2", "type": "expense", "amount": 300.0, "happenedAt": _iso(),
               "accountId": "acc1", "accountName": "信用卡", "merchant": "商店乙"}, device_id="d-app")
        # income 交易不應該被算進 transactions(見 §3.3.4:只算消費金額)。
        _push(client, hdr_app, "lg1", "transaction", "tx3",
              {"syncId": "tx3", "type": "income", "amount": 999.0, "happenedAt": _iso(),
               "accountId": "acc1", "accountName": "信用卡", "merchant": "退款"}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.recompute_usage",
                new=AsyncMock(return_value=True),
            ) as mock_recompute:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)

        assert result == {"users": 1, "accounts_attempted": 1, "accounts_succeeded": 1}
        mock_recompute.assert_awaited_once()
        _, kwargs = mock_recompute.call_args
        assert kwargs["card_id"] == "CARD_A"
        amounts = sorted(t["amount"] for t in kwargs["transactions"])
        assert amounts == [300.0, 500.0]
        merchants = {t["merchantName"] for t in kwargs["transactions"]}
        assert merchants == {"商店甲", "商店乙"}
    finally:
        app.dependency_overrides.clear()


def test_backfill_isolates_per_account_failures():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡A", "type": "credit_card", "currency": "CNY",
               "billingDay": 1, "paymentDueDay": 15, "swipesmartCardId": "CARD_A"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc2",
              {"syncId": "acc2", "name": "卡B", "type": "credit_card", "currency": "CNY",
               "billingDay": 1, "paymentDueDay": 15, "swipesmartCardId": "CARD_B"}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        async def _side_effect(api_key, *, card_id, transactions):
            return card_id != "CARD_A"  # CARD_A 模擬失敗,CARD_B 成功

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.recompute_usage",
                new=AsyncMock(side_effect=_side_effect),
            ) as mock_recompute:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)

        assert result == {"users": 1, "accounts_attempted": 2, "accounts_succeeded": 1}
        assert mock_recompute.await_count == 2
    finally:
        app.dependency_overrides.clear()


def test_backfill_skips_accounts_without_billing_schedule():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "bf4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "bf4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        # 沒有 billingDay/paymentDueDay,也沒有掛靠任何群組。
        _push(client, hdr_app, "lg1", "account", "acc1",
              {"syncId": "acc1", "name": "卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "CARD_A"}, device_id="d-app")

        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with TS() as db:
            with patch(
                "src.services.swipesmart_client.recompute_usage",
                new=AsyncMock(return_value=True),
            ) as mock_recompute:
                result = swipesmart_backfill.run_swipesmart_usage_backfill(db)

        assert result == {"users": 0, "accounts_attempted": 0, "accounts_succeeded": 0}
        mock_recompute.assert_not_called()
    finally:
        app.dependency_overrides.clear()
