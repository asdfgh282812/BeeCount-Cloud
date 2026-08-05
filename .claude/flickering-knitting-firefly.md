# 五項功能改動計畫

## Context

使用者提出五項獨立但都不小的改動請求。已確認的執行原則：

- **依序一項一項做**：完成一項→跑 `pytest`→用瀏覽器實際走一遍→確認沒問題→才進下一項。這對齊 CLAUDE.md 裡反覆記錄的教訓（只跑 pytest/build 不做瀏覽器手測，下一輪幾乎必然挖出純前端 bug）。
- 每項的範圍與已確認的邊界見各 Phase 開頭。

執行順序：**Phase 1 帳戶級聯刪除 → Phase 2 匯入功能 → Phase 3 銀行卡改名+新台幣預設 → Phase 4 主帳戶擴充銀行帳戶 → Phase 5 排程管理後台**（由小到大、由風險低到風險高排列，Phase 5 是唯一牽動背景排程架構的大改動，放最後）。

---

## Phase 1：帳戶級聯刪除交易

**範圍確認**：只對「一般交易」做級聯刪除。若帳戶還被結構性設定引用（週期性收支規則、分期付款、交易範本、信用卡回饋規則、自動扣繳來源帳戶 `auto_pay_from_account_id`、被其他帳戶當 `parent_account_id`），**沿用現況繼續擋下**，提示使用者先去對應功能頁面處理，不自動連動刪除。

### 後端

- `src/snapshot_mutator.py::delete_account`（現況 746-789 行）：
  - 現有「子帳戶群組」擋下邏輯（`parentAccountId` 指向自己）維持不變。
  - **新增**結構性引用檢查：掃描 `recurring_rule`(`account_sync_id`/`from_account_sync_id`/`to_account_sync_id`)、`installment_plan`(`account_sync_id`)、`tx_template`(`account_sync_id`/`from`/`to`)、`card_reward_rule`(`account_sync_id`/`reward_account_id`)、任何帳戶的 `auto_pay_from_account_id` 指向本帳戶 —— 命中任一個就擋下（`ValueError`，訊息帶上實體類型與筆數，方便前端顯示具體提示,例如「此帳戶被 2 條週期性收支規則使用中」）。
  - 交易引用檢查改成依 `cascade` 參數分流：`cascade=False` 維持現況擋下；`cascade=True` 時，先掃出所有引用本帳戶的交易 id，比照 `src/routers/write/transactions_batch_delete.py`（67-272 行）的逐筆 `delete_transaction` + 失敗原因收集模式檢查是否有 `installment_linked`（分期關聯）交易——**只要有一筆卡在這個關卡，整個級聯刪除中止**並回報「N 筆交易因分期付款關聯無法刪除，請先處理分期付款」；否則依序刪除所有引用交易,最後刪除帳戶本身,一次性回傳 diff 讓呼叫端用單一 `_emit_entity_diffs` + 單筆審計 log + 單次 WS broadcast 提交（不要為交易和帳戶分兩次 commit）。
  - Mobile 端寫入路徑（現有 763 行附近註解說明的「允許 orphan」）不受影響，這個改動只影響 web 的 `/ledgers/{id}/accounts/{id}` DELETE 路徑。
- `src/schemas.py`：在 `WriteEntityDeleteRequest`（1374-1375 行）旁新增 `WriteAccountDeleteRequest(WriteEntityDeleteRequest)`，加一個 `cascade: bool = False` 欄位（獨立 schema，避免影響其他實體共用 `WriteEntityDeleteRequest` 的既有行為）。
- `src/routers/write/accounts.py::delete_acc`（110-152 行）：body 改用 `WriteAccountDeleteRequest`，把 `cascade` 傳進 `delete_account`；回應加上 `deleted_transaction_count` 讓前端核對跟預覽時看到的數字一致。

### 前端

- `frontend/apps/web/src/pages/sections/AccountsPage.tsx`：
  - `onDelete` handler（643-678 行）拿掉「`tx_count > 0` 直接擋下」的早退邏輯。
  - 改成兩段式確認：第一個對話框（仿 `frontend/apps/web/src/components/dialogs/LedgerDeleteConfirmDialog.tsx` 的「先讓使用者看到會刪掉什麼」風格）顯示「此帳戶目前有 N 筆交易，刪除帳戶將一併刪除這些交易，此操作無法復原」；使用者按繼續後彈出第二個更強語氣的確認（仿 `frontend/apps/web/src/components/tx-batch/BatchDeleteDialog.tsx` 的 destructive 樣式 + loading spinner），按下才真正呼叫帶 `cascade: true` 的 DELETE。
  - 若後端回傳結構性引用擋下的錯誤,顯示對應提示 toast（依錯誤訊息裡的實體類型給出「請先到 XX 頁面處理」的具體文案）。
