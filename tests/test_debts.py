"""借還款追蹤(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3)—— debt entity 契约:

- `POST/PATCH/DELETE /write/ledgers/{id}/debts`:`principal_amount`/
  `direction` 建立后不可改,PATCH 只暴露 counterparty_name/due_at/note。
- `remaining_amount`/`status` 不落库,`GET /read/ledgers/{id}/debts` 从
  `read_tx_projection.debt_sync_id` 反查交易即时汇总算出(见
  `ReadDebtProjection` docstring)。
- 一笔交易关联 `debt_id`(建/改交易时传入)会被记成一次还款/收款,允许多笔
  部分还款;`debt_id` 必须指向该账本下已存在的欠款,否则 400。
- DELETE 只允许在还没有任何还款交易时执行。
- mobile `/sync/push` 的 `debt` merge 契约(partial update 保留旧值)+
  `transaction` entity 的 `debtId` 反查字段同款保留语义。

============================================================================
手动检查清单(pytest 测不到的运行时行为):

1. `POST /api/v1/internal/tasks/materialize-recurring`(admin token)手动
   触发一次,确认返回体里 `debt_reminders` 计数在有欠款临近到期时 > 0。
2. `GET /api/v1/notifications?category=reminder` 应该能看到欠款到期提醒。
============================================================================
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadDebtProjection, ReadTxProjection
from src.services import debt_reminders


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


def _setup(email, ledger_id="L_DEBT1"):
    client, TS = _make_client()
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    web = _login_web(client, email)
    token = web["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    return client, TS, token, hdr, ledger_id


def _create_debt(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "direction": "payable",
        "counterparty_name": "小明",
        "principal_amount": 1000.0,
    }
    payload.update(overrides)
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/debts", headers=hdr, json=payload)


def _create_tx(client, hdr, ledger_id, token, **overrides):
    base = _latest_change_id(client, token, ledger_id)
    now = datetime.now(timezone.utc)
    payload = {
        "base_change_id": base,
        "tx_type": "expense",
        "amount": 200.0,
        "happened_at": now.isoformat(),
    }
    payload.update(overrides)
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=hdr, json=payload)


def _debts(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/debts", headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# CRUD + derived remaining/status
# ---------------------------------------------------------------------------


def test_create_debt_and_list_defaults_to_open():
    client, _TS, token, hdr, ledger_id = _setup("debt1@example.com")
    try:
        res = _create_debt(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text
        debt_id = res.json()["entity_id"]

        debts = _debts(client, hdr, ledger_id)
        assert len(debts) == 1
        d = debts[0]
        assert d["id"] == debt_id
        assert d["direction"] == "payable"
        assert d["counterparty_name"] == "小明"
        assert d["principal_amount"] == 1000.0
        assert d["remaining_amount"] == 1000.0
        assert d["status"] == "open"
        assert d["repayments"] == []
    finally:
        client.close()


def test_repayment_transaction_reduces_remaining_and_marks_partial_then_settled():
    client, _TS, token, hdr, ledger_id = _setup("debt2@example.com")
    try:
        res = _create_debt(client, hdr, ledger_id, token, principal_amount=1000.0)
        debt_id = res.json()["entity_id"]

        tx_res = _create_tx(client, hdr, ledger_id, token, amount=400.0, debt_id=debt_id)
        assert tx_res.status_code == 200, tx_res.text
        tx_id = tx_res.json()["entity_id"]

        debts = _debts(client, hdr, ledger_id)
        d = debts[0]
        assert d["remaining_amount"] == 600.0
        assert d["status"] == "partial"
        assert len(d["repayments"]) == 1
        assert d["repayments"][0]["id"] == tx_id
        assert d["repayments"][0]["amount"] == 400.0

        # 第二笔部分还款,刚好还清
        tx_res2 = _create_tx(client, hdr, ledger_id, token, amount=600.0, debt_id=debt_id)
        assert tx_res2.status_code == 200, tx_res2.text

        debts = _debts(client, hdr, ledger_id)
        d = debts[0]
        assert d["remaining_amount"] == 0.0
        assert d["status"] == "settled"
        assert len(d["repayments"]) == 2
    finally:
        client.close()


def test_repayment_with_unknown_debt_id_rejected():
    client, _TS, token, hdr, ledger_id = _setup("debt3@example.com")
    try:
        res = _create_tx(client, hdr, ledger_id, token, debt_id="debt_does_not_exist")
        assert res.status_code == 400, res.text
    finally:
        client.close()


def test_update_debt_note_and_due_at():
    client, _TS, token, hdr, ledger_id = _setup("debt4@example.com")
    try:
        res = _create_debt(client, hdr, ledger_id, token)
        debt_id = res.json()["entity_id"]
        due = datetime.now(timezone.utc) + timedelta(days=30)

        base = _latest_change_id(client, token, ledger_id)
        upd = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
            headers=hdr,
            json={"base_change_id": base, "note": "过年借的", "due_at": due.isoformat()},
        )
        assert upd.status_code == 200, upd.text

        d = _debts(client, hdr, ledger_id)[0]
        assert d["note"] == "过年借的"
        assert d["due_at"] is not None
    finally:
        client.close()


def test_delete_debt_blocked_when_has_repayments_but_allowed_when_clean():
    client, _TS, token, hdr, ledger_id = _setup("debt5@example.com")
    try:
        res = _create_debt(client, hdr, ledger_id, token)
        debt_id = res.json()["entity_id"]
        _create_tx(client, hdr, ledger_id, token, amount=100.0, debt_id=debt_id)

        base = _latest_change_id(client, token, ledger_id)
        blocked = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert blocked.status_code == 400, blocked.text

        # 没有还款记录的欠款可以正常删除
        res2 = _create_debt(client, hdr, ledger_id, token, counterparty_name="小华")
        debt_id2 = res2.json()["entity_id"]
        base2 = _latest_change_id(client, token, ledger_id)
        ok = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id2}",
            headers=hdr,
            json={"base_change_id": base2},
        )
        assert ok.status_code == 200, ok.text
        remaining_ids = {d["id"] for d in _debts(client, hdr, ledger_id)}
        assert debt_id2 not in remaining_ids
        assert debt_id in remaining_ids
    finally:
        client.close()


def test_debt_scoped_to_own_ledger_only():
    client, _TS = _make_client()
    try:
        owner = _register(client, "debt6@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, app_token, device, "L_A")
        _seed_ledger(client, app_token, device, "L_B")
        web = _login_web(client, "debt6@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        _create_debt(client, hdr, "L_A", token, counterparty_name="A的债")
        _create_debt(client, hdr, "L_B", token, counterparty_name="B的债")

        debts_a = _debts(client, hdr, "L_A")
        debts_b = _debts(client, hdr, "L_B")
        assert len(debts_a) == 1 and debts_a[0]["counterparty_name"] == "A的债"
        assert len(debts_b) == 1 and debts_b[0]["counterparty_name"] == "B的债"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_debt_partial_update_keeps_existing_fields():
    client, TS = _make_client()
    try:
        owner = _register(client, "debt7@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEBT7"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        sync_id = "debt_manual1"
        _push(client, hdr, ledger_id, "debt", sync_id, {
            "syncId": sync_id,
            "direction": "receivable",
            "counterpartyName": "小美",
            "principalAmount": 500.0,
            "note": "借款给小美",
        }, device_id=device)

        # 只带 note,其它字段应保留
        _push(client, hdr, ledger_id, "debt", sync_id, {
            "syncId": sync_id,
            "note": "已提醒",
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(
                select(ReadDebtProjection).where(ReadDebtProjection.sync_id == sync_id)
            )
            assert row is not None
            assert row.direction == "receivable"
            assert row.counterparty_name == "小美"
            assert row.principal_amount == 500.0
            assert row.note == "已提醒"
        finally:
            db.close()
    finally:
        client.close()


def test_mobile_push_transaction_debt_id_roundtrip():
    """`debtId` 是 transaction merge spec 的一个反查字段,partial update 缺键
    要保留旧值(跟 refundOfId/installmentPlanId 同款契约)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "debt8@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEBT8"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr = {"Authorization": f"Bearer {app_token}"}

        _push(client, hdr, ledger_id, "debt", "debt_x1", {
            "syncId": "debt_x1", "direction": "payable",
            "counterpartyName": "老王", "principalAmount": 300.0,
        }, device_id=device)

        tx_id = "tx_debt1"
        _push(client, hdr, ledger_id, "transaction", tx_id, {
            "syncId": tx_id, "type": "expense", "amount": 100.0,
            "happenedAt": _iso(), "debtId": "debt_x1",
        }, device_id=device)

        # 只改 note,不带 debtId,应保留原关联
        _push(client, hdr, ledger_id, "transaction", tx_id, {
            "syncId": tx_id, "note": "还款备注",
        }, device_id=device)

        db = TS()
        try:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row is not None
            assert row.debt_sync_id == "debt_x1"
            assert row.note == "还款备注"
        finally:
            db.close()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 到期提醒(services.debt_reminders,不经过 HTTP)
