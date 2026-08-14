"""account_group 底下跨幣別子帳戶的 balance rollup 契约测试(2026-08-12)。

根因:`list_workspace_accounts` 曾經把子帳戶的 balance/income_total/
expense_total 不分幣別直接 sum() 回填到 account_group 自己身上——19400 TWD +
10800 JPY 被直接相加成 30200,而不是先按匯率換算再相加。這裡驗證:
1. 有匯率(自動快取)時,群組合計是換算後的正確金額。
2. 沒有匯率(上游失敗 + 無手動 override)時,缺匯率的子帳戶被剔除、不裸加
   1.0,且 `balance_fx_incomplete` 標記為 True。
3. 只有手動 override、沒有自動快取時,override 本身就足夠算出正確合計。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User, UserExchangeRateProjection
from src.services.exchange_rate import fetcher


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


def _login(client, email, *, device_id, client_type):
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


def _two_tokens(client, email):
    app_token = _login(client, email, device_id="d-app", client_type="app")
    web_token = _login(client, email, device_id="d-web", client_type="web")
    return app_token, web_token


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d-app"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_group_with_children(client, hdr_app):
    """TWD 群組 + TWD 子帳戶(19400)+ JPY 子帳戶(10800),群組自己 initial=0。"""
    _push(client, hdr_app, "lg1", "ledger", "lg1",
          {"syncId": "lg1", "ledgerName": "账本", "currency": "TWD"})
    _push(client, hdr_app, "lg1", "account", "acc-group",
          {"syncId": "acc-group", "name": "永豐帳戶", "type": "account_group",
           "currency": "TWD", "initialBalance": 0.0})
    _push(client, hdr_app, "lg1", "account", "acc-twd",
          {"syncId": "acc-twd", "name": "永豐大戶", "type": "bank_card",
           "currency": "TWD", "initialBalance": 19400.0, "parentAccountId": "acc-group"})
    _push(client, hdr_app, "lg1", "account", "acc-jpy",
          {"syncId": "acc-jpy", "name": "永豐日幣", "type": "bank_card",
           "currency": "JPY", "initialBalance": 10800.0, "parentAccountId": "acc-group"})


@pytest.fixture(autouse=True)
def _clear_locks():
    """每个用例前后都清空 fetcher._locks,避免 asyncio.Lock 跨事件循环复用报错
    (同 test_exchange_rate_proxy.py 的既有防护)。"""
    fetcher._locks.clear()
    yield
    fetcher._locks.clear()


def test_account_group_rollup_converts_child_currency(monkeypatch):
    """自動快取有 TWD→JPY 匯率(1 TWD = 4 JPY)時,群組合計要用換算後金額:
    19400 + 10800/4 = 22100,不是裸加的 30200。"""
    async def _fake_upstream(base: str):
        assert base == "TWD"
        return "2026-08-12", "fawazahmed0", {"JPY": "4.0"}

    monkeypatch.setattr(fetcher, "fetch_upstream", _fake_upstream)

    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "fxroll1@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _seed_group_with_children(client, hdr_app)

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        by_id = {a["id"]: a for a in r.json()}

        group = by_id["acc-group"]
        assert group["balance"] == pytest.approx(22100.0)
        assert group["balance_fx_incomplete"] is False
        # 子帳戶自己的 balance 維持原幣金額,不受群組換算影響。
        assert by_id["acc-twd"]["balance"] == pytest.approx(19400.0)
        assert by_id["acc-jpy"]["balance"] == pytest.approx(10800.0)
    finally:
        app.dependency_overrides.clear()


def test_account_group_rollup_excludes_child_without_rate(monkeypatch):
    """上游失敗且無任何快取/手動 override 時:JPY 子帳戶整筆剔除、不裸加
    1.0,群組合計只剩 TWD 子帳戶的 19400,並標記 balance_fx_incomplete=True。"""
    async def _fail_upstream(base: str):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(fetcher, "fetch_upstream", _fail_upstream)

    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "fxroll2@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _seed_group_with_children(client, hdr_app)

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        group = {a["id"]: a for a in r.json()}["acc-group"]
        assert group["balance"] == pytest.approx(19400.0)
        assert group["balance_fx_incomplete"] is True
    finally:
        app.dependency_overrides.clear()


def test_account_group_rollup_uses_manual_override_without_cache(monkeypatch):
    """沒有自動快取(上游失敗),但使用者手動設了 TWD/JPY 匯率 override
    (1 JPY = 0.25 TWD)時,override 本身就足夠算出正確合計,且不算 incomplete。"""
    async def _fail_upstream(base: str):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(fetcher, "fetch_upstream", _fail_upstream)

    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "fxroll3@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _seed_group_with_children(client, hdr_app)

        with TS() as db:
            uid = db.query(User).filter(User.email == "fxroll3@t.com").first().id
            db.add(UserExchangeRateProjection(
                user_id=uid, sync_id="rate-jpy", base_currency="TWD",
                quote_currency="JPY", rate="0.25"))
            db.commit()

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        group = {a["id"]: a for a in r.json()}["acc-group"]
        assert group["balance"] == pytest.approx(22100.0)
        assert group["balance_fx_incomplete"] is False
    finally:
        app.dependency_overrides.clear()


def test_account_group_rollup_excludes_child_with_include_in_total_false():
    """2026-08-14 生產環境回報:子帳戶關掉「納入總餘額」開關後,總資產卡完全
    沒反應。根因:群組合計不分子帳戶自己的 include_in_total 一律照加,前端
    只把子帳戶自己那一列從加總剔除(避免跟群組重複計),但群組那一列的
    balance 早就已經含了這個子帳戶的錢——兩邊互不知道對方的存在,關掉開關
    變成完全沒作用。這裡驗證:acc-b 關閉 include_in_total 後,群組合計只剩
    acc-a 的 1000,不再含 acc-b 的 2000;income_total/expense_total(帳戶
    自身統計顯示用,不是總餘額語意)不受這個開關影響,維持全額回填。"""
    client, TS = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "fxroll5@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr_app, "lg1", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group",
               "currency": "CNY", "initialBalance": 0.0})
        _push(client, hdr_app, "lg1", "account", "acc-a",
              {"syncId": "acc-a", "name": "卡A", "type": "bank_card",
               "currency": "CNY", "initialBalance": 1000.0, "parentAccountId": "acc-group"})
        _push(client, hdr_app, "lg1", "account", "acc-b",
              {"syncId": "acc-b", "name": "卡B", "type": "bank_card",
               "currency": "CNY", "initialBalance": 2000.0, "parentAccountId": "acc-group",
               "includeInTotal": False})

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        by_id = {a["id"]: a for a in r.json()}
        group = by_id["acc-group"]
        assert group["balance"] == pytest.approx(1000.0), \
            "關閉 include_in_total 的子帳戶不應計入群組合計"
        assert group["balance_fx_incomplete"] is False
        # 子帳戶自己的 balance 顯示不受影響(該開關只影響「加總」語意)。
        assert by_id["acc-b"]["balance"] == pytest.approx(2000.0)
        assert by_id["acc-b"]["include_in_total"] is False
    finally:
        app.dependency_overrides.clear()


def test_account_group_single_currency_unaffected():
    """群組跟子帳戶幣別完全一致(常見情況)時,rollup 邏輯完全不觸發匯率分支,
    合計維持原本的直接相加(1000+2000=3000),不因這次改動受影響。"""
    client, _ = _make_client()
    try:
        app_token, web_token = _two_tokens(client, "fxroll4@t.com")
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        hdr_web = {"Authorization": f"Bearer {web_token}"}

        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr_app, "lg1", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group",
               "currency": "CNY", "initialBalance": 0.0})
        _push(client, hdr_app, "lg1", "account", "acc-a",
              {"syncId": "acc-a", "name": "卡A", "type": "bank_card",
               "currency": "CNY", "initialBalance": 1000.0, "parentAccountId": "acc-group"})
        _push(client, hdr_app, "lg1", "account", "acc-b",
              {"syncId": "acc-b", "name": "卡B", "type": "bank_card",
               "currency": "CNY", "initialBalance": 2000.0, "parentAccountId": "acc-group"})

        r = client.get("/api/v1/read/workspace/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        group = {a["id"]: a for a in r.json()}["acc-group"]
        assert group["balance"] == pytest.approx(3000.0)
        assert group["balance_fx_incomplete"] is False
    finally:
        app.dependency_overrides.clear()
