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

from datetime import date, datetime, timedelta
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
