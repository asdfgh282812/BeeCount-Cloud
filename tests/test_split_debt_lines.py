"""拆帳欠款明細(App v67,2026-10-10)。

拆帳裡的欠款明細(支出拆帳=應收、收入拆帳=應付)背後各是一筆 debt
(`originTxId` = 這筆交易,本金 = 明細金額)。驗:

- Web 寫入 API:split 項帶 `debt` 物件 → server 在同一次寫入裡建立/更新/刪除
  欠款;已有收還款的欠款明細不能移除(409);不能借用別筆欠款的 id。
- App 送來的 `debtId` 明細:投影保留、讀 API 帶出欠款資訊、Web 只改備註不會
  洗掉。
- 統計/預算扣掉欠款明細,帳戶餘額照整筆。
- 刪交易:沒有收還款的拆帳欠款一起刪(多筆);欠款 API 不能直接刪拆帳欠款。

設計見 App repo `docs/design/SPLIT_DEBT_LINES_WEB.md`。
"""
from __future__ import annotations

from sqlalchemy import select

from src.main import app
from src.models import (
    ReadDebtProjection,
    ReadTxProjection,
    ReadTxSplitProjection,
    SyncChange,
)
from src.snapshot_mutator import delete_transaction
from tests.test_tx_splits import (
    _create_category,
    _create_tx,
    _latest_change_id,
    _login_web,
    _make_client,
    _push,
    _register,
    _iso,
    _setup,
)


def _debts(TS, tx_id):
    with TS() as db:
        return db.scalars(
            select(ReadDebtProjection).where(ReadDebtProjection.origin_tx_sync_id == tx_id)
        ).all()


def _read_tx(client, hdr, ledger_id, tx_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=hdr)
    assert r.status_code == 200, r.text
    return next(t for t in r.json() if t["id"] == tx_id)


def _patch_tx(client, hdr, ledger_id, token, tx_id, **fields):
    base = _latest_change_id(client, token, ledger_id)
    return client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
        headers=hdr,
        json={"base_change_id": base, **fields},
    )


def _create_receivable_split(client, hdr, ledger_id, token, cat_food, *, debt_category_id=None):
    res = _create_tx(
        client, hdr, ledger_id, token,
        amount=1000.0,
        splits=[
            {"category_id": cat_food, "category_name": "餐饮", "amount": 500.0, "note": "晚餐"},
            {"amount": 500.0, "note": "代墊晚餐", "debt": {
                "counterparty_name": "小明",
                "due_at": "2026-10-31T00:00:00Z",
                "category_id": debt_category_id,
            }},
        ],
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def test_create_expense_split_with_receivable_line_creates_debt():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl1@example.com", "L_SDL1")
    try:
        advance = _create_category(client, hdr, ledger_id, token, "代付", kind="receivable")
        tx_id = _create_receivable_split(client, hdr, ledger_id, token, cat_food, debt_category_id=advance)

        debts = _debts(TS, tx_id)
        assert len(debts) == 1
        debt = debts[0]
        assert debt.direction == "receivable"
        assert debt.counterparty_name == "小明"
        assert debt.principal_amount == 500.0
        assert debt.note == "代墊晚餐"
        assert debt.category_sync_id == advance
        assert debt.due_at.year == 2026 and debt.due_at.month == 10 and debt.due_at.day == 31

        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.debt_split_amount == 500.0
            split_rows = db.scalars(
                select(ReadTxSplitProjection)
                .where(ReadTxSplitProjection.tx_sync_id == tx_id)
                .order_by(ReadTxSplitProjection.sort_order)
            ).all()
            assert [s.debt_sync_id for s in split_rows] == [None, debt.sync_id]
            assert split_rows[1].category_sync_id is None
            # App 拉得到這筆欠款(debt SyncChange,payload 是完整欄位)。
            change = db.scalar(
                select(SyncChange).where(
                    SyncChange.entity_type == "debt", SyncChange.entity_sync_id == debt.sync_id,
                )
            )
            assert change.payload_json["originTxId"] == tx_id
            assert change.payload_json["principalAmount"] == 500.0
            assert change.payload_json["dueAt"].startswith("2026-10-31T00:00:00")
            tx_change = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_type == "transaction", SyncChange.entity_sync_id == tx_id,
                )
            ).all()[-1]
            assert tx_change.payload_json["splits"][1] == {
                "amount": 500.0, "sortOrder": 1, "debtId": debt.sync_id, "note": "代墊晚餐",
            }

        tx = _read_tx(client, hdr, ledger_id, tx_id)
        line = tx["splits"][1]
        assert line["debt_id"] == debt.sync_id
        assert line["category_id"] is None
        assert line["debt_direction"] == "receivable"
        assert line["debt_counterparty_name"] == "小明"
        assert line["debt_category_id"] == advance
        assert line["debt_category_name"] == "代付"
        assert line["debt_remaining_amount"] == 500.0
        assert line["debt_status"] == "open"
        assert line["debt_has_repayments"] is False
    finally:
        app.dependency_overrides.clear()


