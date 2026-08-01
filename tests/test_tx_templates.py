"""交易範本(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)—— tx_template entity 契约:

- `POST/PATCH/DELETE /write/ledgers/{id}/tx-templates`:一般 CRUD,跟
  budget/tag 同款 boilerplate。
- `POST /write/ledgers/{id}/tx-templates/{id}/apply`:把範本内容套成一笔新
  交易,`amount`/`note` 可选择性覆盖範本预设值,其余栏位(category/account/
  tx_type)固定沿用範本。
- mobile `/sync/push` 的 `tx_template` merge 契约(partial update 保留旧值)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxTemplateProjection


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


def _register(client, email, client_type="app", device_id="d1"):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
            "device_id": device_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _login_web(client, email):
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _seed_ledger(client, token, device_id, ledger_id):
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    r = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [{
                "ledger_id": ledger_id,
                "entity_type": "ledger_snapshot",
                "entity_sync_id": ledger_id,
                "action": "upsert",
                "payload": {"content": content},
                "updated_at": _iso(),
            }],
        },
    )
    assert r.status_code == 200, r.text


def _latest_change_id(client, token, ledger_id):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": device_id, "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _create_category(client, hdr, ledger_id, token, name, kind="expense"):
    base = _latest_change_id(client, token, ledger_id)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/categories",
        headers=hdr,
        json={"base_change_id": base, "name": name, "kind": kind, "level": 1},
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/categories", headers=hdr)
    assert r.status_code == 200
    return next(c["id"] for c in r.json() if c["name"] == name)


def _create_account(client, hdr, ledger_id, token, name):
    base = _latest_change_id(client, token, ledger_id)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts",
        headers=hdr,
        json={"base_change_id": base, "name": name, "account_type": "cash", "currency": "CNY"},
    )
    assert r.status_code == 200, r.text
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/accounts", headers=hdr)
    assert r.status_code == 200
    return next(a["id"] for a in r.json() if a["name"] == name)


def _setup(email, ledger_id="L_TPL1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    cat_food = _create_category(client, hdr, ledger_id, token, "餐饮")
    acc_cash = _create_account(client, hdr, ledger_id, token, "现金")
    return client, TS, token, hdr, ledger_id, cat_food, acc_cash


def _create_template(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": "早餐",
        "tx_type": "expense",
        "amount": 15.0,
    }
    payload.update(overrides)
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/tx-templates", headers=hdr, json=payload)


def _templates(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/tx-templates", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def _transactions(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr, params={"limit": 500})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_template_and_list_with_display_names():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl1@example.com")
    try:
        res = _create_template(
            client, hdr, ledger_id, token,
            name="早餐", amount=15.0, category_id=cat_food, account_id=acc_cash,
            note="豆浆油条",
        )
        assert res.status_code == 200, res.text
        template_id = res.json()["entity_id"]

        templates = _templates(client, hdr, ledger_id)
        assert len(templates) == 1
        t = templates[0]
        assert t["id"] == template_id
        assert t["name"] == "早餐"
        assert t["amount"] == 15.0
        assert t["category_id"] == cat_food
        assert t["category_name"] == "餐饮"
        assert t["account_id"] == acc_cash
        assert t["account_name"] == "现金"
        assert t["note"] == "豆浆油条"
    finally:
        client.close()


def test_update_template_fields():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl2@example.com")
    try:
        res = _create_template(client, hdr, ledger_id, token)
        template_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        upd = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/tx-templates/{template_id}",
            headers=hdr,
            json={"base_change_id": base, "amount": 20.0, "name": "豪华早餐"},
        )
        assert upd.status_code == 200, upd.text

        t = _templates(client, hdr, ledger_id)[0]
        assert t["amount"] == 20.0
        assert t["name"] == "豪华早餐"
    finally:
        client.close()


def test_delete_template():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl3@example.com")
    try:
        res = _create_template(client, hdr, ledger_id, token)
        template_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        deleted = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/tx-templates/{template_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert deleted.status_code == 200, deleted.text
        assert _templates(client, hdr, ledger_id) == []
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_template_creates_transaction_with_template_fields():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl4@example.com")
    try:
        res = _create_template(
            client, hdr, ledger_id, token,
            name="早餐", amount=15.0, category_id=cat_food, account_id=acc_cash,
            note="豆浆油条",
        )
        template_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        happened_at = datetime.now(timezone.utc)
        apply_res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/tx-templates/{template_id}/apply",
            headers=hdr,
            json={"base_change_id": base, "happened_at": happened_at.isoformat()},
        )
        assert apply_res.status_code == 200, apply_res.text
        tx_id = apply_res.json()["entity_id"]

        txs = _transactions(client, hdr, ledger_id)
        assert len(txs) == 1
        tx = txs[0]
        assert tx["id"] == tx_id
        assert tx["tx_type"] == "expense"
        assert tx["amount"] == 15.0
        assert tx["category_id"] == cat_food
        assert tx["account_id"] == acc_cash
        assert tx["note"] == "豆浆油条"
    finally:
        client.close()


def test_apply_template_allows_overriding_amount_and_note():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl5@example.com")
    try:
        res = _create_template(
            client, hdr, ledger_id, token,
            name="早餐", amount=15.0, category_id=cat_food, account_id=acc_cash,
        )
        template_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        apply_res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/tx-templates/{template_id}/apply",
            headers=hdr,
            json={
                "base_change_id": base,
                "happened_at": _iso(),
                "amount": 25.5,
                "note": "今天加了培根",
            },
        )
        assert apply_res.status_code == 200, apply_res.text

        tx = _transactions(client, hdr, ledger_id)[0]
        assert tx["amount"] == 25.5
        assert tx["note"] == "今天加了培根"
    finally:
        client.close()


def test_apply_unknown_template_returns_404():
    client, _TS, token, hdr, ledger_id, cat_food, acc_cash = _setup("tpl6@example.com")
    try:
        base = _latest_change_id(client, token, ledger_id)
        apply_res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/tx-templates/tpl_missing/apply",
            headers=hdr,
            json={"base_change_id": base, "happened_at": _iso()},
        )
        assert apply_res.status_code == 404, apply_res.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_tx_template_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        owner = _register(client, "tpl7@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_TPL7"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "tpl_manual1"
        _push(client, hdr, ledger_id, "tx_template", sync_id, {
            "syncId": sync_id,
            "name": "咖啡",
            "txType": "expense",
            "amount": 30.0,
            "note": "美式",
        }, device_id=device)

        # 只带 amount,其它字段应保留
        _push(client, hdr, ledger_id, "tx_template", sync_id, {
            "syncId": sync_id,
            "amount": 35.0,
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadTxTemplateProjection).where(ReadTxTemplateProjection.sync_id == sync_id)
            )
            assert row is not None
            assert row.name == "咖啡"
            assert row.note == "美式"
            assert row.amount == 35.0
        finally:
            db.close()
    finally:
        client.close()


def test_template_scoped_to_own_ledger_only():
    client, _TS = _make_client()
    try:
        owner = _register(client, "tpl8@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, app_token, device, "L_TPLA")
        _seed_ledger(client, app_token, device, "L_TPLB")
        web = _login_web(client, "tpl8@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _create_template(client, hdr, "L_TPLA", token, name="A範本")
        _create_template(client, hdr, "L_TPLB", token, name="B範本")

        tpl_a = _templates(client, hdr, "L_TPLA")
        tpl_b = _templates(client, hdr, "L_TPLB")
        assert len(tpl_a) == 1 and tpl_a[0]["name"] == "A範本"
        assert len(tpl_b) == 1 and tpl_b[0]["name"] == "B範本"
    finally:
        client.close()
