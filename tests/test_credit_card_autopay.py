"""信用卡自動扣繳(MOZE_FEATURE_GAP_SD.md §2.9,2026-08-04 改版:帳戶級開關,
不再借用週期性收支規則)。跟 `tests/test_credit_card_reminders.py` 同一个
模式:直接调用 `services.credit_card_autopay.materialize_due_card_autopay(db)`,
不经过 HTTP background loop,断言生成的交易 + notifications 表内容。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Notification, ReadTxProjection, User, UserAccountProjection
from src.services import credit_card, credit_card_autopay


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


def _notifications_for(TS, email) -> list[Notification]:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        rows = db.scalars(
            select(Notification).where(
                Notification.user_id == user_id, Notification.category == "card_due",
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _transfers_to(TS, ledger_ext_id, to_account_id):
    with TS() as db:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.tx_type == "transfer",
                ReadTxProjection.to_account_sync_id == to_account_id,
            )
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _setup_autopay_ledger(
    client, hdr_app, hdr_web, ledger_id, *, billing_day, payment_due_day, spend, cash_balance,
):
    """一張獨立信用卡(沒有掛靠群組)+ 一個現金帳戶當自動扣繳來源,信用卡
    身上已消費 `spend`,落在「已結束的那個帳單週期」內。"""
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-card",
          {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
           "billingDay": billing_day, "paymentDueDay": payment_due_day}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-cash",
          {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY",
           "initialBalance": cash_balance}, device_id="d-app")

    now = datetime.now(timezone.utc)
    cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
    spend_at = datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
    _push(client, hdr_app, ledger_id, "transaction", "tx-spend",
          {"syncId": "tx-spend", "type": "expense", "amount": spend,
           "happenedAt": _iso(spend_at), "accountId": "acc-card", "accountName": "卡"},
          device_id="d-app")

    r = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/acc-card",
        headers=hdr_web,
        json={"base_change_id": 0, "auto_pay_enabled": True, "auto_pay_from_account_id": "acc-cash"},
    )
    assert r.status_code == 200, r.text


def test_autopay_not_triggered_before_due_date():
    client, TS = _make_client()
    try:
        email = "ap1@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        # payment_due_day 设成还没到(未来)
        billing_day = now.day
        payment_due_day = (now.date() + timedelta(days=10)).day
        _setup_autopay_ledger(
            client, hdr_app, hdr_web, "lgap1",
            billing_day=billing_day, payment_due_day=payment_due_day,
            spend=100.0, cash_balance=1000.0,
        )
        with TS() as db:
            result = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result == {"executed": 0, "skipped_insufficient": 0}
        assert len(_transfers_to(TS, "lgap1", "acc-card")) == 0
    finally:
        app.dependency_overrides.clear()


def test_autopay_executes_full_payment_when_due_and_balance_sufficient():
    client, TS = _make_client()
    try:
        email = "ap2@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        # 让 due_date 落在今天或今天以前(已到期)
        payment_due_day = cycle_start.day
        due_date = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
        assert due_date <= now.date(), "test setup: due date must already have arrived"

        _setup_autopay_ledger(
            client, hdr_app, hdr_web, "lgap2",
            billing_day=billing_day, payment_due_day=payment_due_day,
            spend=300.0, cash_balance=1000.0,
        )
        with TS() as db:
            result = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result == {"executed": 1, "skipped_insufficient": 0}

        transfers = _transfers_to(TS, "lgap2", "acc-card")
        assert len(transfers) == 1
        assert transfers[0].amount == 300.0
        assert transfers[0].from_account_sync_id == "acc-cash"

        notifs = _notifications_for(TS, email)
        assert len(notifs) == 1
        assert notifs[0].payload_json["kind"] == "auto_pay_executed"

        # 再跑一次:这一期已经繳过了,不会重複扣款
        with TS() as db:
            result2 = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result2 == {"executed": 0, "skipped_insufficient": 0}
        assert len(_transfers_to(TS, "lgap2", "acc-card")) == 1
    finally:
        app.dependency_overrides.clear()


def test_autopay_skips_and_notifies_when_balance_insufficient_then_retries():
    client, TS = _make_client()
    try:
        email = "ap3@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        payment_due_day = cycle_start.day
        due_date = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
        assert due_date <= now.date()

        _setup_autopay_ledger(
            client, hdr_app, hdr_web, "lgap3",
            billing_day=billing_day, payment_due_day=payment_due_day,
            spend=300.0, cash_balance=50.0,  # 不够
        )
        with TS() as db:
            result = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result == {"executed": 0, "skipped_insufficient": 1}
        assert len(_transfers_to(TS, "lgap3", "acc-card")) == 0
        notifs = _notifications_for(TS, email)
        assert len(notifs) == 1
        assert notifs[0].payload_json["kind"] == "auto_pay_skipped_insufficient"

        # 同一期再跑一次:不重複通知,但仍然是 skipped(不进 executed 计数)
        with TS() as db:
            result2 = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result2 == {"executed": 0, "skipped_insufficient": 0}
        assert len(_notifications_for(TS, email)) == 1

        # 补足余额后重试成功
        r = client.patch(
            "/api/v1/write/ledgers/lgap3/accounts/acc-cash",
            headers=hdr_web, json={"base_change_id": 0, "initial_balance": 1000.0},
        )
        assert r.status_code == 200, r.text
        with TS() as db:
            result3 = credit_card_autopay.materialize_due_card_autopay(db, now=now)
            db.commit()
        assert result3 == {"executed": 1, "skipped_insufficient": 0}
        assert len(_transfers_to(TS, "lgap3", "acc-card")) == 1
    finally:
        app.dependency_overrides.clear()


def test_autopay_rejects_group_as_source_account():
    """來源帳戶不能是 account_group(群組沒有自己的資金)。"""
    client, TS = _make_client()
    try:
        email = "ap4@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgap4", "ledger", "lgap4",
              {"syncId": "lgap4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgap4", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgap4", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgap4/accounts/acc-card",
            headers=hdr_web,
            json={"base_change_id": 0, "auto_pay_enabled": True, "auto_pay_from_account_id": "acc-group"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_autopay_rejects_self_as_source_account():
    client, TS = _make_client()
    try:
        email = "ap5@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgap5", "ledger", "lgap5",
              {"syncId": "lgap5", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgap5", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgap5/accounts/acc-card",
            headers=hdr_web,
            json={"base_change_id": 0, "auto_pay_enabled": True, "auto_pay_from_account_id": "acc-card"},
        )
        assert r.status_code == 400, r.text
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_unrelated_field_keeps_autopay_settings():
    """2026-08-02 補強期間發現的既有 bug:`snapshot_builder.py` 重建帳戶當前
    狀態(diff-emit 的 "prev" 基線)時完全没有 SELECT `auto_pay_enabled`/
    `auto_pay_from_account_id` 這兩欄——導致任何一次跟自動扣繳無關的帳戶
    編輯(這裡改 note)都會把已設定好的自動扣繳靜默清空成
    enabled=False/from_account_id=None。修法是把這兩欄(以及頭像欄位)補進
    `snapshot_builder.py` 的帳戶 SELECT 裡。"""
    client, TS = _make_client()
    try:
        email = "ap-keep@t.com"
        app_tok = _login(client, email, device_id="d-app", client_type="app")
        web_tok = _login(client, email, device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        _push(client, hdr_app, "lgapkeep", "ledger", "lgapkeep",
              {"syncId": "lgapkeep", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgapkeep", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "现金", "type": "cash", "currency": "CNY"},
              device_id="d-app")
        _push(client, hdr_app, "lgapkeep", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY"},
              device_id="d-app")

        r1 = client.patch(
            "/api/v1/write/ledgers/lgapkeep/accounts/acc-card",
            headers=hdr_web,
            json={"base_change_id": 0, "auto_pay_enabled": True, "auto_pay_from_account_id": "acc-cash"},
        )
        assert r1.status_code == 200, r1.text

        # 跟自动扣缴完全无关的编辑 —— 只改 note。
        base2 = r1.json()["new_change_id"]
        r2 = client.patch(
            "/api/v1/write/ledgers/lgapkeep/accounts/acc-card",
            headers=hdr_web,
            json={"base_change_id": base2, "note": "备注"},
        )
        assert r2.status_code == 200, r2.text

        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == email))
            row = db.scalar(
                select(UserAccountProjection).where(
                    UserAccountProjection.user_id == user_id,
                    UserAccountProjection.sync_id == "acc-card",
                )
            )
            assert row is not None
            assert row.note == "备注"
            assert row.auto_pay_enabled is True, "不相关的编辑不能清空已设置的自动扣缴"
            assert row.auto_pay_from_account_id == "acc-cash"
    finally:
        app.dependency_overrides.clear()
