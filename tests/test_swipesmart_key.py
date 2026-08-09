"""SwipeSmart Personal API Key CRUD(Phase 14,§3.3.1(a))—— /profile/swipesmart。

契約重點:明文 Key 絕不出現在任何 GET 回應裡,只在剛設定當下的回應裡以遮罩
形式確認;資料庫落地欄位是加密的(不是明文)。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User, UserProfile


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


def _login(client, email):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "Pa$$word1!", "device_id": "d1",
            "client_type": "web", "device_name": "pytest", "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_key_status_defaults_to_absent():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        r = client.get("/api/v1/profile/swipesmart", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json() == {"has_key": False, "masked": None, "auto_mapped": 0}
    finally:
        app.dependency_overrides.clear()


def test_set_key_returns_masked_never_plaintext():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        plaintext = "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"

        r = client.post("/api/v1/profile/swipesmart", headers=hdr, json={"api_key": plaintext})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["has_key"] is True
        assert body["masked"] != plaintext
        assert plaintext not in r.text

        r2 = client.get("/api/v1/profile/swipesmart", headers=hdr)
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["has_key"] is True
        assert body2["masked"] == body["masked"]
        assert plaintext not in r2.text
    finally:
        app.dependency_overrides.clear()


def test_key_stored_encrypted_not_plaintext():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        plaintext = "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"
        client.post("/api/v1/profile/swipesmart", headers=hdr, json={"api_key": plaintext})

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "swk3@t.com"))
            profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
            assert profile is not None
            assert profile.swipesmart_api_key_encrypted is not None
            assert profile.swipesmart_api_key_encrypted != plaintext
    finally:
        app.dependency_overrides.clear()


def test_delete_key_clears_it():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk4@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        client.post(
            "/api/v1/profile/swipesmart", headers=hdr,
            json={"api_key": "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"},
        )
        r = client.delete("/api/v1/profile/swipesmart", headers=hdr)
        assert r.status_code == 204, r.text

        r2 = client.get("/api/v1/profile/swipesmart", headers=hdr)
        assert r2.json() == {"has_key": False, "masked": None, "auto_mapped": 0}
    finally:
        app.dependency_overrides.clear()


def test_list_cards_without_key_returns_400():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk5@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        r = client.get("/api/v1/profile/swipesmart/cards", headers=hdr)
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_list_cards_with_key_calls_swipesmart_client():
    client, TS = _make_client()
    try:
        tok = _login(client, "swk6@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        client.post(
            "/api/v1/profile/swipesmart", headers=hdr,
            json={"api_key": "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"},
        )
        fake_cards = [{"cardId": "CARD_A", "bankName": "測試銀行", "cardName": "測試卡"}]
        with patch(
            "src.services.swipesmart_client.get_cards",
            new=AsyncMock(return_value=fake_cards),
        ):
            r = client.get("/api/v1/profile/swipesmart/cards", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json() == [{"card_id": "CARD_A", "bank_name": "測試銀行", "card_name": "測試卡"}]
    finally:
        app.dependency_overrides.clear()
