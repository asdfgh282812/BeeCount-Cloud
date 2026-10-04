# 節日資料 SD（2026-10-05）

App 明細日曆的節日標記與應景台詞、App 和 Web 的年度報告，以及節日主題，全部共用這份資料。App 端的說明見 `BeeCount-main/docs/changes/2026-10-05-holidays-p1.md`。

分階段：
- **P1（本文件）**：server 端資料、排程、API，以及 App 端的日曆與套版台詞。
- **P2**：App 的 AI 月底產台詞、App 和 Web 的年度報告提及節日。
- **P3**：節日主題。

## 資料怎麼產生

`services/holidays/`：

| 檔案 | 職責 |
|---|---|
| `catalog.py` | 節慶目錄：穩定 `key` ↔ 繁中/英文/在地名、emoji、主題色、日期規則；以及 `holidays` 套件英文假日名 → key 的 `NAME_MAP` |
| `generator.py` | `generate_year(year)`：把套件的國定假日和目錄規則合併，並分類成 `public`、`observance`、`day_off`。純函式 |
| `dataset.py` | 落地到 `holiday_entries` 和 `holiday_dataset_meta`；`refresh_dataset`、`ensure_dataset`、`list_entries` |

來源有兩路：
- **python `holidays` 套件**：各國國定假日，以及補假、調整放假。政府公告的調整會隨套件升級進來。
- **目錄裡帶 `rules` 的條目**：
  - 套件沒有的非放假節慶：情人節、七夕、元宵、中元、萬聖節、雙 11、黑色星期五、台灣的聖誕節、母親節、父親節……
  - 大型節慶的「正日」：除夕、中秋、端午。
  - 農曆日期用 `lunardate` 換算。除夕用「正月初一的前一天」，不寫成農曆 12/30，因為小月只有 29 天。

分類規則：
- 套件名稱是 `(observed)`、`Alternative holiday for X`、`Day off (substituted…)`、日本的 `Substitute Holiday` 或 `National Holiday` → `day_off`，名稱寫成「國慶日補假」「調整放假」。
- `exact=True` 的節慶（除夕、中秋、端午）：套件在規則日以外用同名放的假，降級成 `day_off`「X連假」。例如台灣 2026-02-15 套件也標成除夕。
- 規則日剛好也是套件假日 → `public`，在地名用套件的。其他規則日 → `observance`。
- 套件裡沒對照到 key 的假日 → 自動產生 `<country>_<slug>` key，名稱直接用套件的。客戶端對這類 key 用通用套版。

**key 是三端契約**。App 的套版台詞、日曆圖示、主題都靠 key 對應。改 key 名稱，舊版 App 會退回通用內容；新增 key 則沒有影響。同一個節慶跨國共用同一個 key，例如 KR 的 Chuseok 也是 `mid_autumn`。

## 版本與排程

- 排程 `holiday_dataset_refresh` 每 24 小時跑一次，在 `/admin/scheduled-jobs` 可以調整或立即執行。
- 目標年份是去年和今年（年度報告會用到去年），11 月起再加上明年。這就是「年底預先準備明年」。
- 每年產生完計算 sha256，跟 `year_hashes` 比對：
  - 有差異 → 整年刪除重寫。
  - 這次 refresh 只要有任何一年變動，`version` 就加 1。
  - 套件升級帶進補假修正時，客戶端會因此自動重抓。
- 讀端點遇到資料集還沒產生過（新部署、排程還沒輪到）時，會先同步呼叫一次 `ensure_dataset`。

## API

`GET /api/v1/read/holidays?countries=TW,JP&years=2026,2027&known_version=N`

- 走 `get_current_user`，授權稽核測試不需要開例外。
- `countries`、`years` 都可以省略（省略 = 全部）。不支援的國家回 422。
- `known_version` 等於目前版本 → `{version, unchanged: true, entries: []}`。
- 回應：`{version, updated_at, unchanged, years, countries, entries: [{date, country, key, kind, name_zh_tw, name_en, name_local, emoji, color, priority}]}`。欄位是 snake_case，跟其它 read API 一致。
- App 一律不帶 countries，抓全部六國。使用者改勾選的國家時不必重抓。資料量約 500 筆、100KB。

## 多國去重（客戶端責任）

1. 某天主要國家有節日（含補假），只顯示主要國家的。
2. 主要國家那天沒有，才顯示其他已勾選國家的節日，並依 key 去重。
3. 其他國家的 `day_off` 不顯示。

App 的實作在 `lib/services/holidays/holiday_resolver.dart`，Web 在 `frontend/packages/web-features/src/features/annual-report/data/holidays.ts`（`resolveHolidays`）。兩邊規則要一起改。

使用者的國家設定存在 profile appearance 裡（`holiday_enabled`、`holiday_regions`、`holiday_primary`、`holiday_theme_enabled`），由 App 推上來。Cloud 不需要改 schema。Web 的 `ProfileAppearance` 型別也補了這四個欄位；Web 設定頁 patch appearance 時會帶上整份 dict，不會清掉它們。

## Web 年度報告（P2，2026-10-05）

入口：Web 頭像選單 → 年度報告 → 選年份 →「難忘的時刻」那一頁。

- `data/fetch.ts::fetchReportHolidays`：
  - 先讀 `fetchProfileMe` 的 appearance 拿節日設定。App 沒推過時，依 Web 語系推主要國家（zh-TW→TW、zh-CN→CN、其他→US），跟 App 的預設一致。
  - 再呼叫 `fetchHolidays(countries, years=[該年])` 並去重。
  - 跟股票摘要一樣是加分項：任何失敗（舊版 server 404 等）都當作沒有節日，不影響報告。
- `data/aggregate.ts`：只留該年度的節日，用 `topHolidaySpend` 算「節日當天花最多的那天」（`holidaySpend`）。
- `pages/PageExtremes.tsx`：
  - 最大單筆、第一筆、最貴的一天這三張卡，那天剛好是節日時，標題旁加「🥮 中秋節」標籤。非主要國家的節日後面加國旗（`holidayLabel`，跟 App 一致）。
  - 多一張「節日花最多」卡（金額、節日名、今年有幾個節日有花費）。跟「最貴的一天」同一天時不顯示這張，因為那張卡已經有節日標籤。
- **日期用使用者本地時區**（`localDayKey`）。年度報告其它統計沿用 `happenedAt.slice(0, 10)`（UTC 日期），但節日是當地日期：台灣早上 7 點的消費在 UTC 是前一天，用 slice 會對錯節日。
- `apps/web/src/annualReportHolidays.test.ts`：去重規則（對照 App 的 resolver 測試）、設定 fallback、節日支出、只留該年度。

## App 內建備援

`scripts/export_holidays_bundle.py` 用同一個產生器輸出「去年、今年、明年」六國資料，寫到 `BeeCount-main/assets/holidays/holidays_bundle.json`，格式同 API 回應，`version` 固定為 0。

**App 發版前執行一次**。沒用 BeeCount Cloud 的使用者只看得到這份資料。

## 依賴

`requirements.txt` 新增 `holidays>=0.60` 和 `lunardate>=0.3.0`，兩個都是純 Python。

## 測試

`tests/test_holidays.py` 涵蓋：
- 2026 TW 中秋落在 9/25、國慶補假
- 除夕降級
- 目錄節慶的分類
- KR Chuseok 共用 key
- 目標年份
- refresh 重跑不會讓版本加 1，跨進 11 月才加 1
- 讀端點的 `known_version` 和 422