def test_debt_split_excluded_from_stats_but_not_balance():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl2@example.com", "L_SDL2")
    try:
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers=hdr,
            json={"base_change_id": base, "type": "total", "amount": 5000},
        )
        assert res.status_code == 200, res.text
        budget_id = res.json()["entity_id"]

        _create_receivable_split(client, hdr, ledger_id, token, cat_food)

        r = client.get(
            "/api/v1/read/workspace/analytics",
            headers=hdr,
            params={"scope": "all", "metric": "expense", "ledger_id": ledger_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["expense_total"] == 500.0
        ranks = {row["category_name"]: row["total"] for row in body["category_ranks"]}
        assert ranks == {"餐饮": 500.0}

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr)
        detail = r.json()
        assert detail["expense_total"] == 500.0
        # 帳戶/帳本餘額照整筆:錢確實整筆出去了。
        assert detail["balance"] == -1000.0

        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/budgets/usage", headers=hdr)
        usage = {item["budget_id"]: item["used"] for item in r.json()["items"]}
        assert usage[budget_id] == 500.0
    finally:
        app.dependency_overrides.clear()


def test_income_split_with_payable_line():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl3@example.com", "L_SDL3")
    try:
        salary = _create_category(client, hdr, ledger_id, token, "薪资", kind="income")
        res = _create_tx(
            client, hdr, ledger_id, token,
            tx_type="income",
            amount=3000.0,
            splits=[
                {"category_id": salary, "amount": 1000.0},
                {"amount": 2000.0, "debt": {"counterparty_name": "媽媽"}},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]
        debts = _debts(TS, tx_id)
        assert [(d.direction, d.principal_amount, d.counterparty_name) for d in debts] == [
            ("payable", 2000.0, "媽媽"),
        ]
        r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers=hdr)
        assert r.json()["income_total"] == 1000.0
    finally:
        app.dependency_overrides.clear()


def test_update_debt_line_amount_and_note_only_patch_keeps_it():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl4@example.com", "L_SDL4")
    try:
        tx_id = _create_receivable_split(client, hdr, ledger_id, token, cat_food)
        debt_id = _debts(TS, tx_id)[0].sync_id

        # 只改備註:不帶 splits,欠款明細和欠款都要留著。
        res = _patch_tx(client, hdr, ledger_id, token, tx_id, note="聚餐")
        assert res.status_code == 200, res.text
        tx = _read_tx(client, hdr, ledger_id, tx_id)
        assert [s["debt_id"] for s in tx["splits"]] == [None, debt_id]
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_id))
            assert row.debt_split_amount == 500.0

        # 應收 500 → 700(總額 1200),沿用同一個 debt_id,本金跟著改。
        res = _patch_tx(
            client, hdr, ledger_id, token, tx_id,
            amount=1200.0,
            splits=[
                {"category_id": cat_food, "amount": 500.0},
                {"amount": 700.0, "note": "代墊", "debt": {
                    "debt_id": debt_id, "counterparty_name": "小明",
                    "excluded_from_total": True,
                }},
            ],
        )
        assert res.status_code == 200, res.text
        debts = _debts(TS, tx_id)
        assert len(debts) == 1
        assert debts[0].sync_id == debt_id
        assert debts[0].principal_amount == 700.0
        assert debts[0].note == "代墊"
        assert debts[0].due_at is None
        assert debts[0].excluded_from_total is True

        # 改成收入:欠款方向跟著翻轉。
        salary = _create_category(client, hdr, ledger_id, token, "奖金", kind="income")
        res = _patch_tx(
            client, hdr, ledger_id, token, tx_id,
            tx_type="income",
            splits=[
                {"category_id": salary, "amount": 500.0},
                {"amount": 700.0, "debt": {"debt_id": debt_id, "counterparty_name": "小明"}},
            ],
        )
        assert res.status_code == 200, res.text
        assert _debts(TS, tx_id)[0].direction == "payable"
    finally:
        app.dependency_overrides.clear()


