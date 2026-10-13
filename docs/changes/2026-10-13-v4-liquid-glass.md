# 4.0 Liquid Glass 三種材質

App／Web 的 `feat/v4-liquid-glass` 分支新增清透（預設）、平衡、厚霧。Web 入口：右上角頭像 → 個人資料 → 同步偏好 → Liquid Glass。

- `ProfileAppearance.glass_style` 使用 `clear | balanced | tinted`，保存时與原始 appearance 合併，避免丟失 App 的其他偏好。既有後端 `appearance: dict` 無需 migration。
- AppShell 依 profile 更新根元素 dataset；styles.css 的玻璃透明度、面板厚度、模糊與 App BeeGlassTokens 對齊，亮暗色各有基底與高光。
- MobileBottomNav 改成安全區上方懸浮膠囊與滑塊；AppHeader、共用 Dialog／Sheet 採同一材質。內容卡片保持實色。減少動畫、高對比／減少透明度及不支援 backdrop-filter 有降級。
- 保留 Web 原有導覽頁目；App 的首頁／記帳雙態及 `appearance.glass` tour 是 App 專屬。App 圖示與 logo 未改。

驗證：29 個檔案共 258 項單元測試與 production build 通過；正式元件在本機隔離測試頁實際操作 A／B／C、亮暗色、行動導覽、對話框與選擇面板，測試頁已移除。未連接真實 Cloud 實測雙裝置同步。完整理由與 App 驗證限制見 App repo `docs/changes/2026-10-13-v4-liquid-glass.md`。

Flutter／CSS 材質使用模糊、透色與高光，不宣稱與 iOS 原生折射相同。不部署、不併 main；部署順序 Cloud → Web → App。