# ---------------------------------------------------------------------------


def test_send_due_debt_reminders_creates_notification_once():
    client, TS = _make_client()
    try:
        owner = _register(client, "debt9@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEBT9"
        _seed_ledger(client, app_token, device, ledger_id)
        web = _login_web(client, "debt9@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        due = datetime.now(timezone.utc) + timedelta(days=1)
        res = _create_debt(client, hdr, ledger_id, token, due_at=due.isoformat())
        assert res.status_code == 200, res.text

        db = TS()
        try:
            sent = debt_reminders.send_due_debt_reminders(db)
            db.commit()
            assert sent == 1
            # 第二次跑不应该重复提醒
            sent_again = debt_reminders.send_due_debt_reminders(db)
            db.commit()
            assert sent_again == 0
        finally:
            db.close()

        notes = client.get("/api/v1/notifications", headers=hdr, params={"category": "reminder"})
        assert notes.status_code == 200, notes.text
        assert notes.json()["total"] == 1
    finally:
        client.close()


def test_close_and_reopen_debt_overrides_status():
    """結案(closed_at)體驗補強:即使還沒還清也能手動結案,狀態蓋過
    remaining_amount 算出來的 open/partial/settled;傳 null 可重新開啟。"""
    client, _TS, token, hdr, ledger_id = _setup("debt11@example.com")
    try:
        res = _create_debt(client, hdr, ledger_id, token, principal_amount=1000.0)
        debt_id = res.json()["entity_id"]
        _create_tx(client, hdr, ledger_id, token, amount=300.0, debt_id=debt_id)

        d = _debts(client, hdr, ledger_id)[0]
        assert d["status"] == "partial"
        assert d["closed_at"] is None

        base = _latest_change_id(client, token, ledger_id)
        close = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
            headers=hdr,
            json={"base_change_id": base, "closed_at": _iso()},
        )
        assert close.status_code == 200, close.text

        d = _debts(client, hdr, ledger_id)[0]
        assert d["status"] == "closed"
        assert d["closed_at"] is not None
        # 尚未清偿的余额继续如实反映,结案不代表金额归零
        assert d["remaining_amount"] == 700.0

        base2 = _latest_change_id(client, token, ledger_id)
        reopen = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
            headers=hdr,
            json={"base_change_id": base2, "closed_at": None},
        )
        assert reopen.status_code == 200, reopen.text
        d = _debts(client, hdr, ledger_id)[0]
        assert d["status"] == "partial"
        assert d["closed_at"] is None
    finally:
        client.close()


