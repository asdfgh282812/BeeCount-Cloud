"""訂金尾款追蹤(doc.moze.app/record/deposit)—— deposit_balance entity 契約:

- `POST/PATCH/DELETE /write/ledgers/{id}/deposit-balances`:替一筆訂金交易
  (支出/轉帳)登記尾款;amount/due_at 可為 null(未定),補上前可改。
- `POST .../{id}/complete`:同一次寫入建立真實交易(類型/類別/專案/商家沿用
  訂金交易)並把尾款翻成 completed。
- 尾款補上前不是交易,所以不進任何統計(read_tx_projection 查不到)。
- 刪訂金 → 連帶刪尾款;刪已補上的尾款交易 → 連帶刪 balance。
- mobile `/sync/push` 的 `deposit_balance` entity:partial update 缺鍵保留
  舊值、wire key 契約(sourceTxId/amount/dueAt/status/completedTxId)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadDepositBalanceProjection, ReadTxProjection


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


def _register(client, email, device_id="d1"):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
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
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={
            "device_id": device_id,
            "changes": [{
                "ledger_id": ledger_id,
                "entity_type": entity_type,
                "entity_sync_id": sync_id,
                "action": action,
                "updated_at": _iso(),
                "payload": payload,
            }],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _setup(email, ledger_id="L_DEP1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    return client, TS, token, {"Authorization": f"Bearer {token}"}, ledger_id


def _create_tx(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "tx_type": "expense",
        "amount": 1000.0,
        "happened_at": datetime.now(timezone.utc).isoformat(),
        "merchant": "海景飯店",
    }
    payload.update(overrides)
    r = client.post(f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr, json=payload)
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _create_balance(client, hdr, ledger_id, token, source_tx_id, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {"base_change_id": base, "source_tx_id": source_tx_id}
    payload.update(overrides)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/deposit-balances", headers=hdr, json=payload,
    )


def _balances(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/deposit-balances", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


def _tx_rows(TS, ledger_external_id=None):
    db = TS()
    try:
        return list(db.scalars(select(ReadTxProjection)).all())
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_balance_with_amount_and_due_date_is_pending_and_not_a_transaction():
    client, TS, token, hdr, ledger_id = _setup("dep1@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        res = _create_balance(
            client, hdr, ledger_id, token, tx_id,
            amount=5000.0, due_at="2026-10-17T13:45:00+08:00",
        )
        assert res.status_code == 200, res.text
        balance_id = res.json()["entity_id"]

        balances = _balances(client, hdr, ledger_id)
        assert len(balances) == 1
        b = balances[0]
        assert b["id"] == balance_id
        assert b["source_tx_id"] == tx_id
        assert b["amount"] == 5000.0
        assert b["status"] == "pending"
        assert b["completed_tx_id"] is None
        # 純日期:時分被截掉
        assert b["due_at"].startswith("2026-10-17T05:45") is False
        assert b["due_at"][:10] in {"2026-10-17", "2026-10-16"}
        assert b["source_amount"] == 1000.0
        assert b["source_merchant"] == "海景飯店"
        # 尾款不是交易:只有訂金那一筆
        assert len(_tx_rows(TS)) == 1
    finally:
        client.close()


def test_create_balance_with_undetermined_amount_and_date():
    client, _TS, token, hdr, ledger_id = _setup("dep2@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token, amount=500.0)
        res = _create_balance(client, hdr, ledger_id, token, tx_id)
        assert res.status_code == 200, res.text
        b = _balances(client, hdr, ledger_id)[0]
        assert b["amount"] is None
        assert b["due_at"] is None
    finally:
        client.close()


def test_one_balance_per_source_tx_and_income_is_rejected():
    client, _TS, token, hdr, ledger_id = _setup("dep3@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        assert _create_balance(client, hdr, ledger_id, token, tx_id, amount=10.0).status_code == 200
        dup = _create_balance(client, hdr, ledger_id, token, tx_id, amount=20.0)
        assert dup.status_code == 400, dup.text

        income_id = _create_tx(client, hdr, ledger_id, token, tx_type="income")
        bad = _create_balance(client, hdr, ledger_id, token, income_id, amount=10.0)
        assert bad.status_code == 400, bad.text
    finally:
        client.close()


def test_patch_balance_can_fill_and_clear_amount_and_due():
    client, _TS, token, hdr, ledger_id = _setup("dep4@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        balance_id = _create_balance(client, hdr, ledger_id, token, tx_id).json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        r = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}",
            headers=hdr,
            json={"base_change_id": base, "amount": 2780.0, "due_at": "2026-11-01T00:00:00Z"},
        )
        assert r.status_code == 200, r.text
        b = _balances(client, hdr, ledger_id)[0]
        assert b["amount"] == 2780.0
        assert b["due_at"] is not None

        base = _latest_change_id(client, token, ledger_id)
        r = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}",
            headers=hdr,
            json={"base_change_id": base, "due_at": None},
        )
        assert r.status_code == 200, r.text
        b = _balances(client, hdr, ledger_id)[0]
        assert b["due_at"] is None
        assert b["amount"] == 2780.0  # 沒帶的 key 不動
    finally:
        client.close()


def test_delete_pending_balance_keeps_deposit_transaction():
    client, TS, token, hdr, ledger_id = _setup("dep5@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        balance_id = _create_balance(client, hdr, ledger_id, token, tx_id, amount=1.0).json()["entity_id"]
        base = _latest_change_id(client, token, ledger_id)
        r = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert r.status_code == 200, r.text
        assert _balances(client, hdr, ledger_id) == []
        assert len(_tx_rows(TS)) == 1  # 訂金保留為一般支出
    finally:
        client.close()


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------


def test_complete_creates_real_transaction_inheriting_source_fields():
    client, TS, token, hdr, ledger_id = _setup("dep6@example.com")
    try:
        tx_id = _create_tx(
            client, hdr, ledger_id, token,
            category_name="住宿", category_kind="expense", category_id="cat_stay",
        )
        balance_id = _create_balance(
            client, hdr, ledger_id, token, tx_id, amount=5000.0, due_at="2026-10-17T00:00:00Z",
        ).json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}/complete",
            headers=hdr,
            json={"base_change_id": base, "amount": 2780.0},
        )
        assert r.status_code == 200, r.text

        b = _balances(client, hdr, ledger_id)[0]
        assert b["status"] == "completed"
        assert b["completed_tx_id"]
        assert b["amount"] == 2780.0

        rows = _tx_rows(TS)
        assert len(rows) == 2
        new_tx = next(t for t in rows if t.sync_id == b["completed_tx_id"])
        assert new_tx.amount == 2780.0
        assert new_tx.tx_type == "expense"
        assert new_tx.merchant == "海景飯店"
        assert new_tx.category_sync_id == "cat_stay"
    finally:
        client.close()


def test_complete_requires_an_amount_and_cannot_run_twice():
    client, _TS, token, hdr, ledger_id = _setup("dep7@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        balance_id = _create_balance(client, hdr, ledger_id, token, tx_id).json()["entity_id"]  # 金額未定

        base = _latest_change_id(client, token, ledger_id)
        url = f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}/complete"
        r = client.post(url, headers=hdr, json={"base_change_id": base})
        assert r.status_code == 400, r.text

        r = client.post(url, headers=hdr, json={"base_change_id": base, "amount": 3280.0 - 1000.0})
        assert r.status_code == 200, r.text
        base = _latest_change_id(client, token, ledger_id)
        r = client.post(url, headers=hdr, json={"base_change_id": base, "amount": 1.0})
        assert r.status_code == 400, r.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 刪除連動
# ---------------------------------------------------------------------------


def _delete_tx(client, hdr, ledger_id, token, tx_id):
    base = _latest_change_id(client, token, ledger_id)
    return client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
        headers=hdr,
        json={"base_change_id": base},
    )


def test_deleting_deposit_transaction_deletes_pending_balance():
    client, _TS, token, hdr, ledger_id = _setup("dep8@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        _create_balance(client, hdr, ledger_id, token, tx_id, amount=5000.0)
        r = _delete_tx(client, hdr, ledger_id, token, tx_id)
        assert r.status_code == 200, r.text
        assert _balances(client, hdr, ledger_id) == []
    finally:
        client.close()


def test_deleting_completed_balance_transaction_deletes_balance_row():
    client, _TS, token, hdr, ledger_id = _setup("dep9@example.com")
    try:
        tx_id = _create_tx(client, hdr, ledger_id, token)
        balance_id = _create_balance(client, hdr, ledger_id, token, tx_id, amount=5000.0).json()["entity_id"]
        base = _latest_change_id(client, token, ledger_id)
        client.post(
            f"/api/v1/write/ledgers/{ledger_id}/deposit-balances/{balance_id}/complete",
            headers=hdr, json={"base_change_id": base},
        )
        completed_tx_id = _balances(client, hdr, ledger_id)[0]["completed_tx_id"]
        r = _delete_tx(client, hdr, ledger_id, token, completed_tx_id)
        assert r.status_code == 200, r.text
        assert _balances(client, hdr, ledger_id) == []
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契約
# ---------------------------------------------------------------------------


def test_mobile_push_deposit_balance_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        owner = _register(client, "dep10@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEP10"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "depbal_manual1"
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {
            "syncId": sync_id,
            "sourceTxId": "tx_src1",
            "amount": 5000.0,
            "dueAt": "2026-10-17T00:00:00Z",
            "status": "pending",
        }, device_id=device)

        # 只帶 amount,其它字段應保留
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {
            "syncId": sync_id,
            "amount": 4800.0,
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadDepositBalanceProjection).where(
                    ReadDepositBalanceProjection.sync_id == sync_id
                )
            )
            assert row is not None
            assert row.source_tx_sync_id == "tx_src1"
            assert row.amount == 4800.0
            assert row.due_at is not None
            assert row.status == "pending"
        finally:
            db.close()
    finally:
        client.close()


def test_mobile_push_explicit_null_clears_due_and_amount():
    """App 恆發 amount/dueAt/completedTxId(null 也送),null 必須能清空欄位。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "dep11@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEP11"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "depbal_manual2"
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {
            "syncId": sync_id, "sourceTxId": "tx_src2", "amount": 100.0,
            "dueAt": "2026-10-17T00:00:00Z", "status": "pending", "completedTxId": None,
        }, device_id=device)
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {
            "syncId": sync_id, "sourceTxId": "tx_src2", "amount": None,
            "dueAt": None, "status": "pending", "completedTxId": None,
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadDepositBalanceProjection).where(
                    ReadDepositBalanceProjection.sync_id == sync_id
                )
            )
            assert row is not None
            assert row.amount is None
            assert row.due_at is None
        finally:
            db.close()
    finally:
        client.close()


