# Web 玻璃選項統一

使用者指出頂部導覽、記帳類型、欠款進階設定還在使用舊的深色填滿選取塊。前一次只接上「一般／週期／分期／訂金」，其他頁面仍自行繪製分段列，本次將這些呼叫點收斂到既有 `GlassSegmented`／`GlassChoice`，而不是再為每頁加一套樣式。

## 共用層

`packages/ui/src/ui/glass-segmented.tsx` 支援文字／圖示／計數的 ReactNode label、數字值、原有 test id、停用提示、tablist 與導覽的 aria-current。仍沿用按住放大 1.06、直接跟手、放開才切換、取消返回的互動與 A／B／C 材質。無對應選項（例如在設定頁看主導覽）不會誤亮首頁；選项集合變動中斷舊手勢；原生 fieldset 的停用也納入判斷。方向鍵／Home／End、減少動態、減少透明度與高對比沿用既有共用邏輯。

`apps/web/src/styles.css` 的 `.bc-glass-nested` 在已經有玻璃的 header／底部列內停用第二次模糊，但保留材質與邊緣。沒有改動 App/Web 共用玻璃數值。舊 `TabsList`／`TabsTrigger` 介面保留相容，當前頁面已無呼叫點；新分段列使用 GlassSegmented。

## 已接入的入口

| 區域／檔案 | 使用者入口與調整 |
| --- | --- |
| `AppHeader`、`GlassNavigation`、`MobileBottomNav` | 頂部首頁／交易／帳戶／分類／標籤／專案／比較報表與手機底部列，統一玻璃滑塊與拖曳。中型視窗將七項導覽放到獨立一排，避免與品牌／帳本／工具按鈕擠在一起。 |
| `TransactionsPanel` | 新增／編輯交易 → 支出／收入／轉帳／應收／應付；拆帳分類 → 收支／欠款分類切換。保留欠款權限、原表單狀態切換、已有還款的鎖定限制。 |
| `DebtEntryForm` | 記帳 → 應收／應付 → 進階設定 → 單次／分期、款項類型。仍操作 draft，取消不寫回。 |
| `DebtsPanel` | 應收應付款項 → 狀態分頁、建立款項 → 應收／應付。保留計數與對象篩選重置。 |
| `CategoriesPanel` | 分類 → 收支／欠款類型分頁，保留計數。 |
| `DetailScopeToggle` | 帳戶／分類／標籤詳情 → 全部帳本／目前帳本。 |
| `ComparisonReportPage`、`ComparisonReportCard` | 比較報表各分段維度、首頁比較卡的月／年與環比／同比。 |
| `TxTemplatesPanel` | 交易範本 → 常用／手動排序。 |
| `RecurringRulesPanel` | 週期性收支 → 類型與狀態篩選、建立規則 → 一般／股票定期定額；編輯時類型鎖定不變。 |
| `InvestmentsPage` | 股票 → 新增交易 → 一次買賣／定期定額。 |
| `AccountsPanel` | 帳戶 → 編輯信用卡 → 繳款期限方式。 |
| `ProjectsPanel` | 專案 → 編輯 → 期間類型、每日預算方式；分類預算 → 固定／比例，無總預算仍禁用比例。 |
| `BudgetsPanel` | 預算 → 建立／編輯 → 總預算／分類預算，保留編輯鎖定與只能一個總預算的限制。 |
| `ProviderEditDialog`、`AdminBackupPage` | AI → 供應商設定 → API 家族；管理 → 備份 → 執行紀錄／排程／遠端。 |

獨立、多選或不等長的 chips（欠款對象、週期／交易回饋規則、星期、備份 cron 快選、股票幣別、回饋上限群組）使用 GlassChoice，保留獨立勾選。它們不做拖動連續勾選。分類圖示網格、原生下拉、直向說明型 radio 選項與一般內容卡片不是分段列，本次不把它們改成滑塊。

入口「頭像 → 個人資料 → 同步偏好 → Liquid Glass」的繁／簡／英說明補上分段列與拖動操作。App 已有 BeeSegmented、BeeGlassSurface 與 `editor.glassDrag` 導覽；本次是 Web 補齊原設計，沒有新增 App 功能、同步欄位或新的設定值，因此不新增 App tour id。Web 本身沒有 spotlight tour 系統。

## 驗證

- Web 29 個測試檔、258 項測試通過，TypeScript／Vite build 通過；既有 bundle 大小及第三方 use-client 指令警告仍存在。
- 本機瀏覽器掛載實際 `GlassNavigation`、`MobileBottomNav`、`TransactionsPanel`（含實際 `DebtEntryForm`／進階對話框）與 `DetailScopeToggle`；操作頂部／底部拖動、五種交易類型、收入模式限制、單次／分期、款項類型與取消草稿。
- 驗證非主要頁面不誤亮首頁、拖到停用項目不切換、鍵盤、A／B／C、亮暗色、減少動態、430×900 手機視窗。使用假資料且保存回呼不寫入 API。
- 瀏覽器既有 `DialogOverlay` function ref 警告仍在，與本次分段列無關；沒有新增共用元件的 runtime error。未啟動 iOS Simulator。
- 驗證頁與開發伺服器在驗證後移除／停止，沒有部署；變更留在 feat/v4-dev。