def test_remove_debt_line_with_repayment_conflicts_and_without_deletes():
    client, TS, token, hdr, ledger_id, cat_food, cat_transport = _setup("sdl5@example.com", "L_SDL5")
    try:
        tx_id = _create_receivable_split(client, hdr, ledger_id, token, cat_food)
        debt_id = _debts(TS, tx_id)[0].sync_id
        # 小明先還 200。
        res = _create_tx(client, hdr, ledger_id, token, tx_type="income", amount=200.0, debt_id=debt_id)
        assert res.status_code == 200, res.text

        tx = _read_tx(client, hdr, ledger_id, tx_id)
        assert tx["splits"][1]["debt_has_repayments"] is True
        assert tx["splits"][1]["debt_remaining_amount"] == 300.0
        assert tx["splits"][1]["debt_status"] == "partial"

        res = _patch_tx(
            client, hdr, ledger_id, token, tx_id,
            splits=[
                {"category_id": cat_food, "amount": 500.0},
                {"category_id": cat_transport, "amount": 500.0},
            ],
        )
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "SPLIT_DEBT_HAS_REPAYMENTS"
        assert len(_debts(TS, tx_id)) == 1
        tx = _read_tx(client, hdr, ledger_id, tx_id)
        assert tx["splits"][1]["debt_id"] == debt_id

        # 沒有還款的欠款明細移除 → 欠款一起刪。
        tx2 = _create_receivable_split(client, hdr, ledger_id, token, cat_food)
        res = _patch_tx(client, hdr, ledger_id, token, tx2, splits=[])
        assert res.status_code == 200, res.text
        assert _debts(TS, tx2) == []
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == tx2))
            assert row.has_splits is False
            assert row.debt_split_amount == 0.0
    finally:
        app.dependency_overrides.clear()


def test_validation_rules():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl6@example.com", "L_SDL6")
    try:
        # 只有欠款明細、沒有分類明細。
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=1000.0,
            splits=[
                {"amount": 500.0, "debt": {"counterparty_name": "小明"}},
                {"amount": 500.0, "debt": {"counterparty_name": "小華"}},
            ],
        )
        assert res.status_code == 400, res.text
        # 對象空白。
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=1000.0,
            splits=[
                {"category_id": cat_food, "amount": 500.0},
                {"amount": 500.0, "debt": {"counterparty_name": "  "}},
            ],
        )
        assert res.status_code == 400, res.text
        # 借用別筆欠款的 id。
        tx_id = _create_receivable_split(client, hdr, ledger_id, token, cat_food)
        foreign = _debts(TS, tx_id)[0].sync_id
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=1000.0,
            splits=[
                {"category_id": cat_food, "amount": 500.0},
                {"amount": 500.0, "debt": {"debt_id": foreign, "counterparty_name": "小明"}},
            ],
        )
        assert res.status_code == 400, res.text
        assert len(_debts(TS, tx_id)) == 1
    finally:
        app.dependency_overrides.clear()


