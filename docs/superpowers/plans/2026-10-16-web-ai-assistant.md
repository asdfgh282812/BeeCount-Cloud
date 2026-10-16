# Web AI 助手實作計畫

**Goal:** 將 App 的財務對話體驗接到 Web 右下角浮動視窗。

**Architecture:** AppShell 常駐助手，聊天按使用者與帳本隔離並保存在本機。Cloud 新增具權限檢查的 SSE 對話端點，以白名單工具呼叫既有 read handler，記帳沿用 parse-tx-text 與交易草稿確認流程，所有寫入仍走既有同步引擎。

**Tech Stack:** FastAPI、Pydantic、React、TypeScript、既有玻璃材質與 AI provider client。

使用者已確認設計並要求直接實作，本次於目前工作目錄執行，不再增加設計審批。

- [x] `src/services/ai/chat_tools.py`：工具白名單、參數驗證、既有讀取查詢轉接；禁止模型指定使用者或帳本。
- [x] `src/routers/ai/chat.py`：驗證帳本、意圖辨識、唯讀快捷問題、最多三個查詢、最近八則可選記憶、SSE 回應與記帳草稿。
- [x] `frontend/packages/api-client/src/assistant.ts`：支援中止的 SSE client。
- [x] `frontend/apps/web/src/components/ai-assistant/`：對話儲存、浮動按鈕、聊天視窗、快捷建議、記憶開關、清除、重試、結果卡與草稿確認。
- [x] `tests/test_ai_chat.py`：跨使用者隔離、無設定、唯讀模式、記憶限制、錯誤與財務查詢；日期驗證涵蓋台灣凌晨及深夜。
- [x] 執行相關 pytest、Vitest、前端 build，瀏覽器操作桌面與手機、亮暗色、收起重開、切換帳本、中文輸入與錯誤恢復。
- [x] `docs/changes/2026-10-17-web-ai-assistant.md`：記錄實際範圍、資料與安全邊界、驗證結果。
