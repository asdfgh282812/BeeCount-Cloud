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
from src.services import credit_card


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
