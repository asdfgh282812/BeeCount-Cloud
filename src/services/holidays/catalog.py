"""節慶目錄:穩定 key ↔ 顯示資訊 + 日期規則。

`key` 是 App/Web 三端共用的契約——App 的套版台詞、日曆圖示、節日主題都靠 key
對應(`lib/services/holidays/holiday_greeting_templates.dart`)。同一個節慶跨國
共用同一個 key(TW/CN/HK 的中秋都是 `mid_autumn`),客戶端依 key 去重。

**改 key 等於改契約**:已發版的 App 內建資料與快取都帶舊 key,改名會讓舊版
App 的套版台詞/主題對不上(退回通用版,不會壞掉,但會掉應景內容)。新增 key 沒
有這個問題。

規則型別(`rules` 的 value):
- `("fixed", month, day)`:國曆固定日。
- `("lunar", month, day)`:農曆日期(lunardate 換算,農曆年 = 國曆年)。
- `("lunar_eve",)`:除夕 = 農曆正月初一的前一天(不能寫成農曆 12/30,小月只有 29 天)。
- `("nth_weekday", month, weekday, n)`:該月第 n 個星期幾(weekday 0=週一 … 6=週日)。
- `("after_nth_weekday", month, weekday, n, offset_days)`:同上再位移(黑色星期五)。

`rules` 的 key 是國家代碼;`"*"` = 所有支援國家。沒有 `rules` 的條目只提供顯示
資訊,日期完全來自 `holidays` 套件(例如日本海之日)。

`exact=True`:這個節慶只有「規則算出的那一天」算正日。`holidays` 套件在別的日期
用同一個名字放的假(台灣 2026-02-15 套件也標成 Chinese New Year's Eve)會降級成
`day_off`(連假),避免日曆上出現兩個除夕。
"""
from __future__ import annotations

from dataclasses import dataclass, field

SUPPORTED_COUNTRIES: tuple[str, ...] = ("TW", "CN", "HK", "JP", "KR", "US")

# `holidays` 套件各國的在地語言代碼(name_local 用)。
LOCAL_LANGUAGE: dict[str, str] = {
    "TW": "zh_TW",
    "CN": "zh_CN",
    "HK": "zh_HK",
    "JP": "ja",
    "KR": "ko",
    "US": "en_US",
}

# 目錄 `local` 名稱的語言 key(跟 LOCAL_LANGUAGE 不同:HK 用繁中)。
_LOCAL_NAME_LANG: dict[str, str] = {
    "TW": "zh_TW",
    "CN": "zh_CN",
    "HK": "zh_TW",
    "JP": "ja",
    "KR": "ko",
    "US": "en",
}

DAY_OFF_KEY = "day_off"
UNMAPPED_PRIORITY = 900
DAY_OFF_PRIORITY = 999
DEFAULT_COLOR = "#F59E0B"
DEFAULT_EMOJI = "🎌"


@dataclass(frozen=True)
class FestivalMeta:
    key: str
    zh_tw: str
    en: str
    emoji: str
    color: str
    rules: dict[str, tuple] = field(default_factory=dict)
    local: dict[str, str] = field(default_factory=dict)
    exact: bool = False

    def local_name(self, country: str) -> str:
        lang = _LOCAL_NAME_LANG.get(country, "en")
        if lang == "zh_TW":
            return self.zh_tw
        if lang == "en":
            return self.en
        return self.local.get(lang) or (self.zh_tw if lang == "zh_CN" else self.en)


_ALL = "*"
_SUN, _THU = 6, 3

