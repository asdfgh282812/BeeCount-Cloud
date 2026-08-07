# Phase 8 紅利回饋計算與規則管理（需求 #4、#5、#5-1、#15、#16）— 測試報告 + 手動測試清單

依 [`PH6_USER_FEEDBACK_2026-08_SD.md` § Phase 8](./PH6_USER_FEEDBACK_2026-08_SD.md#phase-8紅利回饋計算與規則管理需求-45-1516) 實作，範圍：

- **#4**：取整方式沒有真的取整到整數——新增「總額取整方式」，兩段式設計
  （單筆先不取整/依單筆設定取整，總額才依總額設定再取整一次）。
- **#5**：交易日期/金額事後修改，已入帳的回饋金要跟著沖銷重算。
- **#5-1**：回饋金入帳時間應對齊原交易時間，不是固定 08:00。
- **#15**：「週期結束後一次結算」要能選回饋日期（當月/次月/次N月的第幾天）。
- **#16**：規則有交易掛著後鎖定計算欄位；刪除改軟刪除；已結束的規則收進
  可折疊區；共用上限群組欄位依「本期回饋上限」是否有值決定顯示。

**依 SD 原文說明**：#16 提到「帳戶選擇比照 Phase 11」，但 Phase 11（新增交易
表單改造）尚未實作，此項**未包含在本次範圍**，維持現有下拉選單樣式；其餘
#16 項目（鎖定、軟刪除、已結束收合、共用上限群組條件顯示）皆已完成。

## 程式改動摘要

### 後端

- `src/models.py`：`ReadCardRewardRuleProjection` 新增
  `total_rounding`/`settlement_month_offset`/`settlement_day_of_month` 三個
  欄位。Migration `alembic/versions/0037_card_reward_rounding_and_settlement_date.py`。
- `src/services/card_rewards.py`：
  - `_round_amount` 新增 `to_integer` 參數 + `"keep"` 選項（單筆保留小數不
    取整；總額 keep 仍清理到 2 位小數但不強制整數）。
  - `compute_account_card_rewards` 加總後依 `rule.total_rounding` 再取整一次
    （round/floor/ceil 到整數）。
  - `compute_settlement_date` 的 `period_end` 分支支援
    `settlement_month_offset`/`settlement_day_of_month`。
  - 新增 `combine_settlement_date_with_source_time`（結算日期 + 來源交易
    時分秒，取代固定 00:00:00）。
  - 新增 `rule_has_history`（規則是否已有交易/入帳紀錄），供寫入端鎖定判斷
    與讀取端 `locked` 旗標共用同一份邏輯。
- `src/services/card_reward_payout.py`：
  - `_materialize_per_tx` 呼叫 `combine_settlement_date_with_source_time`
    對齊來源交易時間。
  - 新增 `reverse_card_reward_payouts_for_edit`：交易的
    happened_at/amount/category_id/account_id 任一實際變動時，刪除該交易
    先前逐筆結算入帳的回饋交易 + 去重記錄，讓下一輪排程用新值重新計算。
- `src/routers/write/_shared.py`：交易 PATCH 路徑（`_commit_write_fast_tx`）
  merge 完成後比對 `prev_item`/`new_item` 是否有上述四個影響計算的欄位變動，
  變動時呼叫 `reverse_card_reward_payouts_for_edit`。
- `src/routers/write/card_reward_rules.py`：
  - 新增 `_assert_card_reward_rule_editable`：規則有歷史時，PATCH 若帶了任一
    計算相關欄位（rate_type/rate_value/rounding/total_rounding/calc_basis/
    interval/category_ids/min_spend_threshold/min_tx_amount/cap_amount/
    cap_shared_key/settlement_type/settlement_days/
    settlement_month_offset/settlement_day_of_month/reward_account_id）
    一律 422。
  - DELETE 端點：規則有歷史時改呼叫 `update_card_reward_rule` 把
    `enabled` 設為 `false`（軟刪除），沒有歷史才走原本的物理刪除。
- `src/routers/read/ledgers.py`：`ReadCardRewardRuleOut` 新增 `locked` 欄位
  （呼叫 `rule_has_history`），供前端提前 disable 欄位不用等 PATCH 422。
- `src/snapshot_mutator.py`/`src/projection.py`/`src/sync_applier.py`/
  `src/snapshot_builder.py`/`src/schemas.py`：依 CLAUDE.md SOP 補齊新欄位的
  create/update/merge/snapshot 六個位置。
- `src/error_handling.py`：新增 `CARD_REWARD_RULE_LOCKED` 錯誤碼映射。

### 前端（`CardRewardRulesSection.tsx`）

- 規則列表拆成「進行中」+ 可折疊「已結束的活動」兩組（已結束 =
  `isExpired || !enabled`），抽出共用的 `renderRuleRow`。
- 規則行新增「已鎖定」徽章（`rule.locked`）。
- 表單新增「總額取整方式」下拉；「單筆取整方式」新增「保留小數」選項。
- `settlement_type === 'period_end'` 時展開「回饋入帳日」月份 + 日期兩個
  下拉（未設定=維持現況期末當天，有「恢復預設」清空按鈕）。
- 規則鎖定時：計算相關欄位全部 disable + 頂部提示文案；`label`/`note`/
  `enabled`/`starts_at`/`ends_at` 維持可編輯；PATCH 只送出這幾個欄位，避免
  即使值沒變也因為欄位存在於 payload 而被後端擋 422。
- 「共用上限群組」區塊只在「本期回饋上限」有填值時才顯示。
- `frontend/packages/api-client/src/types.ts`：對應補上新欄位型別。
- i18n 三語系（en/zh-CN/zh-TW）補齊新字串，含 `error.CARD_REWARD_RULE_LOCKED`。

## 自動化測試

- **後端**：`tests/test_card_rewards.py` 新增 9 個測試（總額取整、
  `compute_settlement_date`/`combine_settlement_date_with_source_time` 純
  函式測試、settlement 欄位建立/校驗/partial-update 契約、規則鎖定 422 +
  可編輯欄位仍可過、軟刪除 vs 物理刪除、回饋交易時間對齊、事後修改沖銷重算）
  全數通過。`python -m pytest tests/ -q` 全量跑過（89 個 card-reward 相關
  測試全過），**只有 2 個跟本次改動無關的既有失敗**
  （`test_import_simple.py::test_accounts_parent_before_child_required`、
  `test_recurring_rules.py::test_recurring_occurrence_update_overridden_skipped_by_update_from`
  ——已用 `git stash` 確認在改動前的乾淨樹上就會失敗，不是這次引入的）。
- **前端**：`pnpm -C apps/web build`（tsc + vite build）與
  `pnpm -C apps/web test:unit`（73 例，含三語系 key 一致性的 `i18n.test.ts`）
  都過。
- **Lint/typecheck**：`ruff check`/`mypy` 跑過，跟改動前的 baseline 比對
  （`git stash` 前後計數一致），沒有新增錯誤；本次新寫的檔案
  （`tests/test_card_rewards.py`、migration）本身零錯誤。

## 瀏覽器手測（本輪已由我實際操作驗證，非僅憑自動化測試）

用測試帳本既有資料：主帳戶群組「國泰」底下的子卡「cube」，已有一條 3% 回饋
規則、兩筆逐筆結算入帳的回饋交易（+29.7、+24，固定顯示 08:00 ——這是舊資料，
建立於本次改動之前，不受影響；本次改動只影響**之後**新產生的回饋交易）。

> 手測前先踩到並修好了 CLAUDE.md 已知的「殭屍 listener」陷阱（本地 `:8080`
> 有個 `--reload` 掛著但 reload 沒真的生效的舊行程，`taskkill /T` 砍掉整棵
> 行程樹重開才吃到新 schema）與「Service Worker 快取」陷阱（`localhost:5173`
> 殘留舊版 SW 快取，`unregister()` + 清 Cache API + 硬性重整才吃到新前端
> 程式碼）——過程詳見對話記錄，這裡只記錄修好之後的驗證結果。

- [x] **#16 規則鎖定（GET `locked` 旗標 + PATCH 422 + UI disable）**：打開
      「cube」詳情頁 → 紅利回饋，既有 3% 規則正確顯示「已鎖定」徽章；點編輯，
      彈窗頂部出現鎖定提示文案，「回饋方式/回饋百分比/計算週期/單筆取整/
      總額取整/單筆最低金額/本期累積消費門檻/本期回饋上限/回饋入帳時機/天數/
      回饋帳戶」全部呈灰階不可互動（用 JS 驗證 `disabled` 屬性逐一為
      `true`），「規則名稱/活動開始日/活動結束日」維持可編輯且可正常送出
      （PATCH 200，label 成功更新且規則計算欄位不變）。
- [x] **#4 兩段式取整（keep + total_rounding）**：新增一條規則，「單筆取整
      方式」下拉正確出現第 4 個選項「保留小數(不取整)」；「總額取整方式」
      新欄位正確顯示 round/floor/ceil/keep 四選項 + 提示文字「單筆各自取整
      後加總,再依此設定取整一次」；選「保留小數」+「無條件捨去」建立後，
      重新打開編輯彈窗兩個欄位值正確持久化（round-trip 正確）。
- [x] **#15 週期結束回饋日期可設**：「回饋入帳時機」選「週期結束後一次
      結算」時，正確展開「回饋入帳日」月份（當月/次月/次二月/次三月）+
      日期（1~28）兩個下拉，未設定時顯示「不設定則維持在週期結束當天入帳
      (預設)」提示；選「次月」+「5 日」建立後，重新打開編輯彈窗兩個欄位值
      正確持久化。
- [x] **#16 共用上限群組條件顯示**：新建規則「本期回饋上限」留空時，畫面
      上完全沒有「共用上限群組」區塊（不是隱藏，是整段不渲染）。
- [x] **#16 軟刪除 + 已結束活動收合**：對有交易/入帳紀錄的鎖定規則按刪除
      （DELETE 200），規則**沒有從清單消失**，改成收進新的「顯示已結束的
      活動(1)」可折疊區塊，展開後看到該規則同時帶「已停用」+「已鎖定」兩個
      徽章、狀態文字「規則已停用或不在生效期間」；重新編輯把「啟用此規則」
      勾回去存檔後，規則正確移回進行中清單，不再顯示「已停用」。
- [x] **驗證後還原測試資料**：把上面建立/修改的測試規則名稱改回原樣、
      啟用狀態還原、新建的測試規則刪除乾淨，「cube」卡片最終只剩下原本那條
      3% 規則、狀態與改動前一致。

### 這輪沒有覆蓋到、建議你自己再點一次的情境

- [ ] **#5 事後修改重算（實際排程觸發）**：這項邏輯已有完整 pytest 覆蓋
      （`test_editing_source_tx_impactful_field_reverses_and_recomputes_reward`），
      但這輪瀏覽器手測沒有實際跑過「改一筆已經有回饋入帳的交易金額/日期 →
      等排程（5 分鐘一次）或手動觸發 `POST /internal/tasks/materialize-
      recurring` → 確認舊回饋交易消失、新回饋交易用新金額補上」的完整流程。
      建議：找一筆已經有「回饋金」子交易的消費，改一下金額存檔，等 5 分鐘
      後重新整理交易列表，確認舊回饋交易不見了、新回饋交易金額/日期對得上
      改過的值；接著再測「只改備註不改金額」不會觸發沖銷（舊回饋交易應該
      維持原樣沒被動過）。
- [ ] **#5-1 回饋交易時間對齊（實際排程觸發）**：同上，需要一筆**新**的
      消費交易（設一個非整點的 `happened_at`，例如 14:37）掛上逐筆結算規則，
      等排程跑過之後，確認產生的回饋交易時間是「結算日 14:37」而不是固定
      「08:00」（cube 卡上現有的兩筆回饋交易是改動前的舊資料，固定顯示
      08:00 是預期的，不代表這項沒修好，純粹是舊資料不受影響）。
- [ ] **PATCH 422 的錯誤提示文案**：`error.CARD_REWARD_RULE_LOCKED` 這個
      i18n key 這輪只用程式碼直接呼叫 API 觸發過 422（pytest），沒有實際在
      UI 上透過某個路徑觸發「PATCH 帶了鎖定欄位」進而看到 toast 顯示這串
      文案（目前前端已經用 disable UI 擋掉了正常操作路徑下送出鎖定欄位的
      可能性，只有繞過 UI 直接呼叫 API 才會撞到，所以這個 UI 提示文案理論上
      使用者不會在正常操作下看到，價值主要是防禦性的）。
- [ ] **簡體中文/英文介面**：這輪手測全程用繁體中文介面驗證，簡體中文
      （zh-CN）跟英文（en）兩語系的新增字串是自動化 `i18n.test.ts` 驗證 key
      有對齊，但沒有實際切換語言肉眼看過畫面排版有沒有跑版（尤其英文字串
      普遍比中文長，「總額取整方式」的提示文字、「回饋入帳日」的月份選項
      英文版可能比較長，建議切一次語言看看下拉選單/提示文字有沒有被截斷）。
- [ ] **主帳戶（account_group）視圖下的鎖定/收合行為**：這輪只在「cube」
      這張子卡的視圖測過，沒有從「國泰」主帳戶視圖（`GroupCardRewardsSummary`
      展開後的多卡 Dialog）裡再測一次同樣的鎖定/軟刪除/已結束收合行為——
      理論上共用同一個 `SingleCardCardRewards` 元件應該行為一致，但沒有
      實際點過主帳戶入口確認。

## 環境備註（跟本次程式改動無關，僅記錄給下次遇到類似狀況參考）

這次手測連續踩到 CLAUDE.md 已經記錄過的兩個陷阱的實例，順手補充一個新細節：

- **殭屍 listener**：本地 `:8080` 的 uvicorn `--reload` 進程雖然還活著、
  `netstat` 也顯示 LISTENING，但改動的 schema（新增的
  `total_rounding`/`locked` 等欄位）遲遲沒有反映在 `/openapi.json`。用
  `Get-CimInstance Win32_Process -Filter 'ProcessId=<pid>'` 查它的
  `CommandLine` 確認真的是專案的 `.venv` uvicorn（不是別的進程借用了這個
  port），但它的 reloader 子進程（`multiprocessing.spawn`）明顯沒有真的
  重新載入過 `src/` 底下的程式碼——`taskkill /F /PID <pid> /T` 整棵砍掉、
  重新用 `python -m uvicorn server:app --reload --host 0.0.0.0 --port 8080`
  背景啟動後，`/openapi.json` 立刻就有新欄位了。**這次沒有另外執行過
  `alembic upgrade head`，migration 是手測前才補跑的**——新欄位在
  DB schema 沒補齊之前，即使程式碼是新的，實際查詢（`_card_reward_rule_to_out`
  讀 `row.total_rounding`）也可能因為 SQLite 沒這欄位而整支噴錯，建議下次
  遇到「明明重開了 server 還是不對」時，除了懷疑殭屍 listener，也順手確認
  一下 migration 有沒有補跑。
- **Service Worker 快取**：`localhost:5173` 這次連 `caches.delete()` +
  `serviceWorker.getRegistrations()[].unregister()` 都執行過了，緊接著的
  普通 `navigate()`（相當於瀏覽器網址列打開新分頁）**還是**吃到舊的元件
  渲染結果（規則列表沒有「已鎖定」徽章、沒有「保留小數」選項）；换成
  `Ctrl+Shift+R` 真正的硬性重整（bypass HTTP cache，不只是清 SW 的 Cache
  API）之後才真正吃到新版。也就是說 unregister 服務工作者 + 清快取，
  **不保證**下一次普通導航就會拿到新內容（可能是舊 SW 在 unregister 生效前
  的那個 tick 就已经把回應交回去了，或是瀏覽器層的 HTTP disk cache 獨立於
  Service Worker 的 Cache API 之外還留著一份）——下次遇到改了程式碼、
  Vite dev server 用 `curl` 驗證過確實吐新原始碼、但畫面還是舊的，直接跳過
  「unregister + 清 cache + 普通重整」這個組合，一律用 `Ctrl+Shift+R` 硬性
  重整比較省事。
