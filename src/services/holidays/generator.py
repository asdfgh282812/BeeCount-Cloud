"""產生某一年各國的節日清單(純函式,不碰 DB)。

來源兩路合併:
1. `holidays` 套件:各國國定假日 + 補假/調整放假(政府公告的調整隨套件升級進來)。
2. `catalog.CATALOG` 有 `rules` 的條目:套件沒有的非放假節慶(情人節、七夕、
   聖誕節、母親節…),以及大型節慶的「正日」(中秋、除夕)。

分類結果 `kind`:
- `public`:放假的節日(目錄規則日剛好也是套件假日,或純套件假日)。
- `observance`:不放假的節慶。
- `day_off`:補假/調整放假/連假裡非正日的那幾天。key 一律 `day_off`,名稱帶出
  原本是哪個節日的補假(「國慶日補假」)。客戶端只顯示主要國家的 day_off。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

import holidays as holidays_lib
from lunardate import LunarDate

from .catalog import (
    CATALOG,
    DAY_OFF_KEY,
    DAY_OFF_META,
    DAY_OFF_PRIORITY,
    DEFAULT_COLOR,
    DEFAULT_EMOJI,
    LOCAL_LANGUAGE,
    META_BY_KEY,
    PRIORITY,
    SUPPORTED_COUNTRIES,
    UNMAPPED_PRIORITY,
    FestivalMeta,
    map_name,
)


@dataclass(frozen=True)
class HolidayEntry:
    year: int
    country: str
    date: date
    key: str
    kind: str
    name_zh_tw: str
    name_en: str
    name_local: str
    emoji: str
    color: str
    priority: int

    def to_wire(self) -> dict:
        data = asdict(self)
        data["date"] = self.date.isoformat()
        data.pop("year")
        return data


_OBSERVED_RE = re.compile(r"^(?P<base>.+) \(observed\)$")
_ALTERNATIVE_RE = re.compile(r"^Alternative holiday for (?P<base>.+)$")
_SUBSTITUTED_PREFIX = "Day off (substituted"
# 日本「國民の休日」(夾在兩個假日中間的平日)、「振替休日」;韓國臨時公休日。
_PLAIN_DAY_OFF_NAMES = {"Substitute Holiday", "National Holiday", "Temporary Public Holiday"}


def _rule_date(rule: tuple, year: int) -> date | None:
    kind = rule[0]
    try:
        if kind == "fixed":
            return date(year, rule[1], rule[2])
        if kind == "lunar":
            return LunarDate(year, rule[1], rule[2]).to_solar_date()
        if kind == "lunar_eve":
            return LunarDate(year, 1, 1).to_solar_date() - timedelta(days=1)
        if kind in ("nth_weekday", "after_nth_weekday"):
            month, weekday, n = rule[1], rule[2], rule[3]
            first = date(year, month, 1)
            offset = (weekday - first.weekday()) % 7
            result = first + timedelta(days=offset + 7 * (n - 1))
            if kind == "after_nth_weekday":
                result += timedelta(days=rule[4])
            return result
    except ValueError:
        # lunardate 超出支援年份範圍等;該節慶當年略過,不讓整年產生失敗。
        return None
    raise ValueError(f"unknown holiday rule: {rule!r}")


def _rule_dates_for(country: str, year: int) -> dict[str, date]:
    out: dict[str, date] = {}
    for meta in CATALOG:
        rule = meta.rules.get(country) or meta.rules.get("*")
        if rule is None:
            continue
        d = _rule_date(rule, year)
        if d is not None and d.year == year:
            out[meta.key] = d
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "holiday"


def _lib_names(country: str, year: int, language: str) -> dict[date, list[str]]:
    lib = holidays_lib.country_holidays(country, years=year, language=language)
    return {d: name.split("; ") for d, name in lib.items()}


def _supported_languages(country: str) -> tuple[str, ...]:
    return tuple(getattr(holidays_lib.country_holidays(country), "supported_languages", ()) or ())


def _entry(year: int, country: str, d: date, meta: FestivalMeta, kind: str, *,
           name_zh_tw: str | None = None, name_en: str | None = None,
           name_local: str | None = None, priority: int | None = None) -> HolidayEntry:
    return HolidayEntry(
        year=year,
        country=country,
        date=d,
        key=meta.key,
        kind=kind,
        name_zh_tw=name_zh_tw or meta.zh_tw,
        name_en=name_en or meta.en,
        name_local=name_local or meta.local_name(country),
        emoji=meta.emoji,
        color=meta.color,
        priority=PRIORITY.get(meta.key, UNMAPPED_PRIORITY) if priority is None else priority,
    )


def _day_off(year: int, country: str, d: date, base_key: str | None, en: str, local: str,
             suffix_zh: str = "補假") -> HolidayEntry:
    base = META_BY_KEY.get(base_key) if base_key else None
    zh = f"{base.zh_tw}{suffix_zh}" if base else ("調整放假" if en.startswith(_SUBSTITUTED_PREFIX) else "補假")
    return _entry(year, country, d, DAY_OFF_META, "day_off", name_zh_tw=zh, name_en=en,
                  name_local=local, priority=DAY_OFF_PRIORITY)


def generate_country_year(country: str, year: int) -> list[HolidayEntry]:
    local_lang = LOCAL_LANGUAGE[country]
    languages = _supported_languages(country)
    en_names = _lib_names(country, year, "en_US")
    local_names = _lib_names(country, year, local_lang) if local_lang in languages else en_names
    zh_lang = "zh_TW" if "zh_TW" in languages else ("zh_HK" if "zh_HK" in languages else None)
    zh_names = _lib_names(country, year, zh_lang) if zh_lang else en_names

    rule_dates = _rule_dates_for(country, year)
    out: dict[tuple[date, str], HolidayEntry] = {}

    for d, names in en_names.items():
        locals_ = local_names.get(d, names)
        zhs = zh_names.get(d, names)
        for i, en in enumerate(names):
            local = locals_[i] if i < len(locals_) else en
            zh = zhs[i] if i < len(zhs) else en

            observed = _OBSERVED_RE.match(en) or _ALTERNATIVE_RE.match(en)
            if observed:
                base_key = map_name(country, observed.group("base"))
                e = _day_off(year, country, d, base_key, en, local)
                out.setdefault((d, DAY_OFF_KEY), e)
                continue
            if en.startswith(_SUBSTITUTED_PREFIX) or en in _PLAIN_DAY_OFF_NAMES:
                out.setdefault((d, DAY_OFF_KEY), _day_off(year, country, d, None, en, local))
                continue

            key = map_name(country, en)
            if key is None:
                meta = FestivalMeta(f"{country.lower()}_{_slug(en)}", zh, en, DEFAULT_EMOJI, DEFAULT_COLOR)
                out[(d, meta.key)] = _entry(year, country, d, meta, "public", name_local=local,
                                            priority=UNMAPPED_PRIORITY)
                continue

            meta = META_BY_KEY[key]
            rule_day = rule_dates.get(key)
            if meta.exact and rule_day is not None and rule_day != d:
                out.setdefault((d, DAY_OFF_KEY),
                               _day_off(year, country, d, key, en, local, suffix_zh="連假"))
                continue
            out[(d, key)] = _entry(year, country, d, meta, "public", name_local=local)

    # 目錄規則日:套件同一天已經放了同 key 的假 → 上面已產生 public(在地名用套件的),
    # 這裡只補套件沒有的那些 → observance。
    for key, d in rule_dates.items():
        if (d, key) in out:
            continue
        meta = META_BY_KEY[key]
        out[(d, key)] = _entry(year, country, d, meta, "observance")

    return sorted(out.values(), key=lambda e: (e.date, e.priority, e.key))


def generate_year(year: int, countries: tuple[str, ...] = SUPPORTED_COUNTRIES) -> list[HolidayEntry]:
    entries: list[HolidayEntry] = []
    for country in countries:
        entries.extend(generate_country_year(country, year))
    return entries