# 順序 = priority(越前面越優先):同一天多個節日時的排序、節日主題取哪一個。
CATALOG: tuple[FestivalMeta, ...] = (
    # ── 大型節慶(有規則)────────────────────────────────────────────
    FestivalMeta("lunar_new_year_eve", "除夕", "Lunar New Year's Eve", "🧧", "#D32F2F",
                 rules={"TW": ("lunar_eve",), "CN": ("lunar_eve",), "HK": ("lunar_eve",), "KR": ("lunar_eve",)},
                 local={"zh_CN": "除夕", "ko": "설날 전날"}, exact=True),
    FestivalMeta("lunar_new_year", "春節", "Lunar New Year", "🧧", "#D32F2F",
                 rules={"TW": ("lunar", 1, 1), "CN": ("lunar", 1, 1), "HK": ("lunar", 1, 1), "KR": ("lunar", 1, 1)},
                 local={"zh_CN": "春节", "ko": "설날"}),
    FestivalMeta("mid_autumn", "中秋節", "Mid-Autumn Festival", "🥮", "#FFB300",
                 rules={"TW": ("lunar", 8, 15), "CN": ("lunar", 8, 15), "HK": ("lunar", 8, 15), "KR": ("lunar", 8, 15)},
                 local={"zh_CN": "中秋节", "ko": "추석"}, exact=True),
    FestivalMeta("christmas", "聖誕節", "Christmas", "🎄", "#C62828",
                 rules={_ALL: ("fixed", 12, 25)},
                 local={"zh_CN": "圣诞节", "ja": "クリスマス", "ko": "크리스마스"}),
    FestivalMeta("dragon_boat", "端午節", "Dragon Boat Festival", "🐉", "#2E7D32",
                 rules={"TW": ("lunar", 5, 5), "CN": ("lunar", 5, 5), "HK": ("lunar", 5, 5)},
                 local={"zh_CN": "端午节"}, exact=True),
    FestivalMeta("new_year", "元旦", "New Year's Day", "🎉", "#FF7043",
                 rules={_ALL: ("fixed", 1, 1)},
                 local={"zh_CN": "元旦", "ja": "元日", "ko": "신정"}),
    FestivalMeta("new_years_eve", "跨年夜", "New Year's Eve", "🎆", "#5E35B1",
                 rules={_ALL: ("fixed", 12, 31)},
                 local={"zh_CN": "跨年夜", "ja": "大晦日", "ko": "섣달그믐"}),
    FestivalMeta("christmas_eve", "平安夜", "Christmas Eve", "🎄", "#2E7D32",
                 rules={"TW": ("fixed", 12, 24), "HK": ("fixed", 12, 24), "JP": ("fixed", 12, 24),
                        "KR": ("fixed", 12, 24), "US": ("fixed", 12, 24), "CN": ("fixed", 12, 24)},
                 local={"zh_CN": "平安夜", "ja": "クリスマスイブ", "ko": "크리스마스 이브"}),
    FestivalMeta("valentines", "情人節", "Valentine's Day", "💝", "#E91E63",
                 rules={_ALL: ("fixed", 2, 14)},
                 local={"zh_CN": "情人节", "ja": "バレンタインデー", "ko": "밸런타인데이"}),
    FestivalMeta("qixi", "七夕", "Qixi Festival", "💞", "#EC407A",
                 rules={"TW": ("lunar", 7, 7), "CN": ("lunar", 7, 7), "HK": ("lunar", 7, 7)},
                 local={"zh_CN": "七夕"}),
    FestivalMeta("lantern", "元宵節", "Lantern Festival", "🏮", "#FF7043",
                 rules={"TW": ("lunar", 1, 15), "CN": ("lunar", 1, 15), "HK": ("lunar", 1, 15)},
                 local={"zh_CN": "元宵节"}),
    FestivalMeta("thanksgiving", "感恩節", "Thanksgiving", "🦃", "#A1887F",
                 rules={"US": ("nth_weekday", 11, _THU, 4)}),
    FestivalMeta("halloween", "萬聖節", "Halloween", "🎃", "#FF6F00",
                 rules={"TW": ("fixed", 10, 31), "HK": ("fixed", 10, 31), "JP": ("fixed", 10, 31),
                        "KR": ("fixed", 10, 31), "US": ("fixed", 10, 31)},
                 local={"ja": "ハロウィン", "ko": "핼러윈"}),
    FestivalMeta("mothers_day", "母親節", "Mother's Day", "💐", "#F06292",
                 rules={"TW": ("nth_weekday", 5, _SUN, 2), "CN": ("nth_weekday", 5, _SUN, 2),
                        "HK": ("nth_weekday", 5, _SUN, 2), "JP": ("nth_weekday", 5, _SUN, 2),
                        "US": ("nth_weekday", 5, _SUN, 2)},
                 local={"zh_CN": "母亲节", "ja": "母の日"}),
    FestivalMeta("kr_parents_day", "父母節", "Parents' Day", "💐", "#F06292",
                 rules={"KR": ("fixed", 5, 8)}, local={"ko": "어버이날"}),
    FestivalMeta("fathers_day", "父親節", "Father's Day", "👔", "#1976D2",
                 rules={"TW": ("fixed", 8, 8), "CN": ("nth_weekday", 6, _SUN, 3),
                        "HK": ("nth_weekday", 6, _SUN, 3), "JP": ("nth_weekday", 6, _SUN, 3),
                        "US": ("nth_weekday", 6, _SUN, 3)},
                 local={"zh_CN": "父亲节", "ja": "父の日"}),
    FestivalMeta("double_eleven", "雙11", "Singles' Day", "🛍️", "#FF5722",
                 rules={"TW": ("fixed", 11, 11), "CN": ("fixed", 11, 11)}, local={"zh_CN": "双十一"}),
    FestivalMeta("black_friday", "黑色星期五", "Black Friday", "🛒", "#424242",
                 rules={"US": ("after_nth_weekday", 11, _THU, 4, 1)}),
    FestivalMeta("ghost_festival", "中元節", "Ghost Festival", "🙏", "#8D6E63",
                 rules={"TW": ("lunar", 7, 15), "HK": ("lunar", 7, 15), "CN": ("lunar", 7, 15)},
                 local={"zh_CN": "中元节"}),
    FestivalMeta("double_ninth", "重陽節", "Double Ninth Festival", "🌼", "#FFA726",
                 rules={"TW": ("lunar", 9, 9), "CN": ("lunar", 9, 9), "HK": ("lunar", 9, 9)},
                 local={"zh_CN": "重阳节"}),
    FestivalMeta("white_day", "白色情人節", "White Day", "🍬", "#90A4AE",
                 rules={"JP": ("fixed", 3, 14), "KR": ("fixed", 3, 14)},
                 local={"ja": "ホワイトデー", "ko": "화이트데이"}),
    FestivalMeta("tanabata", "七夕", "Tanabata", "🎋", "#26A69A",
                 rules={"JP": ("fixed", 7, 7)}, local={"ja": "七夕"}),
    # ── 只提供顯示資訊,日期來自 holidays 套件 ──────────────────────
    FestivalMeta("chuseok_holiday", "秋夕連假", "Chuseok Holiday", "🥮", "#FFB300", local={"ko": "추석 연휴"}),
    FestivalMeta("tomb_sweeping", "清明節", "Tomb-Sweeping Day", "🌿", "#66BB6A", local={"zh_CN": "清明节"}),
    FestivalMeta("labor_day", "勞動節", "Labor Day", "🛠️", "#78909C", local={"zh_CN": "劳动节", "ko": "근로자의 날"}),
    FestivalMeta("childrens_day", "兒童節", "Children's Day", "🧸", "#29B6F6", local={"ja": "こどもの日", "ko": "어린이날"}),
    FestivalMeta("buddha_birthday", "佛誕", "Buddha's Birthday", "🪷", "#FFCA28", local={"ko": "부처님오신날"}),
    FestivalMeta("good_friday", "耶穌受難節", "Good Friday", "✝️", "#7E57C2"),
    FestivalMeta("easter_saturday", "耶穌受難節翌日", "The day following Good Friday", "✝️", "#7E57C2"),
    FestivalMeta("easter_monday", "復活節星期一", "Easter Monday", "🐣", "#9CCC65"),
    FestivalMeta("tw_peace_memorial", "和平紀念日", "Peace Memorial Day", "🕊️", "#90A4AE"),
    FestivalMeta("tw_teachers_day", "教師節", "Teachers' Day", "📚", "#8D6E63"),
    FestivalMeta("tw_national_day", "國慶日", "National Day", "🇹🇼", "#D32F2F"),
    FestivalMeta("tw_restoration_day", "臺灣光復節", "Taiwan Restoration Day", "🏝️", "#26A69A"),
    FestivalMeta("tw_constitution_day", "行憲紀念日", "Constitution Day", "📜", "#8D6E63"),
    FestivalMeta("cn_national_day", "國慶節", "National Day", "🇨🇳", "#D32F2F", local={"zh_CN": "国庆节"}),
    FestivalMeta("hk_establishment_day", "香港回歸紀念日", "HKSAR Establishment Day", "🇭🇰", "#D32F2F"),
    FestivalMeta("hk_day_after_mid_autumn", "中秋節翌日", "The day following Mid-Autumn Festival", "🥮", "#FFB300"),
    FestivalMeta("hk_boxing_day", "聖誕節後第一個周日", "The first weekday after Christmas", "🎁", "#C62828"),
    FestivalMeta("jp_coming_of_age", "成人之日", "Coming of Age Day", "👘", "#AB47BC", local={"ja": "成人の日"}),
    FestivalMeta("jp_foundation_day", "建國紀念日", "National Foundation Day", "🎌", "#D32F2F", local={"ja": "建国記念の日"}),
    FestivalMeta("jp_emperor_birthday", "天皇誕生日", "Emperor's Birthday", "🎌", "#D32F2F", local={"ja": "天皇誕生日"}),
    FestivalMeta("jp_vernal_equinox", "春分之日", "Vernal Equinox Day", "🌸", "#F48FB1", local={"ja": "春分の日"}),
    FestivalMeta("jp_showa_day", "昭和之日", "Showa Day", "🎌", "#D32F2F", local={"ja": "昭和の日"}),
    FestivalMeta("jp_constitution_day", "憲法紀念日", "Constitution Memorial Day", "📜", "#8D6E63", local={"ja": "憲法記念日"}),
    FestivalMeta("jp_greenery_day", "綠之日", "Greenery Day", "🌳", "#43A047", local={"ja": "みどりの日"}),
    FestivalMeta("jp_marine_day", "海之日", "Marine Day", "🌊", "#039BE5", local={"ja": "海の日"}),
    FestivalMeta("jp_mountain_day", "山之日", "Mountain Day", "⛰️", "#6D4C41", local={"ja": "山の日"}),
    FestivalMeta("jp_respect_aged", "敬老之日", "Respect for the Aged Day", "🍵", "#8D6E63", local={"ja": "敬老の日"}),
    FestivalMeta("jp_autumnal_equinox", "秋分之日", "Autumnal Equinox Day", "🍂", "#EF6C00", local={"ja": "秋分の日"}),
    FestivalMeta("jp_sports_day", "體育之日", "Sports Day", "🏅", "#1E88E5", local={"ja": "スポーツの日"}),
    FestivalMeta("jp_culture_day", "文化之日", "Culture Day", "🎨", "#7E57C2", local={"ja": "文化の日"}),
    FestivalMeta("jp_labor_thanksgiving", "勤勞感謝之日", "Labor Thanksgiving Day", "🙏", "#8D6E63", local={"ja": "勤労感謝の日"}),
    FestivalMeta("kr_independence_movement", "三一節", "Independence Movement Day", "🇰🇷", "#1565C0", local={"ko": "삼일절"}),
    FestivalMeta("kr_memorial_day", "顯忠日", "Memorial Day", "🕯️", "#607D8B", local={"ko": "현충일"}),
    FestivalMeta("kr_constitution_day", "制憲節", "Constitution Day", "📜", "#8D6E63", local={"ko": "제헌절"}),
    FestivalMeta("kr_liberation_day", "光復節", "Liberation Day", "🇰🇷", "#1565C0", local={"ko": "광복절"}),
    FestivalMeta("kr_armed_forces_day", "國軍日", "Armed Forces Day", "🎖️", "#558B2F", local={"ko": "국군의 날"}),
    FestivalMeta("kr_national_foundation", "開天節", "National Foundation Day", "🇰🇷", "#1565C0", local={"ko": "개천절"}),
    FestivalMeta("kr_hangul_day", "韓文日", "Hangul Day", "🔤", "#5C6BC0", local={"ko": "한글날"}),
    FestivalMeta("kr_election_day", "選舉日", "Election Day", "🗳️", "#607D8B", local={"ko": "선거일"}),
    FestivalMeta("us_mlk_day", "馬丁路德金恩紀念日", "Martin Luther King Jr. Day", "✊", "#5D4037"),
    FestivalMeta("us_presidents_day", "總統日", "Presidents' Day", "🇺🇸", "#1565C0"),
    FestivalMeta("us_memorial_day", "陣亡將士紀念日", "Memorial Day", "🇺🇸", "#1565C0"),
    FestivalMeta("us_juneteenth", "六月節", "Juneteenth", "✊", "#5D4037"),
    FestivalMeta("us_independence_day", "美國獨立紀念日", "Independence Day", "🎆", "#1565C0"),
    FestivalMeta("us_labor_day", "勞動節", "Labor Day", "🛠️", "#78909C"),
    FestivalMeta("us_columbus_day", "哥倫布日", "Columbus Day", "⛵", "#0277BD"),
    FestivalMeta("us_veterans_day", "退伍軍人節", "Veterans Day", "🎖️", "#558B2F"),
)

