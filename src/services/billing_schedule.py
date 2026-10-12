"""信用卡帳單排程(彈性結帳日 + 繳款期限,MOZE 對標)。

對應 App `lib/utils/billing_schedule.dart`——**兩邊的算法必須一致**(結帳日遇非工作日
延後/提前、單期覆寫、繳款期限兩種模式),改這裡要一起改 App。純函式、無 DB 依賴;
要不要從 DB 讀節日資料由 `load_calendar` 負責,`credit_card.py` 的週期函式只吃
`BillingSchedule`。

- `billing_day`:每月結帳日(1..31,超過當月天數夾到月底);
- `holiday_rule` + `region`:結帳日遇「非工作日」(週末 + 該地區放假日)延後/提前;
- `overrides`:單期覆寫,key 是**該期原定結帳日所在年月**(`yyyy-MM`)→ `yyyy-MM-dd`
  (調整後即使跨月也不換 key);
- `payment_due_day` + `due_mode`:繳款期限。`fixed_day` = 每月第幾日;
  `days_after_closing` = 結帳日後第 N 天(`payment_due_day` 存 N)。MOZE 文件沒有「繳款期限
  遇假日順延」,這裡同樣不做。

wire 欄位(帳戶 payload,camelCase 是三端字串契約):`billingHolidayRule`、
`billingHolidayRegion`、`paymentDueMode`、`billingCycleOverrides`(物件)。
"""
from __future__ import annotations

import calendar
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

HOLIDAY_RULE_POSTPONE = "postpone"
HOLIDAY_RULE_ADVANCE = "advance"
HOLIDAY_RULES = (HOLIDAY_RULE_POSTPONE, HOLIDAY_RULE_ADVANCE)

DUE_MODE_FIXED_DAY = "fixed_day"
DUE_MODE_DAYS_AFTER_CLOSING = "days_after_closing"
DUE_MODES = (DUE_MODE_FIXED_DAY, DUE_MODE_DAYS_AFTER_CLOSING)

# 跟 `services/holidays/catalog.py::SUPPORTED_COUNTRIES`、App `kHolidayCountries` 一致。
HOLIDAY_REGIONS = ("TW", "CN", "HK", "JP", "KR", "US")

# 找工作日的上限,避免資料異常(整個月都是假日)時無窮迴圈。
_MAX_SHIFT_DAYS = 31


def _clamp_day(year: int, month: int, day: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day))


def month_key(year: int, month: int) -> str:
    """單期覆寫的 key:`yyyy-MM`。"""
    return f"{year:04d}-{month:02d}"


def parse_override_date(raw: Any) -> date | None:
    """`yyyy-MM-dd`(後面可接時間)→ date;壞格式回 None(他端寫入的壞資料不能崩潰)。"""
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date(int(raw[0:4]), int(raw[5:7]), int(raw[8:10]))
    except ValueError:
        return None


