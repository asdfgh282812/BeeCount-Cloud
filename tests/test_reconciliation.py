"""對帳模式(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5,2026-08-09 改版為 Moze 式
逐筆核對清單,取代 v1 的「單筆餘額比對記錄」CRUD)契约测试:

- `GET /read/ledgers/{id}/accounts/{account_id}/statement`:「這期帳單」的
  交易清單(依卡分組 + 筆數/金額小計 + 已確認筆數/金額),`account_id` 範圍
  限制同 billing-summary(account_group 或沒掛靠群組的獨立信用卡)。
- 確認/取消確認(`reconciled_at`)、延後入帳(`deferred_posting_at`)都透過
  既有通用 `PATCH .../transactions/{id}` 完成,不是專門的 write endpoint
  ——這裡只驗證它們如何反映在 statement 讀端點上。
- `POST .../accounts/{account_id}/statement/clear-confirmations`:批次清空
  指定週期裡的已確認狀態,不影響其它週期。
- mobile `/sync/push` 的 `reconciledAt` merge 契约(partial update 保留旧值)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, LedgerMember, ReadTxProjection, User
from src.services import card_reward_payout, credit_card


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


def _dt(d: date, hour: int = 12) -> str:
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).isoformat()


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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _create_tx(client, hdr, ledger_id, *, account_id, amount, happened_at, tx_type="expense"):
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": 0, "tx_type": tx_type, "amount": amount,
            "happened_at": happened_at, "account_id": account_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _patch_tx(client, hdr, ledger_id, tx_id, **fields):
    r = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
        headers=hdr,
        json={"base_change_id": 0, **fields},
    )
    return r


def _create_transfer_tx(client, hdr, ledger_id, *, from_account_id, to_account_id, amount, happened_at):
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": 0, "tx_type": "transfer", "amount": amount,
            "happened_at": happened_at, "from_account_id": from_account_id,
            "to_account_id": to_account_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _create_reward_rule(client, hdr, ledger_id, account_id, *, reward_account_id, **kwargs):
    payload = {
        "base_change_id": 0, "label": "測試規則", "rate_type": "percentage", "rate_value": 10.0,
        "reward_account_id": reward_account_id,
    }
    payload.update(kwargs)
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules",
        headers=hdr, json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _create_tx_with_reward_rule(client, hdr, ledger_id, *, account_id, amount, happened_at, reward_rule_id):
    r = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=hdr,
        json={
            "base_change_id": 0, "tx_type": "expense", "amount": amount,
            "happened_at": happened_at, "account_id": account_id,
            "reward_rule_ids": [reward_rule_id],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _get_statement(client, hdr, ledger_id, account_id, **params):
    r = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/accounts/{account_id}/statement",
        headers=hdr, params=params,
    )
    return r


def _clear_confirmations(client, hdr, ledger_id, account_id, *, cycle_offset=0):
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts/{account_id}/statement/clear-confirmations",
        headers=hdr, json={"base_change_id": 0, "cycle_offset": cycle_offset},
    )


def _setup_card(client, hdr_app, ledger_id, account_id, *, billing_day, payment_due_day=20,
                 account_type="credit_card", parent_account_id=None, name="卡", device_id="d-app"):
    payload = {
        "syncId": account_id, "name": name, "type": account_type, "currency": "CNY",
        "billingDay": billing_day, "paymentDueDay": payment_due_day,
    }
    if parent_account_id is not None:
        payload = {
            "syncId": account_id, "name": name, "type": account_type, "currency": "CNY",
            "parentAccountId": parent_account_id,
        }
    _push(client, hdr_app, ledger_id, "account", account_id, payload, device_id=device_id)


# ---------------------------------------------------------------------------
# 「這期帳單」清單 + 分組小計
# ---------------------------------------------------------------------------


def test_statement_lists_current_cycle_and_totals_net_income_against_expense():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st1@t.com", device_id="d-app")
        web_tok = _login(client, "st1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st1", "ledger", "st1",
              {"syncId": "st1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st1", "card1", billing_day=billing_day)
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        tx1 = _create_tx(client, hdr_web, "st1", account_id="card1", amount=100.0,
                          happened_at=_dt(cycle_start + timedelta(days=1)))
        _create_tx(client, hdr_web, "st1", account_id="card1", amount=20.0,
                   happened_at=_dt(cycle_start + timedelta(days=2)), tx_type="income")
        # 上一期以外的交易(結帳日之前)不该被计入这一期
        _create_tx(client, hdr_web, "st1", account_id="card1", amount=999.0,
                   happened_at=_dt(cycle_start - timedelta(days=5)))

        r = _get_statement(client, hdr_web, "st1", "card1", cycle_offset=0)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["statement_count"] == 2
        assert data["statement_total"] == 80.0  # 100 - 20
        assert data["confirmed_count"] == 0
        assert data["confirmed_total"] == 0.0
        assert data["cycle_start"] == cycle_start.isoformat()
        assert data["cycle_end"] == cycle_end.isoformat()
        ids = {t["id"] for t in data["transactions"]}
        assert tx1 in ids
        assert len(data["accounts"]) == 1
        assert data["accounts"][0]["account_id"] == "card1"
        assert data["accounts"][0]["count"] == 2
    finally:
        client.close()


def test_statement_account_group_groups_transactions_by_member_card():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st2@t.com", device_id="d-app")
        web_tok = _login(client, "st2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st2", "ledger", "st2",
              {"syncId": "st2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st2", "group2", billing_day=billing_day,
                    account_type="account_group", name="主帳戶")
        _setup_card(client, hdr_app, "st2", "cardA", billing_day=None,
                    account_type="credit_card", parent_account_id="group2", name="卡A")
        _setup_card(client, hdr_app, "st2", "cardB", billing_day=None,
                    account_type="credit_card", parent_account_id="group2", name="卡B")

        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        _create_tx(client, hdr_web, "st2", account_id="cardA", amount=50.0,
                   happened_at=_dt(cycle_start + timedelta(days=1)))
        _create_tx(client, hdr_web, "st2", account_id="cardB", amount=70.0,
                   happened_at=_dt(cycle_start + timedelta(days=1)))

        r = _get_statement(client, hdr_web, "st2", "group2", cycle_offset=0)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["statement_count"] == 2
        assert data["statement_total"] == 120.0
        by_id = {a["account_id"]: a for a in data["accounts"]}
        assert by_id["cardA"]["total"] == 50.0
        assert by_id["cardB"]["total"] == 70.0
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Phase 6(docs/PH6_USER_FEEDBACK_2026-08_SD.md 需求 #1、#7):轉入信用卡的
# 轉帳要出現在對帳清單且正負號正確、轉出不出現、"新增消費"不誤算轉帳、
# 回饋金交易出現且帶 is_reward 標記。
# ---------------------------------------------------------------------------


def test_statement_includes_transfer_in_but_excludes_transfer_out():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st12@t.com", device_id="d-app")
        web_tok = _login(client, "st12@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st12", "ledger", "st12",
              {"syncId": "st12", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "st12", "account", "cash12",
              {"syncId": "cash12", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st12", "card12", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        expense_id = _create_tx(client, hdr_web, "st12", account_id="card12", amount=100.0,
                                 happened_at=_dt(cycle_start + timedelta(days=1)))
        transfer_in_id = _create_transfer_tx(client, hdr_web, "st12", from_account_id="cash12",
                                              to_account_id="card12", amount=40.0,
                                              happened_at=_dt(cycle_start + timedelta(days=2)))
        # 轉出這張卡(還原成現金)的錢不是還款,不該出現在這張卡的對帳清單。
        _create_transfer_tx(client, hdr_web, "st12", from_account_id="card12",
                             to_account_id="cash12", amount=15.0,
                             happened_at=_dt(cycle_start + timedelta(days=3)))

        r = _get_statement(client, hdr_web, "st12", "card12", cycle_offset=0)
        assert r.status_code == 200, r.text
        data = r.json()
        ids = {t["id"] for t in data["transactions"]}
        assert expense_id in ids
        assert transfer_in_id in ids
        assert data["statement_count"] == 2

        transfer_row = next(t for t in data["transactions"] if t["id"] == transfer_in_id)
        assert transfer_row["tx_type"] == "transfer"
        assert transfer_row["account_id"] == "card12"

        # "新增消費"(statement_total)比照 compute_cycle_period_billing.new_spend
        # 的口徑,只算 expense/income,轉入的 40 元不能被算進去。
        assert data["statement_total"] == 100.0
    finally:
        client.close()


def test_statement_confirming_transfer_in_reduces_confirmed_total():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st13@t.com", device_id="d-app")
        web_tok = _login(client, "st13@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st13", "ledger", "st13",
              {"syncId": "st13", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "st13", "account", "cash13",
              {"syncId": "cash13", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st13", "card13", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        transfer_in_id = _create_transfer_tx(client, hdr_web, "st13", from_account_id="cash13",
                                              to_account_id="card13", amount=25.0,
                                              happened_at=_dt(cycle_start + timedelta(days=1)))

        assert _patch_tx(client, hdr_web, "st13", transfer_in_id, reconciled_at=_iso()).status_code == 200

        data = _get_statement(client, hdr_web, "st13", "card13", cycle_offset=0).json()
        assert data["confirmed_count"] == 1
        # 轉入比照 income 記為負值(減少應繳)。
        assert data["confirmed_total"] == -25.0
    finally:
        client.close()


def test_statement_flags_reward_category_transaction_as_is_reward():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st14@t.com", device_id="d-app")
        web_tok = _login(client, "st14@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st14", "ledger", "st14",
              {"syncId": "st14", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st14", "card14", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        r = client.post(
            f"/api/v1/write/ledgers/st14/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0, "tx_type": "income", "amount": 5.0,
                "happened_at": _dt(cycle_start + timedelta(days=1)), "account_id": "card14",
                "category_name": "回饋金", "category_kind": "income",
            },
        )
        assert r.status_code == 200, r.text
        reward_tx_id = r.json()["entity_id"]

        data = _get_statement(client, hdr_web, "st14", "card14", cycle_offset=0).json()
        row = next(t for t in data["transactions"] if t["id"] == reward_tx_id)
        assert row["is_reward"] is True
        assert row["category_name"] == "回饋金"
    finally:
        client.close()


def test_statement_merges_same_rule_reward_payouts_into_one_row():
    """使用者反饋(2026-08-XX,需求 #7 改版):同一個回饋方案在這期帳單內的
    多筆自動入帳回饋,合併成一列顯示總金額,不逐筆列出。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "st15@t.com", device_id="d-app")
        web_tok = _login(client, "st15@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st15", "ledger", "st15",
              {"syncId": "st15", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st15", "card15", billing_day=billing_day, name="Green卡")
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)

        rule_id = _create_reward_rule(
            client, hdr_web, "st15", "card15", reward_account_id="card15",
            settlement_type="immediate_after_tx", settlement_days=0,
        )
        tx1 = _create_tx_with_reward_rule(
            client, hdr_web, "st15", account_id="card15", amount=100.0,
            happened_at=_dt(cycle_start + timedelta(days=1)), reward_rule_id=rule_id,
        )
        tx2 = _create_tx_with_reward_rule(
            client, hdr_web, "st15", account_id="card15", amount=50.0,
            happened_at=_dt(cycle_start + timedelta(days=2)), reward_rule_id=rule_id,
        )

        with TS() as db:
            result = card_reward_payout.materialize_due_card_reward_payouts(db)
            db.commit()
        assert result["tx_payouts"] == 2

        data = _get_statement(client, hdr_web, "st15", "card15", cycle_offset=0).json()
        # 兩筆消費 + 一個合併後的回饋方案列
        assert data["statement_count"] == 3
        reward_rows = [t for t in data["transactions"] if t["is_reward"]]
        assert len(reward_rows) == 1
        group = reward_rows[0]
        assert group["reward_rule_id"] == rule_id
        assert group["reward_rule_label"] == "測試規則"
        assert group["amount"] == 15.0  # 100*10% + 50*10%
        # member_tx_ids 是系統自動入帳的兩筆「回饋金」income 交易 sync_id,
        # 不是原始消費(tx1/tx2)本身。
        assert len(group["member_tx_ids"]) == 2
        assert tx1 not in group["member_tx_ids"]
        assert tx2 not in group["member_tx_ids"]

        # "新增消費" = 100+50-15
        assert data["statement_total"] == 135.0

        # 確認其中一筆成員,合併列還不算「已確認」;兩筆都確認後才算。
        member_ids = group["member_tx_ids"]
        assert _patch_tx(client, hdr_web, "st15", member_ids[0], reconciled_at=_iso()).status_code == 200
        data2 = _get_statement(client, hdr_web, "st15", "card15", cycle_offset=0).json()
        group2 = next(t for t in data2["transactions"] if t["is_reward"])
        assert group2["reconciled_at"] is None
        assert data2["confirmed_count"] == 0

        assert _patch_tx(client, hdr_web, "st15", member_ids[1], reconciled_at=_iso()).status_code == 200
        data3 = _get_statement(client, hdr_web, "st15", "card15", cycle_offset=0).json()
        group3 = next(t for t in data3["transactions"] if t["is_reward"])
        assert group3["reconciled_at"] is not None
        assert data3["confirmed_count"] == 1
        assert data3["confirmed_total"] == -15.0
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 確認 / 取消確認(對應原文右滑)
# ---------------------------------------------------------------------------


def test_statement_reconcile_toggle_via_general_patch_updates_confirmed_totals():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st3@t.com", device_id="d-app")
        web_tok = _login(client, "st3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st3", "ledger", "st3",
              {"syncId": "st3", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st3", "card3", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        tx_id = _create_tx(client, hdr_web, "st3", account_id="card3", amount=100.0,
                            happened_at=_dt(cycle_start + timedelta(days=1)))

        data0 = _get_statement(client, hdr_web, "st3", "card3", cycle_offset=0).json()
        assert data0["confirmed_count"] == 0

        upd = _patch_tx(client, hdr_web, "st3", tx_id, reconciled_at=_iso())
        assert upd.status_code == 200, upd.text

        data1 = _get_statement(client, hdr_web, "st3", "card3", cycle_offset=0).json()
        assert data1["confirmed_count"] == 1
        assert data1["confirmed_total"] == 100.0
        row = next(t for t in data1["transactions"] if t["id"] == tx_id)
        assert row["reconciled_at"] is not None

        # 传 null 取消确认
        upd2 = _patch_tx(client, hdr_web, "st3", tx_id, reconciled_at=None)
        assert upd2.status_code == 200, upd2.text
        data2 = _get_statement(client, hdr_web, "st3", "card3", cycle_offset=0).json()
        assert data2["confirmed_count"] == 0
        assert data2["confirmed_total"] == 0.0
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 延後入帳(對應原文左滑)
# ---------------------------------------------------------------------------


def test_statement_postpone_moves_transaction_to_next_cycle():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st4@t.com", device_id="d-app")
        web_tok = _login(client, "st4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st4", "ledger", "st4",
              {"syncId": "st4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st4", "card4", billing_day=billing_day)
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        tx_id = _create_tx(client, hdr_web, "st4", account_id="card4", amount=60.0,
                            happened_at=_dt(cycle_start + timedelta(days=1)))

        data0 = _get_statement(client, hdr_web, "st4", "card4", cycle_offset=0).json()
        assert data0["statement_count"] == 1

        # 「延後入帳到下期帳單第一天」——用這一期的結帳日隔天當目標入帳日
        next_start = date.fromisoformat(data0["cycle_end"]) + timedelta(days=1)
        upd = _patch_tx(client, hdr_web, "st4", tx_id, deferred_posting_at=_dt(next_start))
        assert upd.status_code == 200, upd.text

        data1 = _get_statement(client, hdr_web, "st4", "card4", cycle_offset=0).json()
        assert data1["statement_count"] == 0

        data_next = _get_statement(client, hdr_web, "st4", "card4", cycle_offset=1).json()
        assert data_next["statement_count"] == 1
        assert data_next["transactions"][0]["id"] == tx_id
        assert data_next["transactions"][0]["deferred_posting_at"] is not None
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 範圍限制 / 錯誤情境
# ---------------------------------------------------------------------------


def test_statement_rejects_plain_account_not_billing_root():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st5@t.com", device_id="d-app")
        web_tok = _login(client, "st5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st5", "ledger", "st5",
              {"syncId": "st5", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "st5", "account", "cash5",
              {"syncId": "cash5", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d-app")

        r = _get_statement(client, hdr_web, "st5", "cash5", cycle_offset=0)
        assert r.status_code == 400, r.text
    finally:
        client.close()


def test_statement_rejects_credit_card_without_billing_schedule():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st6@t.com", device_id="d-app")
        web_tok = _login(client, "st6@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st6", "ledger", "st6",
              {"syncId": "st6", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "st6", "account", "card6",
              {"syncId": "card6", "name": "卡", "type": "credit_card", "currency": "CNY"}, device_id="d-app")

        r = _get_statement(client, hdr_web, "st6", "card6", cycle_offset=0)
        assert r.status_code == 400, r.text
    finally:
        client.close()


def test_statement_unknown_account_404():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st7@t.com", device_id="d-app")
        web_tok = _login(client, "st7@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st7", "ledger", "st7",
              {"syncId": "st7", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = _get_statement(client, hdr_web, "st7", "does-not-exist", cycle_offset=0)
        assert r.status_code == 404, r.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 選單「取消全部選取」(清除確認狀態)
# ---------------------------------------------------------------------------


def test_clear_confirmations_resets_only_the_target_cycle():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st8@t.com", device_id="d-app")
        web_tok = _login(client, "st8@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st8", "ledger", "st8",
              {"syncId": "st8", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st8", "card8", billing_day=billing_day)
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        prev_start, prev_end = credit_card.shift_cycle(cycle_start, cycle_end, billing_day, -1)

        tx_cur = _create_tx(client, hdr_web, "st8", account_id="card8", amount=40.0,
                             happened_at=_dt(cycle_start + timedelta(days=1)))
        tx_old = _create_tx(client, hdr_web, "st8", account_id="card8", amount=30.0,
                             happened_at=_dt(prev_start + timedelta(days=1)))

        assert _patch_tx(client, hdr_web, "st8", tx_cur, reconciled_at=_iso()).status_code == 200
        assert _patch_tx(client, hdr_web, "st8", tx_old, reconciled_at=_iso()).status_code == 200

        clear = _clear_confirmations(client, hdr_web, "st8", "card8", cycle_offset=0)
        assert clear.status_code == 200, clear.text

        data_cur = _get_statement(client, hdr_web, "st8", "card8", cycle_offset=0).json()
        assert data_cur["confirmed_count"] == 0

        data_old = _get_statement(client, hdr_web, "st8", "card8", cycle_offset=-1).json()
        assert data_old["confirmed_count"] == 1  # 未被波及
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 權限:對帳/確認/延後入帳都走一般交易寫權限(owner + editor),不是 owner-only
# ---------------------------------------------------------------------------


def test_statement_actions_allowed_for_editor_role():
    client, TS = _make_client()
    try:
        owner_app = _login(client, "st9owner@t.com", device_id="d-app")
        owner_web = _login(client, "st9owner@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {owner_app}"}
        hdr_owner_web = {"Authorization": f"Bearer {owner_web}"}
        _push(client, hdr_app, "st9", "ledger", "st9",
              {"syncId": "st9", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st9", "card9", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        tx_id = _create_tx(client, hdr_owner_web, "st9", account_id="card9", amount=10.0,
                            happened_at=_dt(cycle_start + timedelta(days=1)))

        editor_app = _login(client, "st9editor@t.com", device_id="d-app2", client_type="app")
        editor_web = _login(client, "st9editor@t.com", device_id="d-web2", client_type="web")
        hdr_editor_app = {"Authorization": f"Bearer {editor_app}"}
        hdr_editor = {"Authorization": f"Bearer {editor_web}"}
        with TS() as db:
            editor_user = db.scalar(select(User).where(User.email == "st9editor@t.com"))
            ledger_row = db.scalar(select(Ledger).where(Ledger.external_id == "st9"))
            db.add(LedgerMember(ledger_id=ledger_row.id, user_id=editor_user.id, role="editor"))
            db.commit()
        # 帳戶是 user-global(UserAccountProjection 按 user_id 分表),不是
        # 帳本成員共享——editor 要能查/寫這個帳戶的對帳狀態,必须自己名下
        # 也有一份同 sync_id 的帳戶記錄。这是既有架構限制(跟 §2.10 balance
        # -adjustment editor 測試同一個理由),不是這裡新引入的问题,详见
        # CLAUDE.md §2.9.5 對應章節。
        _setup_card(client, hdr_editor_app, "st9", "card9", billing_day=billing_day, device_id="d-app2")

        # 讀取:editor 也能看
        r = _get_statement(client, hdr_editor, "st9", "card9", cycle_offset=0)
        assert r.status_code == 200, r.text

        # 確認:editor 走一般交易寫權限,应该成功(不是 owner-only)
        upd = _patch_tx(client, hdr_editor, "st9", tx_id, reconciled_at=_iso())
        assert upd.status_code == 200, upd.text

        # 清除確認:同样应该成功
        clear = _clear_confirmations(client, hdr_editor, "st9", "card9", cycle_offset=0)
        assert clear.status_code == 200, clear.text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# mobile /sync/push merge 契约
# ---------------------------------------------------------------------------


def test_mobile_push_transaction_reconciled_at_partial_update_keeps_existing_value():
    client, TS = _make_client()
    try:
        owner = _login(client, "st10@t.com", device_id="d1")
        hdr = {"Authorization": f"Bearer {owner}"}
        ledger_id = "L_ST10"
        _push(client, hdr, ledger_id, "ledger", ledger_id,
              {"syncId": ledger_id, "ledgerName": ledger_id, "currency": "CNY"}, device_id="d1")
        _push(client, hdr, ledger_id, "account", "acc1",
              {"syncId": "acc1", "name": "現金", "type": "cash", "currency": "CNY"}, device_id="d1")

        sync_id = "tx_recon_1"
        now = _iso()
        confirmed_at = _iso()
        _push(client, hdr, ledger_id, "transaction", sync_id, {
            "syncId": sync_id, "type": "expense", "amount": 50.0, "happenedAt": now,
            "accountId": "acc1", "accountName": "現金", "reconciledAt": confirmed_at,
        }, device_id="d1")

        # 只带 note,其它字段(含 reconciledAt)应保留
        _push(client, hdr, ledger_id, "transaction", sync_id, {
            "syncId": sync_id, "note": "備註更新",
        }, device_id="d1")

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
            assert row is not None
            assert row.note == "備註更新"
            assert row.reconciled_at is not None
    finally:
        client.close()


def test_statement_sort_desc_reverses_transaction_order():
    client, _TS = _make_client()
    try:
        app_tok = _login(client, "st11@t.com", device_id="d-app")
        web_tok = _login(client, "st11@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}"}
        _push(client, hdr_app, "st11", "ledger", "st11",
              {"syncId": "st11", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        now = datetime.now(timezone.utc)
        billing_day = (now.date() - timedelta(days=2)).day
        _setup_card(client, hdr_app, "st11", "card11", billing_day=billing_day)
        cycle_start, _cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
        tx_early = _create_tx(client, hdr_web, "st11", account_id="card11", amount=10.0,
                               happened_at=_dt(cycle_start + timedelta(days=1)))
        tx_late = _create_tx(client, hdr_web, "st11", account_id="card11", amount=20.0,
                              happened_at=_dt(cycle_start + timedelta(days=3)))

        asc = _get_statement(client, hdr_web, "st11", "card11", cycle_offset=0, sort_desc=False).json()
        desc = _get_statement(client, hdr_web, "st11", "card11", cycle_offset=0, sort_desc=True).json()
        assert [t["id"] for t in asc["transactions"]] == [tx_early, tx_late]
        assert [t["id"] for t in desc["transactions"]] == [tx_late, tx_early]
    finally:
        client.close()