def test_mobile_push_delete_removes_balance():
    client, TS = _make_client()
    try:
        owner = _register(client, "dep12@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEP12"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}
        sync_id = "depbal_manual3"
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {
            "syncId": sync_id, "sourceTxId": "tx_src3", "status": "pending",
        }, device_id=device)
        _push(client, hdr, ledger_id, "deposit_balance", sync_id, {}, device_id=device, action="delete")
        db = TS()
        try:
            assert db.scalar(
                select(ReadDepositBalanceProjection).where(
                    ReadDepositBalanceProjection.sync_id == sync_id
                )
            ) is None
        finally:
            db.close()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 到期提醒(services.deposit_balance_reminders,不經過 HTTP)
# ---------------------------------------------------------------------------


def test_due_reminder_sent_once_and_skips_undated_or_completed():
    from datetime import timedelta

    from src.models import Notification
    from src.services import deposit_balance_reminders

    client, TS, token, hdr, ledger_id = _setup("dep13@example.com")
    try:
        overdue_tx = _create_tx(client, hdr, ledger_id, token, merchant="逾期餐廳")
        _create_balance(
            client, hdr, ledger_id, token, overdue_tx,
            amount=800.0,
            due_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        )
        undated_tx = _create_tx(client, hdr, ledger_id, token, merchant="未定餐廳")
        _create_balance(client, hdr, ledger_id, token, undated_tx)  # 日期未定

        db = TS()
        try:
            sent = deposit_balance_reminders.send_due_deposit_balance_reminders(db)
            db.commit()
            assert sent == 1
            # 同一筆不重複提醒
            assert deposit_balance_reminders.send_due_deposit_balance_reminders(db) == 0
            notes = db.scalars(select(Notification)).all()
            assert len(notes) == 1
            assert "逾期餐廳" in notes[0].title
            assert notes[0].payload_json["depositBalanceId"]
        finally:
            db.close()
    finally:
        client.close()
