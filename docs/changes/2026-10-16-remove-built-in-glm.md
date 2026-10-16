# 移除 Web 預設內建 GLM

2026-10-16，配合 App 使用者要求移除服務商列表的內建 GLM 預設。

入口仍是「智慧記帳 → AI 服務管理」。`normalizeAiConfig` 過濾未填 Key 的歷史內建 GLM；已設定者保留 Key、模型、ID 與綁定，轉為可編輯／刪除的一般服務商。設定 merge 沿用正規化結果，保留其它偏好與未知欄位。空清單不新增 provider，能力不隱含指向 GLM。

刪除後將使用該 provider 的能力設為 null，不自動轉到別家。App 沿用同一語意。資料共享說明與刪除確認同步修正文案，不再稱預設傳送給智譜。未修改 backend schema 或 wire key；不批次清理使用者帳號。

驗證：Web 269 項測試、TypeScript／Vite build 與 diff check 通過。App 339 項 AI／UI／導覽測試通過，詳見 App repo 的同名 `docs/changes/2026-10-16-remove-built-in-glm.md`。未部署、未發送真實 AI 請求。