- `frontend/packages/api-client`：找到目前的 account delete client 方法,擴充帶上 `cascade` 參數。

### 測試

- 找到現有帳戶相關 pytest 檔（`tests/` 下 grep `delete_account`）,新增：cascade 成功刪除帳戶+其交易（單次 commit）、cascade 但命中分期關聯交易時整體中止且不留部分刪除、結構性引用（週期性收支/分期/範本/回饋規則/自動扣繳來源）不論 cascade 與否一律擋下、owner-only 權限不變、mobile push 路徑不受影響。

### 驗證

`pytest tests/ -q` 全過 → `make dev-api` + `make dev-web` → 瀏覽器實際建一個有交易的帳戶,走完整刪除流程（含被結構性設定引用時的擋下提示）。

---

## Phase 2：匯入功能（分類 / 帳戶 / 交易，各自獨立匯入 + 範本下載）

**現況**：`src/routers/import_data/` 目前只有「交易」匯入是使用者可見功能；分類/帳戶只在交易匯入時被動 side-effect 建立（`_collect_new_accounts`/`_collect_new_categories`，`endpoints.py` 763-805 行）。`src/services/import_data/schema.py` 裡已經定義了 `ImportAccount`(99)/`ImportCategory`(106) dataclass 但從未被使用——這是現成的起點,實作時檢查欄位是否對得上 `snapshot_mutator.create_account`/`create_category` 的參數,不夠再補。`openpyxl` 已是既有依賴且可讀可寫,不需要新套件。

### 後端

- `src/services/import_data/`：新增 `parsers/categories.py`、`parsers/accounts.py`——因為範本是自己定義的固定欄位格式,**不需要**像交易匯入那樣做「beecount vs generic」格式偵測,直接照固定表頭欄位讀取即可（分類：類型 income/expense、名稱；帳戶：名稱、類型、幣別、期初餘額、主帳戶名稱(可留空)、額度/入帳日/繳款日(可留空,信用卡/主帳戶用)）。實作前先讀 `snapshot_mutator.create_category`/`create_account` 簽章確認欄位。
- `src/routers/import_data/endpoints.py`：`/upload`、`/{token}/preview`、`/{token}/execute` 三個端點加上 `entity_type: Literal["transactions","categories","accounts"]`（存進 cache token,`/preview`/`/execute` 從 token 讀回,不用每次都帶）。`execute` 依 entity_type 分流呼叫對應 parser + `create_category`/`create_account`,沿用既有的 lock ledger → 單一 DB transaction → SSE stage 事件 → `_emit_entity_diffs` → 審計 log → WS broadcast 整套模式(`_do_execute`,現況 406-593 行)。
- 新增範本下載端點,例如 `GET /import/template?entity_type=...&format=csv|xlsx`：CSV 沿用 `src/routers/read/workspace.py` 匯出交易 CSV 用的 `_CSV_HEADERS_BY_LANG`（320-331 行附近）在地化表頭風格；xlsx 直接用 `openpyxl.Workbook()` 寫入(新程式碼,不需新依賴)。三種 entity_type 各自的欄位需與對應 parser 要求的欄位完全對齊。

### 前端

- `frontend/apps/web/src/pages/sections/ImportPage.tsx`：頂部加一個 entity type 切換（交易 / 分類 / 帳戶,預設交易,保留現有交易匯入流程不變）。切換時重置 wizard 狀態,並依 entity_type 換渲染對應的 preview/stats 元件——分類/帳戶所需的 preview 表格比交易單純,新增 `CategoriesPreviewCard.tsx`/`AccountsPreviewCard.tsx`（仿 `frontend/apps/web/src/components/import/TransactionsPreviewCard.tsx` 的表格樣式）,`ImportStatsCard.tsx` 視情況擴充或另建簡化版。
- 在 `FileDropZone` 附近加「下載範本」按鈕（CSV / Excel 兩種格式),呼叫新的範本端點,下載方式仿 `frontend/packages/api-client/src/read.ts` 的 `downloadWorkspaceTransactionsCsv`（blob 下載 + `Content-Disposition` 檔名解析）。
- `frontend/packages/api-client/src/import.ts`：`uploadImport`/`previewImport` 加 `entityType` 參數;新增 `downloadImportTemplate(token, entityType, format)`。

