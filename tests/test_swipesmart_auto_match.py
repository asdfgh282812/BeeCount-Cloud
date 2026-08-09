"""SwipeSmart 卡片自動比對(PH14 SD §3.3.1(b) 2026-08-09 修訂):名稱相近/相同
時自動寫入 `swipesmart_card_id`,不用每個使用者都手動一一勾選。涵蓋
`src/services/swipesmart_matching.py` 的純函式邏輯,以及
`POST /profile/swipesmart`(貼 Key 當下)/`GET /profile/swipesmart/cards`
(每次打開對照視窗)兩個觸發點。"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User, UserAccountProjection
from src.services.swipesmart_matching import _is_fuzzy_match, auto_match_unmapped_accounts

_FAKE_CARDS = [
    {"cardId": "CARD_CATHAY_CUBE", "bankName": "國泰", "cardName": "CUBE卡"},
    {"cardId": "CARD_CTBC_LINE", "bankName": "中信", "cardName": "LINE Pay卡"},
]


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
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": "upsert",
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post(
        "/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]},
    )
    assert r.status_code == 200, r.text


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# 純函式:比對邏輯                                                              #
# --------------------------------------------------------------------------- #


def test_fuzzy_match_hits_on_combined_bank_and_card_name():
    card = {"bankName": "國泰", "cardName": "CUBE卡"}
    assert _is_fuzzy_match("國泰CUBE卡", card) is True
    assert _is_fuzzy_match("我的國泰CUBE卡(現金回饋)", card) is True


def test_fuzzy_match_hits_on_card_name_alone():
    card = {"bankName": "國泰銀行", "cardName": "CUBE卡"}
    assert _is_fuzzy_match("CUBE卡", card) is True


def test_fuzzy_match_ignores_whitespace_and_case():
    card = {"bankName": "Cathay", "cardName": "Cube Card"}
    assert _is_fuzzy_match("  cathay cube card  ", card) is True


def test_fuzzy_match_rejects_unrelated_name():
    card = {"bankName": "國泰", "cardName": "CUBE卡"}
    assert _is_fuzzy_match("現金", card) is False


# --------------------------------------------------------------------------- #
# auto_match_unmapped_accounts:DB 層                                          #
# --------------------------------------------------------------------------- #


def test_auto_match_writes_unique_hit_and_skips_rest():
    client, TS = _make_client()
    try:
        tok = _login(client, "sam1@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-cube",
              {"syncId": "acc-cube", "name": "國泰CUBE卡", "type": "credit_card", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-noise",
              {"syncId": "acc-noise", "name": "隨便命名的信用卡", "type": "credit_card", "currency": "CNY"})

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "sam1@t.com"))
            matched = auto_match_unmapped_accounts(db, user_id=user_id, cards=_FAKE_CARDS)
            db.commit()
        assert matched == 1

        assert _account_row(TS, "sam1@t.com", "acc-cube").swipesmart_card_id == "CARD_CATHAY_CUBE"
        assert _account_row(TS, "sam1@t.com", "acc-cash").swipesmart_card_id is None
        assert _account_row(TS, "sam1@t.com", "acc-noise").swipesmart_card_id is None
    finally:
        app.dependency_overrides.clear()


def test_auto_match_skips_already_mapped_account():
    client, TS = _make_client()
    try:
        tok = _login(client, "sam2@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        _push(client, hdr, "lg1", "account", "acc-cube",
              {"syncId": "acc-cube", "name": "國泰CUBE卡", "type": "credit_card", "currency": "CNY",
               "swipesmartCardId": "MANUAL_CARD"})

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "sam2@t.com"))
            matched = auto_match_unmapped_accounts(db, user_id=user_id, cards=_FAKE_CARDS)
            db.commit()
        assert matched == 0
        assert _account_row(TS, "sam2@t.com", "acc-cube").swipesmart_card_id == "MANUAL_CARD"
    finally:
        app.dependency_overrides.clear()


def test_auto_match_skips_ambiguous_multi_hit():
    client, TS = _make_client()
    try:
        tok = _login(client, "sam3@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "lg1", "account", "acc-both",
              {"syncId": "acc-both", "name": "CUBE卡LINE Pay卡", "type": "credit_card", "currency": "CNY"})

        ambiguous_cards = [
            {"cardId": "A", "bankName": "", "cardName": "CUBE卡"},
            {"cardId": "B", "bankName": "", "cardName": "LINE Pay卡"},
        ]
        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "sam3@t.com"))
            matched = auto_match_unmapped_accounts(db, user_id=user_id, cards=ambiguous_cards)
            db.commit()
        assert matched == 0
        assert _account_row(TS, "sam3@t.com", "acc-both").swipesmart_card_id is None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# HTTP 觸發點                                                                  #
# --------------------------------------------------------------------------- #


def test_set_key_triggers_auto_match_and_reports_count():
    client, TS = _make_client()
    try:
        tok = _login(client, "sam4@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}
        _push(client, hdr, "lg1", "account", "acc-cube",
              {"syncId": "acc-cube", "name": "國泰CUBE卡", "type": "credit_card", "currency": "CNY"})

        with patch(
            "src.services.swipesmart_client.get_cards", new=AsyncMock(return_value=_FAKE_CARDS),
        ):
            r = client.post(
                "/api/v1/profile/swipesmart", headers=hdr,
                json={"api_key": "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"},
            )
        assert r.status_code == 200, r.text
        assert r.json()["auto_mapped"] == 1
        assert _account_row(TS, "sam4@t.com", "acc-cube").swipesmart_card_id == "CARD_CATHAY_CUBE"
    finally:
        app.dependency_overrides.clear()


def test_list_cards_triggers_auto_match_for_newly_added_account():
    """貼 Key 當下沒有信用卡帳戶;之後新增一張,重新打開對照視窗(呼叫
    `GET /profile/swipesmart/cards`)時應該也能補上自動比對 —— 不是只有貼
    Key 那一次會跑。"""
    client, TS = _make_client()
    try:
        tok = _login(client, "sam5@t.com")
        hdr = {"Authorization": f"Bearer {tok}"}

        with patch(
            "src.services.swipesmart_client.get_cards", new=AsyncMock(return_value=[]),
        ):
            r0 = client.post(
                "/api/v1/profile/swipesmart", headers=hdr,
                json={"api_key": "ssm_c0f27646be11aba6db4ab8478af60990a9996ccec0c9f871"},
            )
        assert r0.json()["auto_mapped"] == 0

        _push(client, hdr, "lg1", "account", "acc-line",
              {"syncId": "acc-line", "name": "中信LINE Pay卡", "type": "credit_card", "currency": "CNY"})

        with patch(
            "src.services.swipesmart_client.get_cards", new=AsyncMock(return_value=_FAKE_CARDS),
        ):
            r = client.get("/api/v1/profile/swipesmart/cards", headers=hdr)
        assert r.status_code == 200, r.text
        assert _account_row(TS, "sam5@t.com", "acc-line").swipesmart_card_id == "CARD_CTBC_LINE"
    finally:
        app.dependency_overrides.clear()
