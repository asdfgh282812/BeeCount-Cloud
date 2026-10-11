"""報價開盤價解析(定期定額成交價 = 成交日開盤價):Yahoo 日線、證交所/櫃買、補期查詢。"""
from __future__ import annotations

from datetime import date, datetime, timezone

from src.services.securities.providers import twelvedata, twse, yahoo


def _ts(y, m, d, h=1):  # 台股 09:00 = 01:00 UTC
    return int(datetime(y, m, d, h, 0, tzinfo=timezone.utc).timestamp())


def _payload(*, bars, regular_time, currency="TWD", tz="Asia/Taipei", price=110.0):
    return {"chart": {"result": [{
        "meta": {
            "regularMarketPrice": price, "chartPreviousClose": 100.0, "currency": currency,
            "regularMarketTime": regular_time, "exchangeTimezoneName": tz, "longName": "X",
        },
        "timestamp": [t for t, _ in bars],
        "indicators": {"quote": [{"open": [o for _, o in bars]}]},
    }]}}


def test_yahoo_open_is_the_open_of_the_quote_day():
    p = _payload(bars=[(_ts(2026, 2, 23), 105.0)], regular_time=_ts(2026, 2, 23, 3))
    q = yahoo.parse_chart(p, market="TW", symbol="0050")
    assert q.price == 110.0 and q.open_price == 105.0


def test_yahoo_premarket_bar_of_previous_day_is_not_todays_open():
    # 盤前 Yahoo 回的是前一個交易日的 bar,regularMarketTime 也是前一天 → 日期一致,是那天的開盤。
    p = _payload(bars=[(_ts(2026, 2, 20), 90.0)], regular_time=_ts(2026, 2, 20, 5))
    assert yahoo.parse_chart(p, market="TW", symbol="0050").open_price == 90.0
    # 但 bar 的日期跟報價時間不同天時不能拿來當開盤價。
    p = _payload(bars=[(_ts(2026, 2, 20), 90.0)], regular_time=_ts(2026, 2, 23, 3))
    assert yahoo.parse_chart(p, market="TW", symbol="0050").open_price is None


def test_yahoo_open_missing_value_means_not_opened_yet():
    p = _payload(bars=[(_ts(2026, 2, 23), None)], regular_time=_ts(2026, 2, 23, 1))
    assert yahoo.parse_chart(p, market="TW", symbol="0050").open_price is None


def test_yahoo_london_pence_open_converted_like_price():
    p = _payload(
        bars=[(int(datetime(2026, 2, 23, 8, 0, tzinfo=timezone.utc).timestamp()), 12050.0)],
        regular_time=int(datetime(2026, 2, 23, 12, 0, tzinfo=timezone.utc).timestamp()),
        currency="GBp", tz="Europe/London", price=12100.0,
    )
    q = yahoo.parse_chart(p, market="LSE", symbol="VOD")
    assert q.price == 121.0 and q.open_price == 120.5


def test_day_open_statuses():
    p = _payload(
        bars=[(_ts(2026, 2, 20), 90.0), (_ts(2026, 2, 24), 108.0)], regular_time=_ts(2026, 2, 24, 3),
    )
    kw = dict(market="TW", symbol="0050")
    assert yahoo.parse_day_open(p, day=date(2026, 2, 20), **kw) == ("ok", 90.0)
    # 2/23 之後的 2/24 有 bar、2/23 沒有 → 那天沒成交
    assert yahoo.parse_day_open(p, day=date(2026, 2, 23), **kw) == ("no_trade", None)
    # 2/25 還沒有任何 bar → 還沒開盤/上游延遲,不能判斷
    assert yahoo.parse_day_open(p, day=date(2026, 2, 25), **kw) == ("unavailable", None)


def test_twse_and_tpex_bulk_rows_carry_open_price():
    twse_rows = [{"Date": "1150223", "Code": "0050", "Name": "元大台灣50", "OpeningPrice": "105.00",
                  "ClosingPrice": "110.00", "Change": "10.00"}]
    q = twse.parse_twse(twse_rows).quotes[0]
    assert q.open_price == 105.0 and q.price == 110.0
    tpex_rows = [{"Date": "1150223", "SecuritiesCompanyCode": "00679B", "CompanyName": "X",
                  "Open": "0.00", "Close": "30.00", "Change": "0.10"}]
    assert twse.parse_tpex(tpex_rows).quotes[0].open_price is None  # 0 = 當天無成交價


def test_twelvedata_quote_carries_open_price():
    q = twelvedata.parse_quote({"close": "110", "open": "105", "previous_close": "100",
                                "timestamp": _ts(2026, 2, 23, 3), "currency": "TWD"}, market="TW", symbol="0050")
    assert q.open_price == 105.0


def test_day_open_bar_without_open_value_is_unavailable_not_no_trade():
    p = _payload(bars=[(_ts(2026, 2, 23), None), (_ts(2026, 2, 24), 108.0)], regular_time=_ts(2026, 2, 24, 3))
    assert yahoo.parse_day_open(p, market="TW", symbol="0050", day=date(2026, 2, 23)) == ("unavailable", None)
