"""台股費用對帳(2026-09-28):價金/手續費/稅無條件捨去、證交稅依標的類型、預估變現淨值。

跟 App test/services/investment/investment_settings_test.dart「台股費用對帳」、Web
apps/web/src/investmentFees.test.ts 用同一組數字(跟永豐對帳單核對過)。
"""
from __future__ import annotations

import pytest

from src import snapshot_mutator
from src.main import app
from src.services.securities import trade_fees
from src.services.securities.providers import yahoo
from tests.test_stock_holdings import _buy, _fake_quote, _make_client, _setup, _trade_rows, _tx_row


def test_security_kind_from_symbol():
    assert trade_fees.security_kind("TW", "0050") == trade_fees.KIND_ETF
    assert trade_fees.security_kind("TW", "00878") == trade_fees.KIND_ETF
    assert trade_fees.security_kind("TW", "00631L") == trade_fees.KIND_ETF
    assert trade_fees.security_kind("TWO", "00679B") == trade_fees.KIND_BOND_ETF
    assert trade_fees.security_kind("TW", "2330") == trade_fees.KIND_STOCK
    assert trade_fees.security_kind("US", "0050") == trade_fees.KIND_STOCK


def test_0050_buy_total_cost_is_4878():
    # 50 × 97.45 = 4,872.5 → 捨去 4,872,加手續費 6 = 4,878
    assert trade_fees.stock_gross(50, 97.45, "TWD") == 4872
    assert snapshot_mutator.stock_trade_amount("buy", 50, 97.45, 6, 0, "TWD") == 4878
    fields = snapshot_mutator.stock_trade_tx_fields(
        trade_type="buy", shares=50, price=97.45, fee=6, tax=0,
        security_currency="TWD", settlement_currency="TWD", settlement_amount=None,
    )
    assert fields == {"amount": 4872, "feeAmount": 6}


def test_0050_sell_uses_etf_tax_rate():
    assert trade_fees.sell_tax_rate_for("TW", "0050", {}) == 0.001
    # 5,620 × 0.1% = 5.62 → 5
    assert trade_fees.suggest_sell_tax(5620, "TW", "0050", "TWD", {}) == 5
    assert trade_fees.suggest_sell_tax(5620, "TW", "2330", "TWD", {}) == 16
    assert trade_fees.suggest_sell_tax(5620, "TW", "00679B", "TWD", {}) == 0


def test_custom_stock_rate_does_not_override_etf_rate():
    assert trade_fees.sell_tax_rate_for("TW", "0050", {"sellTaxRate": 0.003}) == 0.001
    assert trade_fees.sell_tax_rate_for("TW", "0050", {"etfSellTaxRate": 0.0005}) == 0.0005


def test_rounding_cleans_float_noise():
    assert trade_fees.stock_gross(1000, 600.1, "TWD") == 600100
    assert trade_fees.stock_gross(3, 10.005, "USD") == pytest.approx(30.02)


def test_estimate_sell_net_value():
    e = trade_fees.estimate_sell(
        shares=50, price=112.40, market="TW", symbol="0050", currency="TWD",
        settings={"feeDiscount": 0.6, "feeMin": 1},
    )
    # 5620 × 0.001425 × 0.6 = 4.8 → 4;稅 5.62 → 5
    assert (e.gross, e.fee, e.tax, e.net) == (5620, 4, 5, 5611)


def test_odd_lot_estimate_matches_broker_statement():
    # 2026-10-03 永豐庫存:0050 50 股、現價 112.8、付出成本 4,878 → 現值 5,627、損益 749
    e = trade_fees.estimate_sell(shares=50, price=112.8, market="TW", symbol="0050", currency="TWD", settings={})
    assert (e.gross, e.fee, e.tax, e.net) == (5640, 8, 5, 5627)
    assert e.net - 4878 == 749
    # 使用者明確設了整股最低 20,零股仍用自己的最低手續費
    e = trade_fees.estimate_sell(
        shares=50, price=112.8, market="TW", symbol="0050", currency="TWD", settings={"feeMin": 20},
    )
    assert e.fee == 8
    e = trade_fees.estimate_sell(
        shares=50, price=112.8, market="TW", symbol="0050", currency="TWD", settings={"oddLotFeeMin": 20},
    )
    assert e.fee == 20


