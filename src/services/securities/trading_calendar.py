"""各市場的交易日曆(週末 + 交易所休市日),給股票定期定額判斷「這一期遇到休市怎麼辦」。

休市日來自 `holidays.financial`(TWSE/NYSE/HKEX/JPX/SSE/SZSE/KRX/LSE),只涵蓋
「事先公告的」休市日;颱風停市這類臨時休市預測不到,不處理。

App(`lib/services/investment/trading_calendar.dart`)和 Web 沒有 `holidays`
套件,用 `scripts/export_trading_calendar.py` 匯出的靜態表(涵蓋年份有限,超出
範圍只剩週末判斷),判斷邏輯 **必須跟這個檔案同一套**:
- 順延/略過的規則見 `stock_dca_trade_time`;
- 以「該期的業務日期」(Cloud 用 `LEDGER_TIMEZONE`、App 用手機本地時區)查市場
  日曆,不換算成交易所所在地的當地日期——台灣使用者的每月 N 號,就是看 N 號
  這個日期那天該市場有沒有開。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from holidays import financial

# 市場代碼(`markets.MARKETS`)→ `holidays.financial` 的交易所類別。
# 櫃買(TWO)跟證交所同一份休市日;滬/深各自一份但實務上相同。
MARKET_EXCHANGE: dict[str, str] = {
    "TW": "TWSE",
    "TWO": "TWSE",
    "US": "NYSE",
    "HK": "HKEX",
    "JP": "JPX",
    "SS": "SSE",
    "SZ": "SZSE",
    "KS": "KRX",
    "KQ": "KRX",
    "LSE": "LSE",
}

# 匯出給 App/Web 的交易所(去重後)。
EXCHANGES: tuple[str, ...] = ("TWSE", "NYSE", "HKEX", "JPX", "SSE", "SZSE", "KRX", "LSE")

_MAX_SEARCH_DAYS = 30


@lru_cache(maxsize=64)
def _exchange_holidays(exchange: str, year: int) -> frozenset[date]:
    cls = getattr(financial, exchange)
    return frozenset(cls(years=year).keys())


def exchange_closures(exchange: str, year: int) -> list[date]:
    """某交易所某年「平日」的休市日(週末不列,由週末判斷處理),已排序。"""
    return sorted(d for d in _exchange_holidays(exchange, year) if d.weekday() < 5)


def exchange_closure_names(exchange: str, year: int) -> dict[date, str]:
    """同 [exchange_closures],附上休市原因(`holidays` 套件的名稱,給後台/除錯看)。"""
    cls = getattr(financial, exchange)
    names = cls(years=year)
    return {d: str(names.get(d) or "") for d in exchange_closures(exchange, year)}


def is_trading_day(market: str | None, day: date) -> bool:
    """週末與交易所休市日回 False。未知市場只看週末。"""
    if day.weekday() >= 5:
        return False
    exchange = MARKET_EXCHANGE.get((market or "").upper())
    if exchange is None:
        return True
    return day not in _exchange_holidays(exchange, day.year)


def next_trading_day(market: str | None, day: date) -> date:
    """[day] 當天或之後的第一個交易日。"""
    for offset in range(_MAX_SEARCH_DAYS):
        candidate = day + timedelta(days=offset)
        if is_trading_day(market, candidate):
            return candidate
    return day


def stock_dca_defers(frequency: str, advanced_rule: dict[str, Any] | None) -> bool:
    """休市時是順延(True)還是略過這一期(False)。

    月/年/每 N 週的規則一期之間隔得夠遠,順延到下一個交易日不會撞到下一期,
    比照券商「扣款日遇休市日順延」。每日、每週指定星期幾([weekly_days])的
    相鄰兩期可能順延到同一天(週五休市順延到週一、剛好週一也要買)造成重複
    扣款,這兩種直接略過休市日那一期。"""
    if advanced_rule is not None and advanced_rule.get("type") == "weekly_days":
        return False
    return frequency != "daily"


def stock_dca_trade_time(
    market: str | None,
    occurrence: datetime,
    *,
    frequency: str,
    advanced_rule: dict[str, Any] | None,
    tz: ZoneInfo,
) -> datetime | None:
    """某一期定期定額「實際該成交」的時間(保留原本的時刻、只換日期)。

    - 排定日是交易日 → 原樣回傳;
    - 休市且可順延([stock_dca_defers])→ 下一個交易日同一時刻;
    - 休市且不可順延 → None(這一期不買)。

    `occurrence` 本身(進度、syncId 的來源)維持不變,只有成交時間/是否到期
    用這個結果判斷。"""
    local = occurrence.astimezone(tz)
    day = local.date()
    if is_trading_day(market, day):
        return occurrence
    if not stock_dca_defers(frequency, advanced_rule):
        return None
    shifted = next_trading_day(market, day)
    return occurrence + timedelta(days=(shifted - day).days)


# ---------------------------------------------------------------------------
# 款券交割日(買進/賣出的錢實際扣/入帳的日子)
# ---------------------------------------------------------------------------

# 預設交割天數(交易日)。台股是 T+2;美股 2024-05 起結算是 T+1,但國內複委託
# 帳戶實際扣/入帳仍是 T+2,所以所有市場先統一 2,使用者每筆都能改。
# App `kStockSettlementLagDays`/Web 說明文字同一個數字。
STOCK_SETTLEMENT_LAG_DAYS = 2

# 交割日的「時刻」:一律當天 09:00(業務時區)——對應「開市時間才扣款」,也讓
# 當天稍晚才記的交易不會比扣款時間還早。App 同。
STOCK_SETTLEMENT_HOUR = 9


def add_trading_days(market: str | None, day: date, days: int) -> date:
    """從 [day] 起算,往後數 [days] 個交易日(`day` 本身當第 0 天,即使它休市)。
    例:週一成交、T+2 → 週三;週五成交 → 下週二;中間夾休市日再順延。"""
    current = day
    remaining = days
    while remaining > 0:
        current = next_trading_day(market, current + timedelta(days=1))
        remaining -= 1
    return current


def default_settlement_at(
    market: str | None,
    trade_at: datetime,
    *,
    tz: ZoneInfo,
    lag_days: int = STOCK_SETTLEMENT_LAG_DAYS,
) -> datetime:
    """預設交割時間:成交日(業務時區)起算第 [lag_days] 個交易日的 09:00。
    `lag_days <= 0` 回成交時間本身(沒有交割延遲)。回傳 aware datetime。
    對齊 App `TradingCalendar.defaultStockSettlementAt`。"""
    if lag_days <= 0:
        return trade_at
    if trade_at.tzinfo is None:
        trade_at = trade_at.replace(tzinfo=ZoneInfo("UTC"))
    day = trade_at.astimezone(tz).date()
    settle_day = add_trading_days(market, day, lag_days)
    return datetime.combine(settle_day, time(STOCK_SETTLEMENT_HOUR), tzinfo=tz)
