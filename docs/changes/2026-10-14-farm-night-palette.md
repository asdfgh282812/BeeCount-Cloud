# 農場夜景共用色票

App 農場入口仍為首頁農場卡片／我的 → 農場。夜景改為深藍紫色、暖色窗光與路燈，並呈現夜間雲、雨、雪與風；開發版天氣預設也將日夜與天氣分開。

Web 農場尚未實作，仍屬 M4。本次只將 App `BeePalette`／`BeeTokens` 的六個夜景色票同步到 `frontend/apps/web/src/styles.css`（`--farm-*`）及 `frontend/packages/ui/src/theme/primary-color-script.ts`（`FARM_NIGHT_PALETTE`），為後續共用農場畫面提供一致色碼。色票由真實日夜決定，不隨介面 `.dark` 或使用者主色改變；現有 Web 記帳介面沒有套用夜色。

`pnpm -C apps/web build` 通過，仍有既有大 bundle 提示。沒有新增 API、改同步契約、部署或合併 main。App 完整設計、導覽 `farm.night` 與測試紀錄見 `/Users/andy/BeeCount-main/BeeCount-main/docs/changes/2026-10-14-farm-night-weather.md`。