def normalize_overrides(raw: Any) -> dict[str, str]:
    """任意輸入(dict / JSON 字串 / None)→ 乾淨的 `{yyyy-MM: yyyy-MM-dd}`:key 必須是
    `yyyy-MM`、值必須是合法日期,其餘丟棄。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or len(key) != 7 or key[4] != "-":
            continue
        if not (key[:4].isdigit() and key[5:].isdigit() and 1 <= int(key[5:]) <= 12):
            continue
        parsed = parse_override_date(value)
        if parsed is not None:
            out[key] = parsed.isoformat()
    return out


def encode_overrides(overrides: Mapping[str, str]) -> str | None:
    """存進 DB 的 JSON 字串;空 → None。key 排序,同樣內容永遠編出同樣字串。"""
    if not overrides:
        return None
    return json.dumps({k: overrides[k] for k in sorted(overrides)}, ensure_ascii=False)


@dataclass(frozen=True)
class NonWorkingCalendar:
    """非工作日:週末 + 指定地區的放假日(節日資料的 `public` 與補假 `day_off`,不含不放假的
    紀念日 `observance`)。沒有資料的年份只剩週末。"""

    holidays: frozenset[date] = frozenset()

    def is_non_working_day(self, day: date) -> bool:
        return day.weekday() >= 5 or day in self.holidays


WEEKENDS_ONLY = NonWorkingCalendar()


@dataclass(frozen=True)
class BillingSchedule:
    billing_day: int
    holiday_rule: str | None = None
    region: str | None = None
    overrides: Mapping[str, str] = field(default_factory=dict)
    payment_due_day: int | None = None
    due_mode: str | None = None
    calendar: NonWorkingCalendar = WEEKENDS_ONLY

    @property
    def has_holiday_rule(self) -> bool:
        return self.holiday_rule in HOLIDAY_RULES

    @property
    def due_is_days_after_closing(self) -> bool:
        return self.due_mode == DUE_MODE_DAYS_AFTER_CLOSING

    def nominal_closing_date(self, year: int, month: int) -> date:
        """未經假日規則/覆寫調整的原定結帳日。"""
        return _clamp_day(year, month, self.billing_day)

    def closing_date(self, year: int, month: int) -> date:
        """某個「原定結帳日所在月份」實際的結帳日:有單期覆寫用覆寫;否則原定日,
        設了假日規則且那天是非工作日就延後/提前到最近的工作日。"""
        override = parse_override_date(self.overrides.get(month_key(year, month)))
        if override is not None:
            return override
        d = _clamp_day(year, month, self.billing_day)
        if not self.has_holiday_rule:
            return d
        step = 1 if self.holiday_rule == HOLIDAY_RULE_POSTPONE else -1
        for _ in range(_MAX_SHIFT_DAYS):
            if not self.calendar.is_non_working_day(d):
                break
            d += timedelta(days=step)
        return d

    def due_date_for_cycle_end(self, cycle_end: date) -> date | None:
        """繳款期限。沒設 `payment_due_day` → None。`cycle_end` 傳**實際**結帳日。"""
        n = self.payment_due_day
        if n is None:
            return None
        if self.due_is_days_after_closing:
            return cycle_end + timedelta(days=n)
        candidate = _clamp_day(cycle_end.year, cycle_end.month, n)
        # 嚴格晚於結帳日(跟 App 一致):數字等於結帳日就落到下個月,出帳當天不算繳款日。
        if candidate <= cycle_end:
            candidate = _clamp_day(
                cycle_end.year + (cycle_end.month // 12), cycle_end.month % 12 + 1, n
            )
        return candidate


def plain_schedule(billing_day: int, payment_due_day: int | None = None) -> BillingSchedule:
    """沒有任何調整的排程(只有結帳日)。"""
    return BillingSchedule(billing_day=billing_day, payment_due_day=payment_due_day)


def month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def closing_for_index(schedule: BillingSchedule, idx: int) -> date:
    return schedule.closing_date(idx // 12, idx % 12 + 1)


def index_of_closing(schedule: BillingSchedule, closing: date) -> int:
    """反查 `closing` 是哪個月份序號的結帳日(單期覆寫可能把結帳日移到隔月,所以在附近
    幾個月裡找);找不到就退回「結帳日所在月份」。"""
    base = month_index(closing.year, closing.month)
    for idx in (base, base - 1, base + 1, base - 2, base + 2):
        if closing_for_index(schedule, idx) == closing:
            return idx
    return base


def load_calendar(
    db: Any,
    region: str | None,
    *,
    years: Iterable[int] | None = None,
) -> NonWorkingCalendar:
    """從節日資料集讀 [region] 的放假日。沒設地區 / 資料集沒有 → 只有週末。讀取失敗不拋例外
    (帳單週期仍算得出來,最壞只是沒順延)。"""
    if not region or db is None:
        return WEEKENDS_ONLY
    try:
        from sqlalchemy import select

        from ..models import HolidayEntryRow

        stmt = select(HolidayEntryRow.day).where(
            HolidayEntryRow.country == region,
            HolidayEntryRow.kind.in_(("public", "day_off")),
        )
        if years:
            stmt = stmt.where(HolidayEntryRow.year.in_(list(years)))
        return NonWorkingCalendar(frozenset(db.scalars(stmt).all()))
    except Exception:  # pragma: no cover — 資料庫尚未建表等情況
        return WEEKENDS_ONLY


def schedule_from_account(
    account: Any,
    group: Any = None,
    *,
    db: Any = None,
    calendar_override: NonWorkingCalendar | None = None,
) -> BillingSchedule | None:
    """由帳戶 projection 列(必要時加上它掛靠的合併帳單主帳戶)組出 `BillingSchedule`。

    跟 App `billingScheduleForAccount` 同一套規則:子卡沒設結帳日時,「結帳日那一包」
    (結帳日 + 規則 + 地區 + 覆寫)整包退回主帳戶;「繳款期限那一包」(繳款日 + 模式)
    獨立判斷來源。兩邊都沒結帳日回 None。"""
    billing_source = account if (account.billing_day is not None or group is None) else group
    if billing_source.billing_day is None:
        return None
    due_source = account if (account.payment_due_day is not None or group is None) else group
    region = getattr(billing_source, "billing_holiday_region", None) or None
    rule = getattr(billing_source, "billing_holiday_rule", None) or None
    if rule not in HOLIDAY_RULES:
        rule = None
    cal = calendar_override
    if cal is None:
        cal = load_calendar(db, region) if rule else WEEKENDS_ONLY
    return BillingSchedule(
        billing_day=billing_source.billing_day,
        holiday_rule=rule,
        region=region if rule else None,
        overrides=normalize_overrides(getattr(billing_source, "billing_cycle_overrides_json", None)),
        payment_due_day=due_source.payment_due_day,
        due_mode=getattr(due_source, "payment_due_mode", None) or None,
        calendar=cal,
    )


def account_read_fields(account: Any) -> dict[str, Any]:
    """帳戶列表讀端點(ReadAccountOut)要帶出去的四個彈性結帳日欄位。壞資料(舊值、不在白名單
    的字串)原樣不外洩:規則/模式/地區不合法視為沒設。"""
    rule = getattr(account, "billing_holiday_rule", None) or None
    mode = getattr(account, "payment_due_mode", None) or None
    region = getattr(account, "billing_holiday_region", None) or None
    return {
        "billing_holiday_rule": rule if rule in HOLIDAY_RULES else None,
        "billing_holiday_region": region if region in HOLIDAY_REGIONS else None,
        "payment_due_mode": mode if mode in DUE_MODES else None,
        "billing_cycle_overrides": normalize_overrides(
            getattr(account, "billing_cycle_overrides_json", None)
        ) or None,
    }


def cycle_info(schedule: BillingSchedule, cycle_end: date) -> dict[str, Any]:
    """某一期(以實際結帳日 `cycle_end` 指定)的「調整本期結帳日」所需資訊:

    - `month_key`:單期覆寫的 key(原定結帳日所在年月);
    - `default_end`:預設結帳日(套假日規則、不含覆寫);
    - `adjusted`:這期是否有單期覆寫;
    - `next_end`:下一期的實際結帳日(可調整的上界:新結帳日必須早於它)。"""
    idx = index_of_closing(schedule, cycle_end)
    year, month = idx // 12, idx % 12 + 1
    key = month_key(year, month)
    without_override = BillingSchedule(
        billing_day=schedule.billing_day,
        holiday_rule=schedule.holiday_rule,
        region=schedule.region,
        overrides={},
        payment_due_day=schedule.payment_due_day,
        due_mode=schedule.due_mode,
        calendar=schedule.calendar,
    )
    return {
        "month_key": key,
        "default_end": without_override.closing_date(year, month),
        "adjusted": key in schedule.overrides,
        "next_end": closing_for_index(schedule, idx + 1),
    }


CalendarLoader = Callable[[str | None], NonWorkingCalendar]
