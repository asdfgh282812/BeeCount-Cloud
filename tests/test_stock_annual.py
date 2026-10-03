"""年度報告股票摘要(services/securities/annual.py):純函式 + `/workspace/stock-annual`。"""
from __future__ import annotations

import pytest

from src.services.securities import annual
from src.services.securities.holdings import TradeRow


def _t(sid, trade_type, date, *, shares=0.0, price=None, amount=0.0, fee=0.0, tax=0.0,
       symbol="2330", market="TW", account="a1", ccy="TWD", name=None):
    return TradeRow(
        sync_id=sid, account_id=account, market=market, symbol=symbol, trade_type=trade_type,
        shares=shares, price=price, fee=fee, tax=tax, amount=amount, trade_date=date,
        security_name=name, currency=ccy,
    )


def test_cost_carries_over_previous_year_and_only_counts_this_year():
    rows = [
        _t("b0", "buy", "2025-06-01", shares=100, price=100, amount=10000),
        _t("b1", "buy", "2026-01-10", shares=100, price=200, amount=20000, fee=20),
        _t("s1", "sell", "2026-03-05", shares=100, price=180, amount=18000, fee=20, tax=54),
    ]
    out = annual.build_stock_annual(rows, 2026)
    [c] = out["currencies"]
    # 平均成本 (10000+20000)/200=150,賣 100 股成本 15000 → 損益 +3000
    assert c["realized_pnl"] == pytest.approx(3000)
    assert c["buy_count"] == 1 and c["sell_count"] == 1
    assert c["buy_amount"] == pytest.approx(20000)
    assert c["fees"] == pytest.approx(40) and c["taxes"] == pytest.approx(54)
    assert c["win_count"] == 1 and c["win_rate"] == pytest.approx(100)
    assert c["monthly_realized_pnl"][2] == pytest.approx(3000)
    assert c["best_sell"]["pnl"] == pytest.approx(3000) and c["worst_sell"] is None
    assert annual.build_stock_annual(rows, 2025)["currencies"][0]["sell_count"] == 0


def test_no_activity_when_only_opening_or_other_year():
    rows = [_t("o", "opening", "2026-01-01", shares=10, amount=1000)]
    assert annual.build_stock_annual(rows, 2026) == {"year": 2026, "has_activity": False, "currencies": []}


def test_win_rate_best_worst_and_top_symbols():
    rows = [
        _t("b1", "buy", "2026-01-02", shares=10, price=100, amount=1000, symbol="AAA", name="甲"),
        _t("b2", "buy", "2026-01-03", shares=10, price=100, amount=1000, symbol="BBB", name="乙"),
        _t("s1", "sell", "2026-02-02", shares=10, price=150, amount=1500, symbol="AAA"),
        _t("s2", "sell", "2026-02-03", shares=10, price=70, amount=700, symbol="BBB"),
        _t("d1", "cash_dividend", "2026-07-01", amount=50, symbol="AAA"),
        _t("d2", "cash_dividend", "2026-08-01", amount=30, symbol="BBB"),
    ]
    [c] = annual.build_stock_annual(rows, 2026)["currencies"]
    assert c["win_count"] == 1 and c["loss_count"] == 1 and c["win_rate"] == pytest.approx(50)
    assert c["best_sell"]["symbol"] == "AAA" and c["best_sell"]["return_percent"] == pytest.approx(50)
    assert c["worst_sell"]["symbol"] == "BBB" and c["worst_sell"]["pnl"] == pytest.approx(-300)
    assert c["top_dividend_symbol"]["symbol"] == "AAA" and c["top_dividend_symbol"]["amount"] == 50
    assert c["top_symbol_by_trades"]["count"] == 2
    assert c["dividends"] == 80 and c["monthly_dividends"][6] == 50 and c["monthly_dividends"][7] == 30
    assert c["market_breakdown"] == {"TW": 4}


def test_currencies_split_and_sorted_by_activity():
    rows = [
        _t("b1", "buy", "2026-01-02", shares=1, price=10, amount=10, symbol="TSM"),
        _t("b2", "buy", "2026-01-03", shares=10, price=100, amount=1000, symbol="AAPL", market="US",
           ccy="USD", account="a2"),
    ]
    out = annual.build_stock_annual(rows, 2026)
    assert [c["currency"] for c in out["currencies"]] == ["USD", "TWD"]


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(buy_count=30, sell_count=15, dividends=0, dividend_count=0, realized=0), annual.STYLE_ACTIVE),
        (dict(buy_count=3, sell_count=1, dividends=500, dividend_count=3, realized=100), annual.STYLE_DIVIDEND),
        (dict(buy_count=6, sell_count=0, dividends=0, dividend_count=0, realized=0), annual.STYLE_LONG_TERM),
        (dict(buy_count=3, sell_count=3, dividends=0, dividend_count=0, realized=0), annual.STYLE_SWING),
        (dict(buy_count=1, sell_count=1, dividends=0, dividend_count=0, realized=0), annual.STYLE_BEGINNER),
    ],
)
def test_style_tag(kwargs, expected):
    assert annual._style_tag(**kwargs) == expected
