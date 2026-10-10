"""應收應付款項 MOZE 化(App v68):Cloud 端。

驗:
- 投影保留 App 送來的 `kind`/`startedAt`/分期欄位,Web 編輯不會洗掉。
- 已還只算到現在:分期排程的未來收還款算「待出帳」,不算已還。
- Web 記的收還款交易不計收支/預算,商家補對象名稱。
- 刪欠款只看已發生的收還款,未來排程一起刪。
- 新寫入 API:建立(新款項起點交易、既有款項、分期、代刷分期)、多筆收還款、
  停止追蹤、轉為支出。

設計見 App repo `docs/design/DEBT_MOZE_PARITY_WEB.md`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.models import ReadDebtProjection, ReadTxProjection, SyncChange
from src.services.debt_schedule import installment_date_at, split_installment_amounts
from tests.test_tx_splits import (
    _create_category,
    _latest_change_id,
    _login_web,
    _make_client,
    _push,
    _register,
    _seed_ledger,
    _setup,
)

TPE = ZoneInfo("Asia/Taipei")


def _post(client, hdr, token, ledger_id, path, body):
    base = _latest_change_id(client, token, ledger_id)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}{path}",
        headers=hdr,
        json={"base_change_id": base, **body},
    )


def _account(client, hdr, token, ledger_id, name="現金"):
    r = _post(client, hdr, token, ledger_id, "/accounts", {"name": name, "account_type": "cash"})
    assert r.status_code == 200, r.text
    return r.json()["entity_id"]


def _list_debts(client, hdr, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/debts", headers=hdr)
    assert r.status_code == 200, r.text
    return {d["id"]: d for d in r.json()}


def _txs(TS, **where):
    with TS() as db:
        stmt = select(ReadTxProjection)
        for k, v in where.items():
            stmt = stmt.where(getattr(ReadTxProjection, k) == v)
        return db.scalars(stmt).all()


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _setup_with_app(email, ledger_id):
    """App 推送用 app token,Web 讀寫用 web token。"""
    client, TS = _make_client()
    owner = _register(client, email)
    app_hdr = {"Authorization": f"Bearer {owner['access_token']}"}
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    token = _login_web(client, email)["access_token"]
    return client, TS, token, {"Authorization": f"Bearer {token}"}, app_hdr


def _aware(raw):
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 純函式:跟 App 同一套金額/日期規則
# ---------------------------------------------------------------------------


def test_split_installment_amounts_matches_app():
    assert split_installment_amounts(1000, 3) == [333, 333, 334]
    assert split_installment_amounts(100.5, 2) == [50.25, 50.25]
    assert split_installment_amounts(10.01, 3) == [3.33, 3.33, 3.35]


def test_installment_date_clamps_to_month_end_in_business_tz():
    # 台灣 1/31 00:30(= 1/30 16:30Z):第二期是 2/28 00:30,不是 3/3。
    first = datetime(2026, 1, 31, 0, 30, tzinfo=TPE)
    second = installment_date_at(first, 1, tz=TPE)
    assert second.astimezone(TPE) == datetime(2026, 2, 28, 0, 30, tzinfo=TPE)
    assert installment_date_at(first, 12, tz=TPE).astimezone(TPE).date().isoformat() == "2027-01-31"


# ---------------------------------------------------------------------------
# Step 1:資料正確性
# ---------------------------------------------------------------------------


def test_projection_keeps_app_kind_and_installment_fields():
    ledger_id = "L_MZ1"
    client, TS, token, hdr, app_hdr = _setup_with_app("moze1@example.com", ledger_id)
    started = datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc)
    _push(client, app_hdr, ledger_id, "debt", "d-app-1", {
        "syncId": "d-app-1", "direction": "payable", "counterpartyName": "彰銀",
        "principalAmount": 180000, "kind": "existing", "startedAt": _iso(started),
        "installmentCount": 18, "note": "青創", "excludedFromTotal": False,
    })
    debt = _list_debts(client, hdr, ledger_id)["d-app-1"]
    assert debt["kind"] == "existing"
    assert debt["installment_count"] == 18
    assert debt["started_at"].startswith("2026-08-10T04:00")

    # Web 只改備註:新 payload 仍帶 kind / 分期欄位。
    base = _latest_change_id(client, token, ledger_id)
    r = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/debts/d-app-1",
        headers=hdr, json={"base_change_id": base, "note": "青創貸款"},
    )
    assert r.status_code == 200, r.text
    with TS() as db:
        change = db.scalars(
            select(SyncChange).where(SyncChange.entity_sync_id == "d-app-1")
            .order_by(SyncChange.change_id.desc())
        ).first()
    assert change.payload_json["kind"] == "existing"
    assert change.payload_json["installmentCount"] == 18
    assert change.payload_json["note"] == "青創貸款"


def test_repaid_only_counts_up_to_now_and_future_is_scheduled():
    ledger_id = "L_MZ2"
    client, TS, token, hdr, app_hdr = _setup_with_app("moze2@example.com", ledger_id)
    now = datetime.now(timezone.utc)
    _push(client, app_hdr, ledger_id, "debt", "d-sch", {
        "syncId": "d-sch", "direction": "payable", "counterpartyName": "Ken",
        "principalAmount": 300, "kind": "existing", "installmentCount": 3,
    })
    for i, at in enumerate([now - timedelta(days=5), now + timedelta(days=25), now + timedelta(days=55)]):
        _push(client, app_hdr, ledger_id, "transaction", f"tx-sch-{i}", {
            "syncId": f"tx-sch-{i}", "type": "expense", "amount": 100,
            "happenedAt": _iso(at), "debtId": "d-sch",
            "excludeFromStats": True, "excludeFromBudget": True,
        })
    debt = _list_debts(client, hdr, ledger_id)["d-sch"]
    assert debt["repaid_amount"] == 100
    assert debt["scheduled_amount"] == 200
    assert debt["remaining_amount"] == 200
    assert debt["status"] == "partial"
    assert sorted(r["scheduled"] for r in debt["repayments"]) == [False, True, True]



def test_web_repayment_transaction_is_excluded_from_stats():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze3@example.com", "L_MZ3")
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "receivable", "counterparty_name": "Ken", "principal_amount": 1000,
    })
    assert r.status_code == 200, r.text
    debt_id = r.json()["entity_id"]
    r = _post(client, hdr, token, ledger_id, "/transactions", {
        "tx_type": "income", "amount": 400, "happened_at": _iso(datetime.now(timezone.utc)),
        "debt_id": debt_id,
    })
    assert r.status_code == 200, r.text
    tx = _txs(TS, sync_id=r.json()["entity_id"])[0]
    assert tx.exclude_from_stats is True
    assert tx.exclude_from_budget is True
    assert tx.merchant == "Ken"
    assert _list_debts(client, hdr, ledger_id)[debt_id]["remaining_amount"] == 600


def test_delete_debt_ignores_and_removes_future_schedule():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze4@example.com", "L_MZ4")
    first = datetime.now(timezone.utc) + timedelta(days=10)
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "彰銀", "principal_amount": 1200,
        "kind": "existing", "installment": {"count": 12, "first_at": _iso(first)},
    })
    assert r.status_code == 200, r.text
    debt_id = r.json()["entity_id"]
    assert len(_txs(TS, debt_sync_id=debt_id)) == 12

    base = _latest_change_id(client, token, ledger_id)
    r = client.request(
        "DELETE", f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
        headers=hdr, json={"base_change_id": base},
    )
    assert r.status_code == 200, r.text
    assert _txs(TS, debt_sync_id=debt_id) == []
    deleted_tx = [
        c for c in _changes(TS) if c.entity_type == "transaction" and c.action == "delete"
    ]
    assert len(deleted_tx) == 12


def _changes(TS):
    with TS() as db:
        return db.scalars(select(SyncChange)).all()


def test_delete_debt_with_past_repayment_still_blocked():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze5@example.com", "L_MZ5")
    first = datetime.now(timezone.utc) - timedelta(days=3)
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "彰銀", "principal_amount": 300,
        "kind": "existing", "installment": {"count": 3, "first_at": _iso(first)},
    })
    debt_id = r.json()["entity_id"]
    base = _latest_change_id(client, token, ledger_id)
    r = client.request(
        "DELETE", f"/api/v1/write/ledgers/{ledger_id}/debts/{debt_id}",
        headers=hdr, json={"base_change_id": base},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Step 2:新寫入 API
# ---------------------------------------------------------------------------


def test_create_new_payable_with_account_creates_origin_income():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze6@example.com", "L_MZ6")
    acc = _account(client, hdr, token, ledger_id)
    cat = _create_category(client, hdr, ledger_id, token, "借入", kind="payable")
    started = datetime.now(timezone.utc) - timedelta(hours=1)
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "媽媽", "principal_amount": 5000,
        "account_id": acc, "category_id": cat, "started_at": _iso(started), "note": "學費",
    })
    assert r.status_code == 200, r.text
    debt_id = r.json()["entity_id"]
    debt = _list_debts(client, hdr, ledger_id)[debt_id]
    assert debt["kind"] == "new"
    assert debt["origin_tx_id"]
    assert debt["category_name"] == "借入"
    assert debt["remaining_amount"] == 5000
    origin = _txs(TS, sync_id=debt["origin_tx_id"])[0]
    assert origin.tx_type == "income"
    assert origin.amount == 5000
    assert origin.account_sync_id == acc
    assert origin.exclude_from_stats is True and origin.exclude_from_budget is True
    assert origin.category_sync_id == cat
    assert origin.merchant == "媽媽"


def test_create_new_debt_requires_account():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze7@example.com", "L_MZ7")
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "receivable", "counterparty_name": "Ken", "principal_amount": 100,
        "installment": {"count": 2, "first_at": _iso(datetime.now(timezone.utc))},
    })
    assert r.status_code == 400


def test_create_existing_installment_counts_past_periods():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze8@example.com", "L_MZ8")
    acc = _account(client, hdr, token, ledger_id)
    now = datetime.now(timezone.utc)
    first = installment_date_at(now - timedelta(days=1), -2)  # 三期已到期
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "彰銀", "principal_amount": 1800,
        "kind": "existing", "account_id": acc,
        "installment": {"count": 18, "first_at": _iso(first), "schedule_account_id": acc},
    })
    assert r.status_code == 200, r.text
    debt_id = r.json()["entity_id"]
    debt = _list_debts(client, hdr, ledger_id)[debt_id]
    assert debt["origin_tx_id"] is None  # 既有款項沒有起點交易
    assert debt["installment_count"] == 18
    assert debt["repaid_amount"] == 300
    assert debt["scheduled_amount"] == 1500
    assert debt["remaining_amount"] == 1500
    sched = _txs(TS, debt_sync_id=debt_id)
    assert len(sched) == 18
    assert all(t.account_sync_id == acc and t.exclude_from_stats for t in sched)
    assert all(t.tx_type == "expense" for t in sched)


def test_create_card_installment_makes_one_receivable_per_period():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze9@example.com", "L_MZ9")
    card = _account(client, hdr, token, ledger_id, "信用卡")
    first = datetime.now(timezone.utc) - timedelta(days=1)
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "receivable", "counterparty_name": "Alan", "principal_amount": 1000,
        "account_id": card,
        "installment": {"count": 3, "first_at": _iso(first), "card": True},
    })
    assert r.status_code == 200, r.text
    with TS() as db:
        debts = db.scalars(
            select(ReadDebtProjection).where(ReadDebtProjection.counterparty_name == "Alan")
            .order_by(ReadDebtProjection.installment_no)
        ).all()
        assert [d.installment_no for d in debts] == [1, 2, 3]
        assert [d.principal_amount for d in debts] == [333, 333, 334]
        assert len({d.installment_group_id for d in debts}) == 1
        assert all(d.direction == "receivable" and d.installment_count == 3 for d in debts)
        origins = [db.scalar(select(ReadTxProjection).where(
            ReadTxProjection.sync_id == d.origin_tx_sync_id)) for d in debts]
    assert all(o.tx_type == "expense" and o.account_sync_id == card for o in origins)
    listed = _list_debts(client, hdr, ledger_id)
    later = [d for d in listed.values() if d["installment_no"] == 2][0]
    assert _aware(later["started_at"]) > datetime.now(timezone.utc)

    # 代刷分期只限應收。
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "X", "principal_amount": 100,
        "account_id": card, "installment": {"count": 2, "first_at": _iso(first), "card": True},
    })
    assert r.status_code == 400


def test_repay_multiple_debts_and_settle_rest():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze10@example.com", "L_MZ10")
    acc = _account(client, hdr, token, ledger_id)
    ids = []
    for amount in (899, 165):
        r = _post(client, hdr, token, ledger_id, "/debts", {
            "direction": "receivable", "counterparty_name": "Alan", "principal_amount": amount,
            "account_id": acc,
        })
        ids.append(r.json()["entity_id"])
    payable = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "Alan", "principal_amount": 10,
    }).json()["entity_id"]

    happened = _iso(datetime.now(timezone.utc))
    r = _post(client, hdr, token, ledger_id, "/debts/repay", {
        "allocations": [{"debt_id": ids[0], "amount": 1000}], "happened_at": happened,
    })
    assert r.status_code == 400  # 超過剩餘
    r = _post(client, hdr, token, ledger_id, "/debts/repay", {
        "allocations": [{"debt_id": ids[0], "amount": 1}, {"debt_id": payable, "amount": 1}],
        "happened_at": happened,
    })
    assert r.status_code == 400  # 應收應付混選

    r = _post(client, hdr, token, ledger_id, "/debts/repay", {
        "allocations": [{"debt_id": ids[0], "amount": 899}, {"debt_id": ids[1], "amount": 101}],
        "account_id": acc, "happened_at": happened, "settle_debt_ids": [ids[1]],
    })
    assert r.status_code == 200, r.text
    listed = _list_debts(client, hdr, ledger_id)
    assert listed[ids[0]]["status"] == "settled"
    assert listed[ids[1]]["status"] == "closed"
    assert listed[ids[1]]["remaining_amount"] == 64
    repayments = _txs(TS, debt_sync_id=ids[0])
    assert len(repayments) == 1
    assert repayments[0].tx_type == "income"
    assert repayments[0].exclude_from_stats is True
    assert repayments[0].merchant == "Alan"


def test_stop_tracking_drops_future_schedule_only():
    client, TS, token, hdr, ledger_id, _, _ = _setup("moze11@example.com", "L_MZ11")
    acc = _account(client, hdr, token, ledger_id)
    first = datetime.now(timezone.utc) - timedelta(days=2)
    r = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "payable", "counterparty_name": "彰銀", "principal_amount": 300,
        "kind": "existing", "installment": {"count": 3, "first_at": _iso(first), "schedule_account_id": acc},
    })
    debt_id = r.json()["entity_id"]
    r = _post(client, hdr, token, ledger_id, f"/debts/{debt_id}/stop", {})
    assert r.status_code == 200, r.text
    debt = _list_debts(client, hdr, ledger_id)[debt_id]
    assert debt["status"] == "closed"
    assert debt["scheduled_amount"] == 0
    assert len(_txs(TS, debt_sync_id=debt_id)) == 1


def test_write_off_records_repayment_plus_counted_expense():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("moze12@example.com", "L_MZ12")
    acc = _account(client, hdr, token, ledger_id)
    bad_debt = _create_category(client, hdr, ledger_id, token, "呆帳", kind="expense")
    debt_id = _post(client, hdr, token, ledger_id, "/debts", {
        "direction": "receivable", "counterparty_name": "Ken", "principal_amount": 500,
        "account_id": acc,
    }).json()["entity_id"]
    _post(client, hdr, token, ledger_id, "/debts/repay", {
        "allocations": [{"debt_id": debt_id, "amount": 200}],
        "account_id": acc, "happened_at": _iso(datetime.now(timezone.utc)),
    })
    income_cat = _create_category(client, hdr, ledger_id, token, "獎金", kind="income")
    r = _post(client, hdr, token, ledger_id, f"/debts/{debt_id}/write-off", {
        "category_id": income_cat, "account_id": acc,
        "happened_at": _iso(datetime.now(timezone.utc)),
    })
    assert r.status_code == 400  # 應收要選支出分類

    r = _post(client, hdr, token, ledger_id, f"/debts/{debt_id}/write-off", {
        "category_id": bad_debt, "account_id": acc,
        "happened_at": _iso(datetime.now(timezone.utc)),
    })
    assert r.status_code == 200, r.text
    debt = _list_debts(client, hdr, ledger_id)[debt_id]
    assert debt["status"] == "settled"
    counted = _txs(TS, sync_id=r.json()["entity_id"])[0]
    assert counted.tx_type == "expense"
    assert counted.amount == 300
    assert counted.exclude_from_stats is False
    assert counted.category_sync_id == bad_debt
    assert counted.debt_sync_id is None