### 測試

- 找到既有交易匯入的 pytest 檔（grep `import_data`）,比照新增分類/帳戶匯入的 upload/preview/execute 成功路徑 + 常見驗證錯誤（重複名稱、必填欄位缺漏、無效 account_type/currency）+ 範本下載端點回傳正確表頭。

### 驗證

`pytest tests/ -q` → 瀏覽器手測三種 entity_type 各自完整跑一次（下載範本 → 依範本填資料上傳 → 預覽 → 送出 → 確認資料真的寫入）。

---

## Phase 3：「銀行卡」→「銀行」改名 + 新台幣預設幣別

**範圍**：只動 web 前端(mobile 是獨立 Flutter repo,不在本次範圍)。「預設新台幣」只套用在「全新建立、沒有任何上下文可推導幣別」的表單初始值,凡是幣別由既有帳戶/情境帶出的地方(例如替某個已存在的外幣帳戶記一筆交易)維持沿用該情境的幣別,不強制蓋成 TWD。

### 改名

- `frontend/apps/web/src/i18n/zh-TW.ts:773` `'accountType.bank_card': '銀行卡'` → `'銀行'`
- `frontend/apps/web/src/i18n/zh-CN.ts:308` `'accountType.bank_card': '银行卡'` → `'银行'`
- `frontend/apps/web/src/i18n/en.ts:774` `'accountType.bank_card': 'Bank card'` → `'Bank'`
- （其餘出現「銀行卡/银行卡」的地方都只是程式碼註解,不動。）

### 新台幣預設

- `frontend/packages/web-features/src/forms.ts:306-319` `accountDefaults()` 的 `currency: 'CNY'` → `'TWD'`。
- 實作時全域 grep `'CNY'` 在 `frontend/` 下的其他「表單預設值」用法(例如帳本建立預設幣別、預算預設幣別等),逐一確認是「空白表單初始值」才改成 `'TWD'`;凡是「跟隨既有帳戶/帳本幣別」的邏輯一律不動。
- 順便檢查 `src/schemas.py`/`snapshot_mutator.py` 有無後端層的預設幣別(Pydantic Field default)—如果有,確認 mobile 是否依賴這個後端預設(mobile 若總是明確帶 currency 則可放心一併改,若不確定則只動前端,後端維持現況避免影響 mobile)。

### 驗證

`pnpm build` + `pnpm test:unit`(含 i18n 三語系 key 一致性檢查)過 → 瀏覽器手測:①帳戶類型下拉/顯示文字都變成「銀行」;②新建帳戶/新建帳本等空白表單幣別預設是新台幣;③替既有非台幣帳戶記交易,幣別仍正確跟隨該帳戶,不被強制改成台幣。

---

## Phase 4：主帳戶（群組合併）開放銀行帳戶掛靠

**範圍確認**：只加開 `bank_card` 可以掛靠主帳戶群組,`cash`/`alipay`/`wechat`/`other` 等其餘類型不變。後端本來就沒有限制子帳戶類型（`_assert_valid_account_parent`,`src/snapshot_mutator.py:128-159`,只檢查 parent 必須是 `account_group`、不能自己掛自己、不能巢狀、不能循環）,`credit_card_billing.py`/`read/workspace.py::list_workspace_accounts` 的群組聚合邏輯也都是 type-agnostic(163-... 行已有註解預告「以後銀行帳戶群組也走這條路」)。**這個 Phase 純前端改動,後端不需要動**。

### 前端

- `frontend/packages/web-features/src/features/AccountsPanel.tsx`：
  - 約 1418 行:「主帳戶」下拉的顯示條件從 `form.account_type === 'credit_card'` 改成 `form.account_type === 'credit_card' || form.account_type === 'bank_card'`。
  - 約 1233-1235 行:離開 `credit_card` 時清空 `parent_account_id` 的邏輯,改成離開 `credit_card` **且**離開 `bank_card`(即新 account_type 兩者都不是)才清空。
  - 下拉標籤文字「主帳戶(合併帳單)」改成較中性的「主帳戶(群組)」,避免對純銀行群組場景使用「帳單」字眼造成誤解(小改動,風險低)。
  - `showsBilling`(額度/帳單日/繳款日欄位顯示條件,1220-1230 行)維持不變——這組欄位本來就是選填,銀行群組留空即可,不需要另外做欄位隱藏。