META_BY_KEY: dict[str, FestivalMeta] = {m.key: m for m in CATALOG}
PRIORITY: dict[str, int] = {m.key: i for i, m in enumerate(CATALOG)}

DAY_OFF_META = FestivalMeta(DAY_OFF_KEY, "補假", "Day off", "😴", "#90A4AE")

# `holidays` 套件英文名(en_US)→ key。先查 (country, name),再查 ("*", name)。
# 沒對照到的假日 generator 會自動產生 `<country>_<slug>` key、名稱直接用套件的。
NAME_MAP: dict[tuple[str, str], str] = {
    (_ALL, "New Year's Day"): "new_year",
    (_ALL, "Christmas Day"): "christmas",
    (_ALL, "Labor Day"): "labor_day",
    (_ALL, "Children's Day"): "childrens_day",
    (_ALL, "Tomb-Sweeping Day"): "tomb_sweeping",
    (_ALL, "Dragon Boat Festival"): "dragon_boat",
    (_ALL, "Mid-Autumn Festival"): "mid_autumn",
    (_ALL, "Chinese New Year's Eve"): "lunar_new_year_eve",
    (_ALL, "Chinese New Year"): "lunar_new_year",
    (_ALL, "Chinese New Year (Spring Festival)"): "lunar_new_year",
    # TW
    ("TW", "Founding Day of the Republic of China"): "new_year",
    ("TW", "Peace Memorial Day"): "tw_peace_memorial",
    ("TW", "Confucius' Birthday"): "tw_teachers_day",
    ("TW", "National Day"): "tw_national_day",
    ("TW", "Taiwan Restoration and Guningtou Victory Memorial Day"): "tw_restoration_day",
    ("TW", "Constitution Day"): "tw_constitution_day",
    # CN
    ("CN", "National Day"): "cn_national_day",
    # HK
    ("HK", "National Day"): "cn_national_day",
    ("HK", "The second day of Chinese New Year"): "lunar_new_year",
    ("HK", "The third day of Chinese New Year"): "lunar_new_year",
    ("HK", "The fourth day of Chinese New Year"): "lunar_new_year",
    ("HK", "Good Friday"): "good_friday",
    ("HK", "The day following Good Friday"): "easter_saturday",
    ("HK", "Easter Monday"): "easter_monday",
    ("HK", "The Buddha's Birthday"): "buddha_birthday",
    ("HK", "Hong Kong S.A.R. Establishment Day"): "hk_establishment_day",
    ("HK", "The Day following Mid-Autumn Festival"): "hk_day_after_mid_autumn",
    ("HK", "The Second Day following Mid-Autumn Festival"): "hk_day_after_mid_autumn",
    ("HK", "Double Ninth Festival"): "double_ninth",
    ("HK", "The first weekday after Christmas Day"): "hk_boxing_day",
    # JP
    ("JP", "Coming of Age Day"): "jp_coming_of_age",
    ("JP", "Foundation Day"): "jp_foundation_day",
    ("JP", "Emperor's Birthday"): "jp_emperor_birthday",
    ("JP", "Vernal Equinox Day"): "jp_vernal_equinox",
    ("JP", "Showa Day"): "jp_showa_day",
    ("JP", "Constitution Day"): "jp_constitution_day",
    ("JP", "Greenery Day"): "jp_greenery_day",
    ("JP", "Marine Day"): "jp_marine_day",
    ("JP", "Mountain Day"): "jp_mountain_day",
    ("JP", "Respect for the Aged Day"): "jp_respect_aged",
    ("JP", "Autumnal Equinox Day"): "jp_autumnal_equinox",
    ("JP", "Sports Day"): "jp_sports_day",
    ("JP", "Culture Day"): "jp_culture_day",
    ("JP", "Labor Thanksgiving Day"): "jp_labor_thanksgiving",
    # KR
    ("KR", "The day preceding Korean New Year"): "lunar_new_year_eve",
    ("KR", "Korean New Year"): "lunar_new_year",
    ("KR", "The second day of Korean New Year"): "lunar_new_year",
    ("KR", "Chuseok"): "mid_autumn",
    ("KR", "The day preceding Chuseok"): "chuseok_holiday",
    ("KR", "The second day of Chuseok"): "chuseok_holiday",
    ("KR", "Independence Movement Day"): "kr_independence_movement",
    ("KR", "Buddha's Birthday"): "buddha_birthday",
    ("KR", "Memorial Day"): "kr_memorial_day",
    ("KR", "Constitution Day"): "kr_constitution_day",
    ("KR", "Liberation Day"): "kr_liberation_day",
    ("KR", "Armed Forces Day"): "kr_armed_forces_day",
    ("KR", "National Foundation Day"): "kr_national_foundation",
    ("KR", "Hangul Day"): "kr_hangul_day",
    ("KR", "Local Election Day"): "kr_election_day",
    ("KR", "National Assembly Election Day"): "kr_election_day",
    ("KR", "Presidential Election Day"): "kr_election_day",
    # US
    ("US", "Martin Luther King Jr. Day"): "us_mlk_day",
    ("US", "Washington's Birthday"): "us_presidents_day",
    ("US", "Memorial Day"): "us_memorial_day",
    ("US", "Juneteenth National Independence Day"): "us_juneteenth",
    ("US", "Independence Day"): "us_independence_day",
    ("US", "Labor Day"): "us_labor_day",
    ("US", "Columbus Day"): "us_columbus_day",
    ("US", "Veterans Day"): "us_veterans_day",
    ("US", "Thanksgiving Day"): "thanksgiving",
}


def map_name(country: str, en_name: str) -> str | None:
    key = NAME_MAP.get((country, en_name)) or NAME_MAP.get((_ALL, en_name))
    if key is None or key not in META_BY_KEY:
        return None
    return key
