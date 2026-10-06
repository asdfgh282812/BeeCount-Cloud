"""業務時區(`LEDGER_TIMEZONE`)——「瞬間 ↔ 日期」轉換的單一事實來源。

為什麼需要:交易 `happened_at` 是 UTC 儲存的**瞬間**(instant),但信用卡回饋
活動起訖、自然月、帳單週期、結帳日、入帳日都是**日期**(曆法概念),屬於使用者
所在時區。以前各處手寫 `happened_at.date()`(= UTC 日期)與
`datetime(d.year, d.month, d.day, tzinfo=utc)`(= UTC 零點),台灣(UTC+8)使用者
00:00~08:00 的消費會被歸到前一天:活動起始日 10/06 凌晨 02:04(= 10/05 18:04Z)
的消費被判成活動前一天、每月 1 號凌晨的消費被算進上個月、結帳日邊界差一天。

**規則:instant(UTC 儲存)↔ date 的轉換一律走這個模組**,不要再散落手寫
`.date()` / `datetime(d.year, d.month, d.day, tzinfo=timezone.utc)`:

- instant → date:`to_business_date` / `business_today`
- date → instant(查詢邊界、入帳交易的 `happened_at`):
  `business_date_start_utc` / `business_date_end_utc`

**不要**做時區轉換的情況——「純日期」語意的欄位:
- 回饋規則 `starts_at` / `ends_at`:Web/App 送 `YYYY-MM-DDT00:00:00Z`,其 UTC 年月日
  就是使用者選的那個日期,維持 `.date()` 取 UTC 年月日(轉成業務時區反而會在
  UTC- 時區的部署上少一天)。
- 對外回傳的 `period_start` / `cycle_start` / `due_date` / `settlement_date` 等
  日期標籤:前端只取 ISO 字串前 10 碼,維持 UTC 零點編碼(各 router 內的
  `_date_to_utc_dt`),不是查詢邊界。

時區只有一個(整個部署共用 `LEDGER_TIMEZONE`),改值只影響日期歸屬的計算,
不用遷移任何已儲存資料。

測試:`business_tz()` 讀 `get_settings().ledger_timezone`;所有 helper 都接受
`tz=` 參數可直接指定時區(不碰設定),整合測試則 monkeypatch `business_tz`
或 `LEDGER_TIMEZONE` env + `get_settings.cache_clear()`。
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings


def business_tz() -> ZoneInfo:
    """目前部署的業務時區(`LEDGER_TIMEZONE`)。`ZoneInfo` 自帶快取,重複呼叫便宜;
    設定值在載入時已驗證過(見 `config.Settings._validate_ledger_timezone`)。"""
    return ZoneInfo(get_settings().ledger_timezone)


def _aware_utc(dt: datetime) -> datetime:
    """naive 一律視為 UTC。SQLite 讀回 `DateTime(timezone=True)` 欄位常是 naive
    的 UTC(Postgres 則是 aware),現有程式(`deferred_posting.attribution_date`、
    `credit_card_billing`)一律 `replace(tzinfo=utc)`,這裡保持一致。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def to_business_date(dt: datetime, *, tz: ZoneInfo | None = None) -> date:
    """瞬間 → 業務時區的日期。aware 先換算到業務時區再取日期;naive 視為 UTC。"""
    return _aware_utc(dt).astimezone(tz or business_tz()).date()


def business_today(now: datetime | None = None, *, tz: ZoneInfo | None = None) -> date:
    """「現在」在業務時區是哪一天。`now` 省略時取系統當下;排程/測試可傳入固定瞬間。"""
    return to_business_date(now or datetime.now(timezone.utc), tz=tz)


def business_date_start_utc(d: date, *, tz: ZoneInfo | None = None) -> datetime:
    """業務時區 `d` 當天 00:00:00 → UTC aware datetime(查詢下界 / 當天零點入帳)。"""
    return datetime(d.year, d.month, d.day, tzinfo=tz or business_tz()).astimezone(timezone.utc)


def business_date_end_utc(d: date, *, tz: ZoneInfo | None = None) -> datetime:
    """業務時區 `d` 當天 23:59:59.999999 → UTC aware datetime(查詢上界,含當天)。"""
    return datetime.combine(d, time.max, tzinfo=tz or business_tz()).astimezone(timezone.utc)


def business_datetime_with_time_of(
    d: date, source: datetime, *, tz: ZoneInfo | None = None,
) -> datetime:
    """業務時區的日期 `d` + `source` 瞬間在業務時區的時:分:秒 → UTC aware。
    回饋入帳交易用:入帳日當天、對齊來源消費的「牆上時間」(台灣使用者看到的
    時分秒與原消費相同,而不是被換算成 UTC 時分秒後偏 8 小時)。"""
    zone = tz or business_tz()
    local = _aware_utc(source).astimezone(zone)
    return datetime.combine(d, local.time(), tzinfo=zone).astimezone(timezone.utc)