### 測試

- 檢查現有測試(`tests/` grep `parent_account_id`)有沒有斷言「只有 credit_card 能設 parent」——研究階段已確認沒有這條限制,理論上不需要新增後端測試,但補一條「bank_card 設定 parent_account_id 成功」的輕量 pytest 用例以鎖住這個行為(防退化)。

### 驗證

`pytest tests/ -q` → `pnpm build` → 瀏覽器手測:建一個 `account_group`,把一張銀行帳戶掛靠上去,確認帳戶列表/詳情頁的群組卡片正確聚合子帳戶餘額/收支(對齊 §2.9 2026-08-03 那次已修好的聚合邏輯)。

---

## Phase 5：背景排程管理後台

**技術方案確認**：新增資料庫設定表 + 統一輪詢排程器(而非改用 APScheduler)。這是五項裡影響面最大的一項,牽動 `src/main.py` 現有的排程迴圈架構,務必最後做、做完要跑過全量 `pytest tests/` 並手測「調整頻率」「立即執行」「停用」三種操作。

### 現況(研究結論)

`src/main.py` 目前有 4 條各自獨立的 asyncio 迴圈,共涵蓋 7 個邏輯排程動作:

| job_key | 現況所在迴圈 | 現況間隔 | 現況 sleep 順序 |
|---|---|---|---|
| `mcp_log_retention` | 303-318 行 | 24h | 先 sleep 才跑(冷啟動要等滿 24h) |
| `recurring_materializer` | 355-388 行 | 24h | 先 sleep 才跑 |
| `debt_reminders` | 409-476 行(共用同一迴圈) | 15min | 先跑才 sleep(已修過 sleep-first bug) |
| `card_due_reminders` | 同上 | 15min | 同上 |
| `transfer_rule_materialization` | 同上 | 15min | 同上 |
| `card_autopay` | 同上 | 15min | 同上 |
| `card_reward_payout` | 490-525 行 | 5min | 先跑才 sleep |

手動觸發現況:`POST /api/v1/internal/tasks/materialize-recurring`(`src/routers/internal_tasks.py`,`require_admin_user` 保護)一次觸發全部 7 個動作,無法單獨挑一個跑。

備份排程(`services/backup/scheduler.py` + `BackupSchedule` model)用的是 APScheduler + cron,是架構上最接近但**不是**這次要改的對象——維持不動。

### 後端

- `src/models.py` 新增 `ScheduledJobConfig`(比照 `BackupSchedule`,1195-1213 行的欄位風格):`job_key`(唯一)、`interval_seconds`、`enabled`、`next_run_at`、`last_run_at`、`last_run_status`、`last_run_message`、`created_at`/`updated_at`。Alembic migration 建表 + seed 上表 7 筆預設值(interval 對齊現況數字);`mcp_log_retention`/`recurring_materializer` 兩筆的 `next_run_at` seed 成「現在+間隔」(保留現況冷啟動延遲行為,避免部署當下就跑一次原本要等 24h 的清理/物化);其餘 5 筆 `next_run_at` 留空(視為立即到期,保留現況「啟動後很快跑第一次」的行為)。
- 新增 `src/services/scheduled_jobs.py`:
  - `JOB_REGISTRY: dict[str, Callable]` 把上表 7 個 job_key 對應到既有函式(`recurring_materializer.materialize_all_due`、`recurring_materializer.materialize_due_transfer_rules`、`debt_reminders.send_due_debt_reminders`、`credit_card_reminders.send_due_card_reminders`、`credit_card_autopay.materialize_due_card_autopay`、`card_reward_payout.materialize_due_card_reward_payouts`、MCP log 清理函式)——這些函式本來就是各自獨立可呼叫的,不需要改動函式本身。
  - `run_job(db, job_key) -> dict`:讀設定→呼叫對應函式(包 try/except,失敗記 `last_run_status='error'` 但不讓整個迴圈掛掉,沿用現況每個 tick 都有的錯誤隔離模式)→更新 `last_run_at`/`last_run_status`/`last_run_message`/`next_run_at = now + interval_seconds`→commit→回傳摘要。
  - `run_due_jobs(db) -> list[dict]`:掃描所有 `enabled=True` 且到期(`next_run_at is None or next_run_at <= now`)的設定,逐一呼叫 `run_job`。
