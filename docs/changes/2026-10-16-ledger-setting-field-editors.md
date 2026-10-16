# 帳本設定逐欄編輯對齊 App

2026-10-16。入口 `/app/ledgers?settings=...`。

名稱、帳本幣種、每月起始日改為各自可操作列，直接開單欄名稱編輯、共用幣種選擇器、1～28 日選單，不再經共同的完整編輯介面。幣種選擇後要求換算確認；取消不更新。建立帳本保留完整表單。

`LedgerEditDialog` 接欄位模式，沿用既有提交鎖、權限、重名檢查與失敗保留；`ledgerFieldPatch` 每次只更新被選欄位，避免重試時寫回其它欄位的舊資料。`updateLedgerMeta` 的衝突重試和 backend 幣種重算沿用原流程，沒有新增 API 或 schema。

Web 272 項測試與 TypeScript／Vite build 通過。App 同步修正並通過 24 項帳本／導航／導覽測試，詳見 App repo 同名變更紀錄。未部署；真實跨裝置與完整實機驗收仍待後續進行。
