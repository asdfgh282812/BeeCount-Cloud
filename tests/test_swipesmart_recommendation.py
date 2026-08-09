"""GET /read/ledgers/{id}/card-recommendation(Phase 14,§3.3.3)——
所有 SwipeSmart 呼叫皆 mock,不打真實外部服務(見 PH14 plan §10 的測試邊界:
真實 NAS/key 只用於手動驗證,不進 CI)。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


_FAKE_RESULTS = [
    {
        "card": {"cardId": "CARD_MAPPED", "bankName": "銀行A", "cardName": "卡A"},
        "ruleName": "一般消費", "estimatedReward": 30.0, "effectiveRate": 0.03,
        "baseRate": 0.01, "bonusRate": 0.02, "alertMessages": [], "note": None,
        "isFavorite": False, "matchedCategoryName": "GENERAL", "matchedAlias": "GENERAL",
    },
    {
        "card": {"cardId": "CARD_UNMAPPED", "bankName": "銀行B", "cardName": "卡B"},
        "ruleName": "一般消費", "estimatedReward": 20.0, "effectiveRate": 0.02,
        "baseRate": 0.02, "bonusRate": 0, "alertMessages": [], "note": None,
        "isFavorite": False, "matchedCategoryName": "GENERAL", "matchedAlias": "GENERAL",
    },
]


def _setup_ledger_and_account(client, hdr_app):
    _push(client, hdr_app, "lg1", "ledger", "lg1",
          {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, "lg1", "account", "acc-mapped",
          {"syncId": "acc-mapped", "name": "已對照信用卡", "type": "credit_card",
           "currency": "CNY", "swipesmartCardId": "CARD_MAPPED"}, device_id="d-app")


def test_no_key_returns_empty_list():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "rec1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "rec1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _setup_ledger_and_account(client, hdr_app)

        r = client.get(
            "/api/v1/read/ledgers/lg1/card-recommendation?amount=1000&merchant=全聯",
            headers=hdr_web,
        )
        assert r.status_code == 200, r.text
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


def test_with_key_maps_account_id_for_matched_card_only():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "rec2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "rec2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _setup_ledger_and_account(client, hdr_app)
        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with (
            patch(
                "src.services.swipesmart_client.fetch_user_usages",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "src.services.swipesmart_client.recommend",
                new=AsyncMock(return_value=_FAKE_RESULTS),
            ),
        ):
            r = client.get(
                "/api/v1/read/ledgers/lg1/card-recommendation?amount=1000&merchant=全聯",
                headers=hdr_web,
            )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data) == 2
        mapped = next(x for x in data if x["card_id"] == "CARD_MAPPED")
        unmapped = next(x for x in data if x["card_id"] == "CARD_UNMAPPED")
        assert mapped["account_id"] == "acc-mapped"
        assert mapped["account_name"] == "已對照信用卡"
        assert unmapped["account_id"] is None
        assert unmapped["account_name"] is None
        assert mapped["estimated_reward"] == 30.0
        assert mapped["bank_name"] == "銀行A"
    finally:
        app.dependency_overrides.clear()


def test_swipesmart_timeout_degrades_gracefully():
    client, TS = _make_client()
    try:
        app_tok = _login(client, "rec3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "rec3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _setup_ledger_and_account(client, hdr_app)
        client.post(
            "/api/v1/profile/swipesmart", headers=hdr_web,
            json={"api_key": "ssm_test_key_1234567890"},
        )

        with (
            patch(
                "src.services.swipesmart_client.fetch_user_usages",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "src.services.swipesmart_client.recommend",
                new=AsyncMock(return_value=None),
            ),
        ):
            r = client.get(
                "/api/v1/read/ledgers/lg1/card-recommendation?amount=1000&merchant=全聯",
                headers=hdr_web,
            )
        assert r.status_code == 200, r.text
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()