def test_mixed_board_and_odd_lot_are_separate_orders():
    # 1,050 股 = 1,000 股整股 + 50 股零股,兩張單各自算手續費與稅
    assert trade_fees.order_parts(118440, 1050, "TW", "TWD") == [(112800, False), (5640, True)]
    e = trade_fees.estimate_sell(shares=1050, price=112.8, market="TW", symbol="2330", currency="TWD", settings={})
    # 整股:⌊112,800 × 0.1425%⌋ = 160;零股:⌊5,640 × 0.1425%⌋ = 8
    assert e.fee == 160 + 8
    # 稅:⌊112,800 × 0.3%⌋ = 338;⌊5,640 × 0.3%⌋ = 16
    assert e.tax == 338 + 16
    # 整股小金額仍套整股最低 20
    assert trade_fees.suggest_fee(10000, "TW", "TWD", {}, shares=1000) == 20
    assert trade_fees.suggest_fee(100, "TW", "TWD", {}, shares=10) == 1
    # 沒給股數或非台股:不拆,維持 feeMin
    assert trade_fees.suggest_fee(5640, "TW", "TWD", {}) == 20
    assert trade_fees.order_parts(5640, 50, "US", "USD") == [(5640, False)]


def test_new_settings_keys_survive_normalization():
    out = snapshot_mutator.normalize_investment_settings(
        {"etfSellTaxRate": 0.001, "bondEtfSellTaxRate": 0, "pnlAfterSellCosts": False, "oddLotFeeMin": 1, "junk": 1}
    )
    assert out == {
        "etfSellTaxRate": 0.001, "bondEtfSellTaxRate": 0.0, "pnlAfterSellCosts": False, "oddLotFeeMin": 1.0,
    }


def test_web_buy_0050_floors_gross():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "fees-buy@t.com")
        r = _buy(client, hdr_web, symbol="0050", security_name="元大台灣50", shares=50, price=97.45, fee=6)
        assert r.status_code == 200, r.text
        [trade] = _trade_rows(TS)
        assert trade.amount == 4878
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.amount == 4872
    finally:
        app.dependency_overrides.clear()


def test_holdings_pnl_toggle_uses_gross_when_off(monkeypatch):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "fees-pnl@t.com")
        _buy(client, hdr_web, symbol="0050", security_name="元大台灣50", shares=50, price=97.45, fee=6)
        monkeypatch.setattr(yahoo, "fetch_quote", _fake_quote(112.40, symbol="0050"))
        body = client.get("/api/v1/read/workspace/holdings", headers=hdr_web).json()
        [h] = body["accounts"][0]["holdings"]
        # 預設:零股最低手續費 1 → ⌊5,620 × 0.1425%⌋ = 8、ETF 稅 5 → 淨值 5,607
        assert (h["est_sell_fee"], h["est_sell_tax"], h["net_value"]) == (8, 5, 5607)
        assert h["unrealized_pnl"] == pytest.approx(5607 - 4878)

        from tests.test_stock_holdings import _push
        _push(client, hdr_app, "lg1", "account", "acc_inv",
              {"syncId": "acc_inv", "investmentSettings": {"pnlAfterSellCosts": False}})
        body = client.get("/api/v1/read/workspace/holdings?refresh=false", headers=hdr_web).json()
        [h] = body["accounts"][0]["holdings"]
        assert h["pnl_after_sell_costs"] is False
        assert h["net_value"] == 5607
        assert h["unrealized_pnl"] == pytest.approx(5620 - 4878)
        assert body["pnl_after_sell_costs"] is False
    finally:
        app.dependency_overrides.clear()