def test_delete_tx_with_two_debt_lines_deletes_both_and_debt_api_guard():
    client, TS, token, hdr, ledger_id, cat_food, _ = _setup("sdl7@example.com", "L_SDL7")
    try:
        res = _create_tx(
            client, hdr, ledger_id, token,
            amount=900.0,
            splits=[
                {"category_id": cat_food, "amount": 300.0},
                {"amount": 400.0, "debt": {"counterparty_name": "小明"}},
                {"amount": 200.0, "debt": {"counterparty_name": "小華"}},
            ],
        )
        assert res.status_code == 200, res.text
        tx_id = res.json()["entity_id"]
        debts = _debts(TS, tx_id)
        assert sorted(d.principal_amount for d in debts) == [200.0, 400.0]

        # 欠款列表:各自顯示本金,標記來自拆帳。
        r = client.get(f"/api/v1/read/ledgers/{ledger_id}/debts", headers=hdr)
        listed = {d["counterparty_name"]: d for d in r.json()}
        assert listed["小明"]["from_split"] is True
        assert listed["小明"]["origin_transaction"]["amount"] == 400.0
        assert listed["小華"]["origin_transaction"]["amount"] == 200.0

        # 欠款 API 不能直接刪拆帳欠款。
        base = _latest_change_id(client, token, ledger_id)
        r = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/debts/{listed['小明']['id']}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "DEBT_FROM_SPLIT"

        base = _latest_change_id(client, token, ledger_id)
        r = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
            headers=hdr,
            json={"base_change_id": base},
        )
        assert r.status_code == 200, r.text
        assert _debts(TS, tx_id) == []
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_mutator_removes_all_unrepaid_origin_debts():
    snapshot = {
        "items": [
            {"syncId": "tx-a", "type": "expense", "amount": 900.0},
            {"syncId": "tx-r", "type": "income", "amount": 50.0, "debtId": "d2"},
        ],
        "debts": [
            {"syncId": "d1", "originTxId": "tx-a"},
            {"syncId": "d2", "originTxId": "tx-a"},
            {"syncId": "d3", "originTxId": "tx-a"},
            {"syncId": "d4", "originTxId": "tx-other"},
        ],
    }
    out = delete_transaction(snapshot, "tx-a")
    assert [d["syncId"] for d in out["debts"]] == ["d2", "d4"]


def test_app_pushed_debt_split_lines_survive_web_edit():
    client, TS = _make_client()
    try:
        owner = _register(client, "sdl8@example.com")
        hdr = {"Authorization": f"Bearer {owner['access_token']}"}
        now = _iso()
        _push(client, hdr, "lg1", "debt", "debt-app-1", {
            "syncId": "debt-app-1", "direction": "receivable", "counterpartyName": "小明",
            "principalAmount": 500.0, "originTxId": "tx-app-1", "excludedFromTotal": False,
        })
        _push(client, hdr, "lg1", "transaction", "tx-app-1", {
            "syncId": "tx-app-1", "type": "expense", "amount": 1000.0, "happenedAt": now,
            "splits": [
                {"categoryId": "cat-a", "categoryName": "餐饮", "amount": 500.0, "sortOrder": 0},
                {"categoryName": None, "amount": 500.0, "note": "代墊", "debtId": "debt-app-1"},
            ],
        })
        with TS() as db:
            row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == "tx-app-1"))
            assert row.debt_split_amount == 500.0
            split_rows = db.scalars(
                select(ReadTxSplitProjection).where(ReadTxSplitProjection.tx_sync_id == "tx-app-1")
            ).all()
            assert {s.debt_sync_id for s in split_rows} == {None, "debt-app-1"}

        web = {"Authorization": f"Bearer {_login_web(client, 'sdl8@example.com')['access_token']}"}
        r = client.get("/api/v1/read/ledgers/lg1/transactions", headers=web)
        assert r.status_code == 200, r.text
        tx = next(t for t in r.json() if t["id"] == "tx-app-1")
        assert tx["splits"][1]["debt_id"] == "debt-app-1"
        assert tx["splits"][1]["debt_counterparty_name"] == "小明"

        # Web 只改備註:欠款明細原樣保留。
        r = client.patch(
            "/api/v1/write/ledgers/lg1/transactions/tx-app-1",
            headers=web,
            json={"base_change_id": 0, "note": "聚餐"},
        )
        assert r.status_code == 200, r.text
        with TS() as db:
            change = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_type == "transaction", SyncChange.entity_sync_id == "tx-app-1",
                ).order_by(SyncChange.change_id.desc())
            ).first()
            assert change.payload_json["note"] == "聚餐"
            assert change.payload_json["splits"][1]["debtId"] == "debt-app-1"
            assert db.scalar(
                select(ReadDebtProjection).where(ReadDebtProjection.sync_id == "debt-app-1")
            ) is not None
    finally:
        app.dependency_overrides.clear()
