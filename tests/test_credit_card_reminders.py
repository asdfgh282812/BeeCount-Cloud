"""信用卡繳款到期提醒(MOZE_FEATURE_GAP_SD.md §2.9 Phase 4,2026-08-02 補)。

跟 `tests/test_debts.py` 里 debt reminder 的测试同一个模式:直接调用
`services.credit_card_reminders.send_due_card_reminders(db)`,不经过 HTTP,
断言 `notifications` 表落地的内容 + 去重行为。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Notification, User
from src.services import credit_card, credit_card_reminders


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


def _setup_group_with_debt(client, hdr_app, ledger_id, email, *, billing_day, payment_due_day, spend=100.0):
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-group",
          {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY",
           "billingDay": billing_day, "paymentDueDay": payment_due_day}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "account", "acc-card",
          {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
           "parentAccountId": "acc-group"}, device_id="d-app")
    _push(client, hdr_app, ledger_id, "transaction", "tx-spend",
          {"syncId": "tx-spend", "type": "expense", "amount": spend,
           "happenedAt": _iso(datetime.now(timezone.utc) - timedelta(days=1)),
           "accountId": "acc-card", "accountName": "卡"}, device_id="d-app")


def test_statement_closed_reminder_fires_on_billing_day():
    client, TS = _make_client()
    try:
        email = "ccr1@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        billing_day = now.day  # 今天就是結帳日
        _setup_group_with_debt(client, hdr_app, "lgr1", email, billing_day=billing_day, payment_due_day=20)

        with TS() as db:
            sent = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent == 1

        notifs = _notifications_for(TS, email)
        assert len(notifs) == 1
        assert notifs[0].payload_json["kind"] == "statement_closed"
        assert notifs[0].payload_json["accountId"] == "acc-group"
    finally:
        app.dependency_overrides.clear()


def test_due_soon_and_due_today_reminders():
    client, TS = _make_client()
    try:
        email = "ccr2@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        # 让 payment_due_day 落在 7 天后:billing_day 设成"这个周期已结束",
        # due_date = cycle_end + N 天,凑成 today + 7。
        billing_day = (now.date() - timedelta(days=1)).day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        target_due = now.date() + timedelta(days=7)
        # payment_due_day 用 due_date_for_cycle_end 反推:直接用 target_due.day,
        # 只在 due_date 落在同月/下月都会被 due_date_for_cycle_end 处理,这里
        # 构造成简单情形(cycle_end 到 target_due 同月或下月均可,函数自己找)。
        payment_due_day = target_due.day
        actual_due = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
        assert actual_due == target_due, "test setup: adjust billing_day so due falls exactly 7 days out"

        _setup_group_with_debt(
            client, hdr_app, "lgr2", email, billing_day=billing_day, payment_due_day=payment_due_day,
        )

        with TS() as db:
            sent = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent == 1
        notifs = _notifications_for(TS, email)
        assert notifs[0].payload_json["kind"] == "due_soon"
    finally:
        app.dependency_overrides.clear()


def test_no_reminder_when_already_paid():
    client, TS = _make_client()
    try:
        email = "ccr3@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        billing_day = now.day
        _setup_group_with_debt(client, hdr_app, "lgr3", email, billing_day=billing_day, payment_due_day=20, spend=100.0)
        _push(client, hdr_app, "lgr3", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgr3", "transaction", "tx-pay",
              {"syncId": "tx-pay", "type": "transfer", "amount": 100.0,
               "happenedAt": _iso(now),
               "fromAccountId": "acc-cash", "fromAccountName": "現金",
               "toAccountId": "acc-card", "toAccountName": "卡"}, device_id="d-app")

        with TS() as db:
            sent = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent == 0
        assert len(_notifications_for(TS, email)) == 0
    finally:
        app.dependency_overrides.clear()


def test_reminder_not_duplicated_for_same_cycle():
    client, TS = _make_client()
    try:
        email = "ccr4@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        billing_day = now.day
        _setup_group_with_debt(client, hdr_app, "lgr4", email, billing_day=billing_day, payment_due_day=20)

        with TS() as db:
            sent1 = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        with TS() as db:
            sent2 = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent1 == 1
        assert sent2 == 0
        assert len(_notifications_for(TS, email)) == 1
    finally:
        app.dependency_overrides.clear()


def test_overdue_reminder_fires_when_due_date_already_passed():
    """2026-08-04 补:使用者建了一筆已逾期的帳單(還款日已經過去才設定/才
    有欠款),之前只精確比對 `today == due_date` 這三個時機,一旦錯過那個
    精確的一天就永遠不會再提醒——這裡驗證新加的 `overdue` 時機兜底。"""
    client, TS = _make_client()
    try:
        email = "ccr6@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=10)).day
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        target_due = now.date() - timedelta(days=3)
        payment_due_day = target_due.day
        actual_due = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
        assert actual_due == target_due, "test setup: adjust billing_day so due falls exactly 3 days in the past"

        # 花費要落在「已結束的那個帳單週期」內(cycle_start~cycle_end),不能
        # 用 _setup_group_with_debt 預設的 now-1天(那會落在還沒結束的當期
        # 窗口內,lifetime remaining_due 只算到 cycle_end 為止,不會計入)。
        _push(client, hdr_app, "lgr6", "ledger", "lgr6",
              {"syncId": "lgr6", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgr6", "account", "acc-group",
              {"syncId": "acc-group", "name": "主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": billing_day, "paymentDueDay": payment_due_day}, device_id="d-app")
        _push(client, hdr_app, "lgr6", "account", "acc-card",
              {"syncId": "acc-card", "name": "卡", "type": "credit_card", "currency": "CNY",
               "parentAccountId": "acc-group"}, device_id="d-app")
        _push(client, hdr_app, "lgr6", "transaction", "tx-spend",
              {"syncId": "tx-spend", "type": "expense", "amount": 100.0,
               "happenedAt": _iso(datetime.combine(cycle_start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)),
               "accountId": "acc-card", "accountName": "卡"}, device_id="d-app")

        with TS() as db:
            sent = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent == 1
        notifs = _notifications_for(TS, email)
        assert notifs[0].payload_json["kind"] == "overdue"

        # 同一期不重复提醒
        with TS() as db:
            sent2 = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent2 == 0
        assert len(_notifications_for(TS, email)) == 1
    finally:
        app.dependency_overrides.clear()


def test_no_reminder_for_group_without_children():
    client, TS = _make_client()
    try:
        email = "ccr5@t.com"
        tok = _login(client, email, device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {tok}"}
        now = datetime.now(timezone.utc)
        _push(client, hdr_app, "lgr5", "ledger", "lgr5",
              {"syncId": "lgr5", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgr5", "account", "acc-group-empty",
              {"syncId": "acc-group-empty", "name": "空主帳戶", "type": "account_group", "currency": "CNY",
               "billingDay": now.day, "paymentDueDay": 20}, device_id="d-app")

        with TS() as db:
            sent = credit_card_reminders.send_due_card_reminders(db, now=now)
            db.commit()
        assert sent == 0
    finally:
        app.dependency_overrides.clear()
