"""比較矩陣(`GET /workspace/comparison-matrix` + `/cell`)——對齊
doc.moze.app/analysis/comparison-report:列 = 月份、欄 = 比較項目,右側
每月合計 + 月增率、底部各欄合計/平均;點格子下鑽看交易明細。口徑跟
`/workspace/comparison` 共用 `_stat_legs`(本位幣、退款 netting、拆帳展開)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from tests.test_comparison_report import _push, _setup


def _tx(month, day, amount, tx_type, category, *, year=2026, **extra):
    return {
        "type": tx_type,
        "amount": amount,
        "happenedAt": datetime(year, month, day, 12, tzinfo=timezone.utc).isoformat(),
        "categoryName": category,
        **extra,
    }


def _get(client, hdr, ledger_id, **params):
    r = client.get(
        "/api/v1/read/workspace/comparison-matrix", headers=hdr,
        params={"ledger_id": ledger_id, **params},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_matrix_expense_category_rows_totals_mom_and_parent_rollup():
    client, _TS, hdr_app, device, _token, hdr, ledger_id = _setup("mx1@example.com", "L_MX1")
    try:
        # 一級「生活」底下有二級「餐飲」;子分類交易要併進一級欄位
        _push(client, hdr_app, ledger_id, "category", "c_life",
              {"syncId": "c_life", "name": "生活", "kind": "expense", "level": 1}, device_id=device)
        _push(client, hdr_app, ledger_id, "category", "c_food",
              {"syncId": "c_food", "name": "餐飲", "kind": "expense", "level": 2,
               "parentName": "生活"}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t1",
              {"syncId": "t1", **_tx(1, 10, 100.0, "expense", "餐飲", categoryId="c_food")},
              device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t2",
              {"syncId": "t2", **_tx(1, 11, 50.0, "expense", "生活", categoryId="c_life")},
              device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t3",
              {"syncId": "t3", **_tx(2, 3, 300.0, "expense", "娛樂")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t4",
              {"syncId": "t4", **_tx(2, 4, 9999.0, "income", "薪水")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t5",
              {"syncId": "t5", **_tx(2, 5, 70.0, "expense", "娛樂"), "excludeFromStats": True},
              device_id=device)

        data = _get(client, hdr, ledger_id, dimension="expense_category",
                    start="2026-01", end="2026-03")
        assert [r["month"] for r in data["rows"]] == ["2026-01", "2026-02", "2026-03"]
        assert [c["key"] for c in data["columns"]] == ["娛樂", "生活"], "依合計降序"
        jan, feb, mar = data["rows"]
        assert jan["values"] == {"娛樂": 0.0, "生活": 150.0}
        assert jan["total"] == 150.0
        assert feb["total"] == 300.0
        assert feb["mom_pct"] == 100.0
        assert mar["total"] == 0.0
        assert jan["mom_pct"] is None
        assert data["column_totals"] == {"娛樂": 300.0, "生活": 150.0}
        assert data["column_averages"] == {"娛樂": 100.0, "生活": 50.0}
        assert data["grand_total"] == 450.0

        sub = _get(client, hdr, ledger_id, dimension="expense_subcategory",
                   start="2026-01", end="2026-01")
        cols = {c["key"]: c for c in sub["columns"]}
        assert cols["生活›餐飲"]["label"] == "餐飲"
        assert cols["生活›餐飲"]["parent_label"] == "生活"
        assert sub["rows"][0]["values"]["生活›餐飲"] == 100.0
        assert sub["rows"][0]["values"]["生活"] == 50.0

        cell = client.get(
            "/api/v1/read/workspace/comparison-matrix/cell", headers=hdr,
            params={"ledger_id": ledger_id, "dimension": "expense_category",
                    "month": "2026-01", "column_key": "生活"},
        )
        assert cell.status_code == 200, cell.text
        cd = cell.json()
        assert cd["total"] == 150.0
        assert [i["sync_id"] for i in cd["items"]] == ["t2", "t1"], "時間新到舊"
    finally:
        client.close()


def test_matrix_record_type_balance_and_refund_netting():
    client, _TS, hdr_app, device, _token, hdr, ledger_id = _setup("mx2@example.com", "L_MX2")
    try:
        _push(client, hdr_app, ledger_id, "transaction", "t1",
              {"syncId": "t1", **_tx(7, 5, 200.0, "expense", "購物")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t2",
              {"syncId": "t2", **_tx(7, 6, 80.0, "income", "購物", refundOfId="t1")},
              device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t3",
              {"syncId": "t3", **_tx(7, 7, 1000.0, "income", "薪水")}, device_id=device)

        data = _get(client, hdr, ledger_id, dimension="record_type",
                    start="2026-07", end="2026-07")
        assert [c["key"] for c in data["columns"]] == ["expense", "income", "balance"]
        row = data["rows"][0]
        assert row["values"] == {"expense": 120.0, "income": 1000.0, "balance": 880.0}
        assert row["total"] == 880.0
        assert data["grand_total"] == 880.0

        cell = client.get(
            "/api/v1/read/workspace/comparison-matrix/cell", headers=hdr,
            params={"ledger_id": ledger_id, "dimension": "record_type",
                    "month": "2026-07", "column_key": "expense"},
        ).json()
        assert cell["total"] == 120.0
        refund = next(i for i in cell["items"] if i["sync_id"] == "t2")
        assert refund["amount"] == -80.0
        assert refund["is_refund"] is True
    finally:
        client.close()


def test_matrix_project_and_account_group_with_none_column_last():
    client, _TS, hdr_app, device, _token, hdr, ledger_id = _setup("mx3@example.com", "L_MX3")
    try:
        _push(client, hdr_app, ledger_id, "project", "p1",
              {"syncId": "p1", "name": "旅行"}, device_id=device)
        _push(client, hdr_app, ledger_id, "account", "g1",
              {"syncId": "g1", "name": "主卡", "type": "account_group"}, device_id=device)
        _push(client, hdr_app, ledger_id, "account", "a1",
              {"syncId": "a1", "name": "附卡", "type": "credit_card", "parentAccountId": "g1"},
              device_id=device)
        _push(client, hdr_app, ledger_id, "account", "a2",
              {"syncId": "a2", "name": "現金", "type": "cash"}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t1",
              {"syncId": "t1", **_tx(3, 1, 10.0, "expense", "餐飲", projectId="p1",
                                     accountId="a1")}, device_id=device)
        _push(client, hdr_app, ledger_id, "transaction", "t2",
              {"syncId": "t2", **_tx(3, 2, 500.0, "expense", "餐飲", accountId="a2")},
              device_id=device)

        proj = _get(client, hdr, ledger_id, dimension="project", start="2026-03", end="2026-03")
        assert [c["key"] for c in proj["columns"]] == ["p1", "__none__"], "(無) 固定最後"
        assert proj["columns"][0]["label"] == "旅行"
        assert proj["rows"][0]["values"] == {"p1": 10.0, "__none__": 500.0}

        grp = _get(client, hdr, ledger_id, dimension="account_group",
                   start="2026-03", end="2026-03")
        cols = {c["key"]: c["label"] for c in grp["columns"]}
        assert cols == {"g1": "主卡", "__none__": ""}
        assert grp["rows"][0]["values"]["g1"] == 10.0

        income = _get(client, hdr, ledger_id, dimension="project", kind="income",
                      start="2026-03", end="2026-03")
        assert income["kind"] == "income"
        assert income["columns"] == []
    finally:
        client.close()


def test_matrix_rejects_bad_ranges():
    client, _TS, _hdr_app, _device, _token, hdr, ledger_id = _setup("mx4@example.com", "L_MX4")
    try:
        r = client.get(
            "/api/v1/read/workspace/comparison-matrix", headers=hdr,
            params={"ledger_id": ledger_id, "start": "2026-13", "end": "2026-14"},
        )
        assert r.status_code == 400
        r = client.get(
            "/api/v1/read/workspace/comparison-matrix", headers=hdr,
            params={"ledger_id": ledger_id, "start": "2000-01", "end": "2026-01"},
        )
        assert r.status_code == 400
        # 顛倒的區間自動校正
        data = _get(client, hdr, ledger_id, start="2026-03", end="2026-01")
        assert [r["month"] for r in data["rows"]] == ["2026-01", "2026-02", "2026-03"]
    finally:
        client.close()
