"""股票定期定額「這一期用什麼價格、現在能不能成交」的判斷(純函式,不碰 DB/網路)。

規則(2026-10-12,使用者要求):定期定額一律以**成交日當天的開盤價**成交,
不能拿快取裡「上次」的價格去買。成交日 [target_day] 是市場當地日期(排定日遇休市
已先順延,見 `trading_calendar.stock_dca_trade_time`)。

資料就是閘門:只有拿到「報價所屬交易日 == 成交日」且帶有開盤價的資料才成交。
- 還沒開盤 / 上游還沒給當天資料 → `WAIT`(靜默等下一輪,不通知);
- 休市日表過期、臨時休市:那天根本沒有開盤價,同樣 `WAIT` 到補期上限,不會誤買;
- 成交日已過、快取是更晚的報價(App/排程很晚才跑)→ 需要查那天的日線開盤價
  (`NEED_DAY_OPEN`,呼叫方去抓後再帶 [day_open] 進來重判);
- 日線涵蓋到成交日之後卻沒有那天的 bar → `NO_TRADE`(停牌/當天沒成交,略過這期)。

App `lib/services/investment/stock_dca.dart::decideStockDcaPrice` 必須同一套
(App 沒有時區資料庫,所以報價所屬日期 `quote_date` 由 server 算好隨報價下發)。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

OK = "ok"
WAIT = "wait"
NEED_DAY_OPEN = "need_day_open"
NO_TRADE = "no_trade"
MISSING = "missing"


@dataclass(frozen=True)
class PriceDecision:
    status: str
    price: float | None = None


def decide(
    *,
    has_quote: bool,
    quote_date: str | None,
    open_price: float | None,
    target_day: date,
    market_today: date,
    day_open: tuple[str, float | None] | None = None,
    slack_days: int = 0,
) -> PriceDecision:
    """[quote_date] 是報價所屬的市場當地日期 `yyyy-MM-dd`;[market_today] 是「現在」
    的市場當地日期;[day_open] 是 `yahoo.fetch_day_open` 的結果(還沒查 = None)。

    [slack_days]:[market_today] 的不確定範圍(±天)。Cloud 有時區資料庫,精確 = 0;
    App 只知道手機本地日期,市場當地日期可能差一天(台灣看美股),傳 1——「一定在未來」
    「一定已過」都以 ±1 天的範圍判斷,範圍內一律當成「可能就是今天」去等,寧可晚一點
    報缺料也不誤報。"""
    target = target_day.isoformat()
    certainly_future = target_day > market_today + timedelta(days=slack_days)
    maybe_today_or_future = target_day >= market_today - timedelta(days=slack_days)
    # 成交日還沒到(順延到未來的交易日):等。
    if certainly_future:
        return PriceDecision(WAIT)
    # 快取就是成交日當天的資料:有開盤價直接用。
    if quote_date == target and open_price is not None and open_price > 0:
        return PriceDecision(OK, open_price)
    # 成交日就是今天、快取還停在更早的交易日:當天還沒開盤/上游還沒給資料。
    if has_quote and quote_date is not None and quote_date < target and maybe_today_or_future:
        return PriceDecision(WAIT)
    # 其餘情況(快取是更晚的報價、同一天但沒帶開盤價、完全沒有報價)要查那天的日線。
    if day_open is None:
        return PriceDecision(NEED_DAY_OPEN)
    status, price = day_open
    if status == "ok" and price is not None and price > 0:
        return PriceDecision(OK, price)
    if status == "no_trade":
        return PriceDecision(NO_TRADE)
    # unavailable:成交日是今天且有報價 → 還沒開盤,等;否則(成交日已過/從沒報價)= 資料缺失。
    if has_quote and maybe_today_or_future:
        return PriceDecision(WAIT)
    return PriceDecision(MISSING)