def test_due_at_truncated_to_date_only():
    """到期日只存日期(體驗補強):送帶時分的 ISO 時間,落庫後讀出來應該
    是當天 UTC 零點,不保留原本的時分。"""
    client, _TS, token, hdr, ledger_id = _setup("debt12@example.com")
    try:
        due_with_time = datetime(2026, 9, 15, 14, 37, 22, tzinfo=timezone.utc)
        res = _create_debt(client, hdr, ledger_id, token, due_at=due_with_time.isoformat())
        assert res.status_code == 200, res.text

        d = _debts(client, hdr, ledger_id)[0]
        parsed = datetime.fromisoformat(d["due_at"].replace("Z", "+00:00"))
        assert parsed.year == 2026 and parsed.month == 9 and parsed.day == 15
        assert parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0
    finally:
        client.close()


def test_send_due_debt_reminders_skips_closed_debt():
    """結案的欠款即使逾期未還清,也不該再產生到期提醒。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "debt13@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEBT13"
        _seed_ledger(client, app_token, device, ledger_id)
        web = _login_web(client, "debt13@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        due = datetime.now(timezone.utc) - timedelta(days=1)
        res = _create_debt(client, hdr, ledger_id, token, principal_amount=100.0, due_at=due.isoformat())
        debt_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, ledger_id)
        close = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
            headers=hdr,
            json={"base_change_id": base, "closed_at": _iso()},
        )
        assert close.status_code == 200, close.text

        db = TS()
        try:
            sent = debt_reminders.send_due_debt_reminders(db)
            db.commit()
            assert sent == 0
        finally:
            db.close()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 交易讀取回傳 debt_id / debt_counterparty_name / debt_direction(既有 bug 修復)
# ---------------------------------------------------------------------------


def test_list_transactions_includes_debt_fields():
    client, _TS, token, hdr, ledger_id = _setup("debt14@example.com")
    try:
        res = _create_debt(
            client, hdr, ledger_id, token,
            direction="receivable", counterparty_name="老李", principal_amount=500.0,
        )
        debt_id = res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, tx_type="income", amount=200.0, debt_id=debt_id)
        assert tx_res.status_code == 200, tx_res.text
        tx_id = tx_res.json()["entity_id"]

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()}
        row = rows[tx_id]
        assert row["debt_id"] == debt_id
        assert row["debt_counterparty_name"] == "老李"
        assert row["debt_direction"] == "receivable"
    finally:
        client.close()


def test_workspace_transactions_includes_debt_fields():
    client, _TS, token, hdr, ledger_id = _setup("debt15@example.com")
    try:
        res = _create_debt(
            client, hdr, ledger_id, token,
            direction="payable", counterparty_name="小張", principal_amount=800.0,
        )
        debt_id = res.json()["entity_id"]
        tx_res = _create_tx(client, hdr, ledger_id, token, amount=250.0, debt_id=debt_id)
        assert tx_res.status_code == 200, tx_res.text
        tx_id = tx_res.json()["entity_id"]

        r = client.get(
            "/api/v1/read/workspace/transactions",
            headers=hdr,
            params={"ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        rows = {row["id"]: row for row in r.json()["items"]}
        row = rows[tx_id]
        assert row["debt_id"] == debt_id
        assert row["debt_counterparty_name"] == "小張"
        assert row["debt_direction"] == "payable"
    finally:
        client.close()


def test_send_due_debt_reminders_skips_settled_debt():
    client, TS = _make_client()
    try:
        owner = _register(client, "debt10@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DEBT10"
        _seed_ledger(client, app_token, device, ledger_id)
        web = _login_web(client, "debt10@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        due = datetime.now(timezone.utc) + timedelta(days=1)
        res = _create_debt(client, hdr, ledger_id, token, principal_amount=100.0, due_at=due.isoformat())
        debt_id = res.json()["entity_id"]
        _create_tx(client, hdr, ledger_id, token, amount=100.0, debt_id=debt_id)

        db = TS()
        try:
            sent = debt_reminders.send_due_debt_reminders(db)
            db.commit()
            assert sent == 0
        finally:
            db.close()
    finally:
        client.close()
