"""年度記帳報告的「股票年度摘要」(純函式,不碰 DB)。

口徑跟 `/workspace/realized-pnl`、`/workspace/investment-flow` 一致:
- 各幣別分開、不跨幣別加總;
- 已實現損益一律用「全部歷史」算出每筆賣出的成本,再過濾年度(先過濾再算,
  移動平均成本會跑掉);
- 買進金額含手續費、賣出金額為已扣費稅的淨額(= 明細 amount);
- 股利 = cash_dividend + reinvest 的 amount。

App 端對應 `lib/services/investment/stock_annual_report.dart`,規則同一份。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from . import holdings as holdings_service
from .holdings import TradeRow

# 風格標籤,依優先序取第一個符合者(順序即優先序)。
STYLE_ACTIVE = "active_trader"
STYLE_DIVIDEND = "dividend_hunter"
STYLE_LONG_TERM = "long_term_holder"
STYLE_SWING = "swing_trader"
STYLE_BEGINNER = "beginner"

_TRADING_TYPES = ("buy", "sell")
_DIVIDEND_TYPES = ("cash_dividend", "reinvest")
# 期初持股/配股/分割不是當年的買賣或現金流,不算「當年有股票活動」。
_COUNTED_TYPES = _TRADING_TYPES + _DIVIDEND_TYPES


def _year_of(date: str | None) -> int | None:
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None


def _month_of(date: str | None) -> int | None:
    if date and len(date) >= 7 and date[5:7].isdigit():
        m = int(date[5:7])
        if 1 <= m <= 12:
            return m
    return None


def _event_view(e: holdings_service.RealizedEvent) -> dict[str, Any]:
    rate = (e.pnl / e.cost_basis * 100) if e.cost_basis > 0 else None
    return {
        "market": e.market,
        "symbol": e.symbol,
        "security_name": e.security_name,
        "date": e.date,
        "pnl": round(e.pnl, 6),
        "proceeds": round(e.proceeds, 6),
        "cost_basis": round(e.cost_basis, 6),
        "return_percent": round(rate, 4) if rate is not None else None,
    }


def _style_tag(*, buy_count: int, sell_count: int, dividends: float, dividend_count: int, realized: float) -> str:
    trades = buy_count + sell_count
    if trades >= 40:
        return STYLE_ACTIVE
    if dividend_count >= 2 and dividends > 0 and dividends >= abs(realized):
        return STYLE_DIVIDEND
    if buy_count > 0 and (sell_count == 0 or buy_count >= 3 * sell_count):
        return STYLE_LONG_TERM
    if trades < 5:
        return STYLE_BEGINNER
    return STYLE_SWING


def build_stock_annual(
    rows: Iterable[TradeRow],
    year: int,
    *,
    account_ids: set[str] | None = None,
    account_currency: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """回傳 `{year, has_activity, currencies: [...]}`,currencies 依活躍度
    (買進+賣出金額)由大到小排序。"""
    account_currency = account_currency or {}
    all_rows = [r for r in rows if account_ids is None or r.account_id in account_ids]

    def ccy_of(currency: str | None, account_id: str | None) -> str:
        return (currency or account_currency.get(account_id or "") or "").upper()

    events: list[holdings_service.RealizedEvent] = []
    holdings_service.compute_holdings(all_rows, include_closed=True, events=events)

    def new_bucket() -> dict[str, Any]:
        return {
            "buy_count": 0, "sell_count": 0, "dividend_count": 0,
            "buy_amount": 0.0, "sell_amount": 0.0, "fees": 0.0, "taxes": 0.0,
            "dividends": 0.0, "realized": 0.0,
            "win": 0, "loss": 0,
            "symbols": set(), "trades_by_symbol": defaultdict(int), "name": {},
            "div_by_symbol": defaultdict(float),
            "monthly_pnl": [0.0] * 12, "monthly_div": [0.0] * 12,
            "markets": defaultdict(int),
            "events": [],
        }

    buckets: dict[str, dict[str, Any]] = defaultdict(new_bucket)

    for t in all_rows:
        date = holdings_service._date_key(t.trade_date) or None
        if _year_of(date) != year or t.trade_type not in _COUNTED_TYPES:
            continue
        b = buckets[ccy_of(t.currency, t.account_id)]
        key = f"{t.market}:{t.symbol}"
        if t.security_name:
            b["name"][key] = t.security_name
        if t.trade_type in _TRADING_TYPES:
            b["symbols"].add(key)
            b["trades_by_symbol"][key] += 1
            b["markets"][t.market] += 1
            b["fees"] += t.fee
            b["taxes"] += t.tax
            if t.trade_type == "buy":
                b["buy_count"] += 1
                b["buy_amount"] += t.amount
            else:
                b["sell_count"] += 1
                b["sell_amount"] += t.amount
        elif t.trade_type in _DIVIDEND_TYPES:
            b["symbols"].add(key)
            b["dividend_count"] += 1
            b["dividends"] += t.amount
            b["div_by_symbol"][key] += t.amount
            m = _month_of(date)
            if m:
                b["monthly_div"][m - 1] += t.amount

    for e in events:
        if _year_of(e.date) != year or (account_ids is not None and e.account_id not in account_ids):
            continue
        b = buckets[ccy_of(e.currency, e.account_id)]
        b["realized"] += e.pnl
        b["events"].append(e)
        if e.pnl > 0:
            b["win"] += 1
        elif e.pnl < 0:
            b["loss"] += 1
        m = _month_of(e.date)
        if m:
            b["monthly_pnl"][m - 1] += e.pnl

    def sym_view(key: str, b: dict[str, Any], **extra: Any) -> dict[str, Any]:
        market, symbol = key.split(":", 1)
        return {"market": market, "symbol": symbol, "security_name": b["name"].get(key), **extra}

    currencies: list[dict[str, Any]] = []
    for ccy, b in buckets.items():
        evs: list[holdings_service.RealizedEvent] = b["events"]
        best = max(evs, key=lambda e: e.pnl) if evs else None
        worst = min(evs, key=lambda e: e.pnl) if evs else None
        # 只有真的賺/賠才算亮點(全部持平就不顯示最賺/最賠)。
        if best is not None and best.pnl <= 0:
            best = None
        if worst is not None and worst.pnl >= 0:
            worst = None
        decided = b["win"] + b["loss"]
        top_trades = max(b["trades_by_symbol"].items(), key=lambda kv: (kv[1], kv[0]), default=None)
        top_div = max(b["div_by_symbol"].items(), key=lambda kv: (kv[1], kv[0]), default=None)
        currencies.append({
            "currency": ccy,
            "buy_count": b["buy_count"],
            "sell_count": b["sell_count"],
            "dividend_count": b["dividend_count"],
            "symbol_count": len(b["symbols"]),
            "buy_amount": round(b["buy_amount"], 2),
            "sell_amount": round(b["sell_amount"], 2),
            "fees": round(b["fees"], 2),
            "taxes": round(b["taxes"], 2),
            "dividends": round(b["dividends"], 2),
            "realized_pnl": round(b["realized"], 2),
            "win_count": b["win"],
            "loss_count": b["loss"],
            "win_rate": round(b["win"] / decided * 100, 2) if decided else None,
            "best_sell": _event_view(best) if best else None,
            "worst_sell": _event_view(worst) if worst else None,
            "top_symbol_by_trades": sym_view(top_trades[0], b, count=top_trades[1]) if top_trades else None,
            "top_dividend_symbol": sym_view(top_div[0], b, amount=round(top_div[1], 2)) if top_div else None,
            "monthly_realized_pnl": [round(v, 2) for v in b["monthly_pnl"]],
            "monthly_dividends": [round(v, 2) for v in b["monthly_div"]],
            "market_breakdown": dict(sorted(b["markets"].items(), key=lambda kv: -kv[1])),
            "style_tag": _style_tag(
                buy_count=b["buy_count"], sell_count=b["sell_count"], dividends=b["dividends"],
                dividend_count=b["dividend_count"], realized=b["realized"],
            ),
        })

    currencies.sort(key=lambda c: (-(c["buy_amount"] + c["sell_amount"] + c["dividends"]), c["currency"]))
    return {"year": year, "has_activity": bool(currencies), "currencies": currencies}