- `src/main.py`:把現有 4 個 `_start_..._loop` 啟動函式**合併成一個** `_start_scheduled_jobs_loop()`:`while True: run_due_jobs(db); await asyncio.sleep(60)`(60 秒輪詢一次,先跑後 sleep,順便修掉 `mcp_log_retention`/`recurring_materializer` 現有的「先 sleep 才跑」冷啟動問題)。舊的 4 個迴圈函式與其呼叫處整段刪除。
- 新增 `src/routers/admin_scheduled_jobs.py`(仿 `src/routers/admin_backup.py` 的 CRUD + `_audit()` 寫法):
  - `GET /admin/scheduled-jobs`:列出 7 筆設定 + 目前狀態。
  - `PATCH /admin/scheduled-jobs/{job_key}`:改 `interval_seconds`/`enabled`,重算 `next_run_at`,寫 `AuditLog`。
  - `POST /admin/scheduled-jobs/{job_key}/run-now`:同步呼叫 `run_job`,HTTP 回應直接帶結果摘要(不用等下一次輪詢),寫 `AuditLog`。
  - 沿用 `require_admin_user`(+ 視既有慣例是否也要疊 `require_scopes(SCOPE_OPS_WRITE)`,對齊 `admin.py` 裡 `/logs` 端點的保護方式)。
  - `main.py` 註冊這個新 router(仿現有 `admin_backup`/`internal_tasks` 的註冊方式)。
- `POST /internal/tasks/materialize-recurring`(`src/routers/internal_tasks.py`)**維持不變、不刪除**(避免破壞既有呼叫方/測試),背後呼叫的函式跟新排程器共用同一批底層函式,兩邊不會互相干擾(這些函式本身有去重/冪等保護,詳見 CLAUDE.md §2.2/§2.5/§2.9.5.4 各自的 dedup key 設計)。

### 前端

- 新頁面 `frontend/apps/web/src/pages/sections/AdminScheduledJobsPage.tsx`(仿 `AdminDataCleanupPage.tsx`/`AdminBackupPage.tsx` 的 `useAuth()` admin 判斷 + 首次載入 fetch 樣板)。
- 路由:`App.tsx` 加 `<Route path="admin/scheduled-jobs" .../>`;`frontend/packages/web-features/src/nav.ts` 加對應 section + admin-only 導覽項(比照既有 admin 項目只出現在頭像下拉,不進主導覽);`frontend/apps/web/src/state/router.ts` 加 URL 對應。
- `frontend/packages/api-client`:新增 `fetchScheduledJobs`/`updateScheduledJob`/`runScheduledJobNow` 三個 client 方法。
- UI:表格列出 7 個排程,每列顯示中文顯示名稱(前端自建 job_key→顯示名稱對照,不需要後端多存一個欄位)、間隔(可編輯,分鐘輸入換算成秒送出)、啟用開關、上次執行時間+結果、下次預定執行時間、「立即執行」按鈕(執行中 disable + spinner,完成後 toast 顯示摘要並刷新列表)。

### 測試

- 新增 `tests/test_scheduled_jobs.py`:設定表 CRUD、`run_due_jobs` 到期判斷邏輯、`run-now` 端點即時回傳摘要、admin-only 權限、確認 7 個 job 都正確對應到既有函式且被實際呼叫(可用 mock/spy)、確認 `/internal/tasks/materialize-recurring` 舊端點行為不受影響。

### 驗證

`pytest tests/ -q` 全過(留意跟本次改動無關的既有已知 flaky 用例) → `make dev-api` + `make dev-web` → 瀏覽器手測:調整某個排程間隔、停用再啟用、按「立即執行」看到摘要與資料庫真的有反映(例如手動觸發 `card_reward_payout` 後檢查有沒有真的結算)、確認頁面只有 admin 帳號看得到。

---

## 共用注意事項(套用到每個 Phase)

- 每個 Phase 做完都要跑 `pytest tests/ -q`(全量,不只新測試)+ 有牽動前端的話跑 `pnpm build`/`pnpm test:unit`,**然後才進到瀏覽器手測**——不能只憑自動化測試過就宣告完成。
- 瀏覽器手測優先用真實點擊/表單操作觸發的路徑,不要只在 console 打 `fetch` 直接呼叫 API(會繞過前端事件總線/表單驗證等整層邏輯,測不出純前端 bug)。
- 若手測結果跟預期不符,先確認 `uvicorn --reload`/前端 dev server 是否真的重新載入了新程式碼,再懷疑邏輯本身有問題(CLAUDE.md 記錄過這台機器 reload 偶爾不生效的坑)。
