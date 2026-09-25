# 比較報表矩陣(Web)

對齊 https://doc.moze.app/analysis/comparison-report。矩陣要大螢幕才看得清楚,所以只做在 Web。App 端(BeeCount repo)的對應功能是底部「報表」分頁的統計報表(`docs/changes/2026-09-25-statistics-report.md`)。

## 入口

- 頂部導覽「記帳」組,「專案」右邊的「比較報表」(`nav.ts` NAV_GROUPS,路由 `/app/comparison-report`)。
- ⌘K 指令面板也有「比較報表」。
- 頁面:`frontend/apps/web/src/pages/sections/ComparisonReportPage.tsx`。

## API

### `GET /read/workspace/comparison-matrix`

| 參數 | 說明 |
|---|---|
| `dimension` | `record_type` / `expense_category` / `income_category` / `expense_subcategory` / `income_subcategory` / `project` / `account_group` |
| `kind` | `expense` / `income`,只對 `project` / `account_group` 有效;類別維度由 dimension 本身決定 |
| `start` / `end` | 週期標籤 `YYYY-MM`,兩端都含,最多 120 個月;順序顛倒會自動校正 |
| `ledger_id`、`user_id`、`tz_offset_minutes`、`natural_month` | 語意同 `/workspace/comparison` |

- 回傳內容:
  - `columns[]`:`key`、`label`、`parent_label`
  - `rows[]`:`month`、`start`、`end`、`values`、`total`、`mom_pct`
  - `column_totals`、`column_averages`、`grand_total`
- 欄位怎麼算:
  - `record_type` 的欄位固定是 expense / income / balance,每月 `total` = 結餘。
  - 其他維度的 `total` = 當月各欄加總。
- 欄位 key 規則:
  - 類別維度用**分類名稱**當 key,跟 `/workspace/analytics`、`/workspace/comparison` 一樣按名稱分組。
  - 子類別維度的 key 是 `一級›二級`;一級分類本身的交易 key 就是一級名稱。
  - 找不到分類階層時,退回用交易上的 `category_name`。
- `__none__` 代表「(無)」;在 `account_group` 維度代表「未分組」。它固定排在最後,其餘欄位依期間合計金額降序。
- `account_group` 怎麼歸組:帳戶的 `parent_account_id` 指向主帳戶就算那一組;帳戶本身就是 `account_group` 類型時算它自己那組;都不是就歸「未分組」。

### `GET /read/workspace/comparison-matrix/cell`

點格子下鑽用。參數同上,但把 `start`/`end` 換成 `month` + `column_key`。

- 回傳該格的交易列表。拆帳交易只算對應的明細;退款為負值,並帶 `is_refund`。
- `record_type` 的 `balance` 欄會列出該月全部收支交易。

### 口徑

- 抽出共用 helper `_stat_legs()`:本位幣 `native_amount ?? amount`、拆帳按明細展開並按折算比例縮放、退款 netting、排除 `exclude_from_stats`。
- 退款 netting 的規則:income 退款從 expense 扣,expense 退款從 income 扣。App 端統計也是同一口徑(見 App repo `docs/changes/2026-09-25-refund-netting.md`)。2026-09-25 起退款扣回原交易的分類(原交易拆帳按明細比例分攤),專案沒填就沿用原交易的;原交易排除統計時退款也不計入。
- `/workspace/comparison` 的 `_comparison_period_totals` 和 `workspace_analytics` 都改成呼叫同一個 helper;除了上面 2026-09-25 的退款歸屬,其餘行為不變。
- 矩陣端點和下鑽端點共用 `_matrix_legs()`,兩邊分組規則不會分歧。

## 前端互動

- **月份欄**
  - 點一下選取或取消;Shift+點另一個月份會選取整段範圍。
  - 底部「合計/平均」、右下總計、分析圖表都只算選取的月份,沒有選取時算全部。
- **欄位標題**
  - 點一下切換是否計入右側「總計」與月增率,被排除的欄位會加刪除線。
  - 記錄類型維度下,總計 = (有計入時的)收入 − (有計入時的)支出。
- **點格子**:右側抽屜顯示該格的交易明細。
- **排序**:依金額、依平均、依名稱。
- **期間快捷**:近 6 個月、近 12 個月、今年、去年。
- **分析卡片**(recharts)
  - 項目佔比:圓餅圖。
  - 區間統計:堆疊長條圖,加上平均線 ReferenceLine。
  - 區間變化:折線圖,可切換「累積」。
  - 最多畫前 8 個欄位。

## 刻意不做

- 自訂/儲存比較報表範本。
- 結算週期設定:一律沿用帳本的月起始日。
- CSV 匯出。
- 長按全選欄位(Web 沒有長按)。

## 手動測試

1. 登入 Web,選一個有多個月份資料的帳本,進「比較報表」。確認預設是近 12 個月、支出類別。
2. 把維度切成記錄類型,確認每月有支出/收入/結餘三欄,右側總計 = 結餘。
3. 點 3 月,再 Shift+點 6 月:確認 3~6 月都被選取,底部合計/平均只算這 4 個月,分析卡片標題顯示「已選 4 個月」。
4. 點某個欄位標題:確認整欄變淡、加刪除線,右側總計與月增率跟著變。再點一次恢復。
5. 點一個非零的格子:確認右側抽屜列出的交易加總等於該格金額;退款列帶「退款」標記,金額為負。
6. 切成子類別維度:確認欄位標題上方有一級分類的小字,子類別交易沒有同時算進一級分類欄。
7. 切成專案、帳戶分組維度,切換支出/收入:確認「(無)」/「未分組」固定在最右邊。
8. 分析卡片三個分頁都能畫出來,區間變化勾「累積」後線條單調遞增(沒有退款時)。
9. 深色模式下表頭、固定欄、選取高亮都清楚可讀。
