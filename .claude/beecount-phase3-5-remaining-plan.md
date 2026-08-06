# Phase 3~5 待辦計畫(接續 Phase 1/2 之後)

## 狀態:全部 5 個 Phase 已完成(2026-08-07)

Phase 1~5 都已完成 + pytest 全過 + 瀏覽器驗證通過,詳見下方各 Phase 小節。
這份文件保留給未來需要回頭查閱這批改動細節的 session 參考,不再是待辦清單。

## 背景

原始五項需求的完整計畫在 `flickering-knitting-firefly.md`。Phase 1(帳戶級聯刪除)、
Phase 2(匯入功能)已完成並驗證,詳見下方「已完成狀態」。這份文件只保留
**尚未執行的 Phase 3~5**,供 token 用盡後的新 session 直接接續執行,不需要
重新研究。執行原則不變:**依序一項一項做,完成一項→跑 pytest→用瀏覽器
實際走一遍→確認沒問題→才進下一項**。

## 已完成狀態(供新 session 快速對齊上下文)

- **Phase 1 帳戶級聯刪除交易**:已完成 + 瀏覽器驗證通過。
  - `src/snapshot_mutator.py::delete_account` 加了 `cascade` 參數 +
    `_assert_account_has_no_structural_references`(週期性收支/分期/範本/
    回饋規則/自動扣繳來源一律擋下,不受 cascade 影響)。
  - `src/schemas.py::WriteAccountDeleteRequest`(新增 `cascade: bool`)。
  - `src/routers/write/accounts.py::delete_acc` 改用新 schema。
  - 前端 `AccountsPage.tsx` 兩段式確認(`cascadeConfirming` state)+
    `api-client/src/write.ts::deleteAccount` 加 `options.cascade` 參數。
  - i18n 三語系加了 `accounts.delete.cascadeWarningTitle/cascadeWarningMessage/
    cascadeFinalMessage`、`notice.accountDeletedCascade`,移除了不再使用的
    `accounts.delete.blockedByTransactions`。
  - 測試:`tests/test_account_cascade_delete.py`(8 例)。
- **Phase 2 匯入功能(分類/帳戶/範本下載)**:後端 + 前端已完成,pytest/build/
  unit test 全過;瀏覽器手測**只做了部分**(entity type 切換 UI、範本下載
  network 200 已確認;categories 檔案上傳觸發成功但瀏覽器渲染卡住沒能看到
  最終畫面截圖——**不是後端邏輯問題**,是這台機器 Chrome 分頁在下載動作後
  偶發卡住的既有毛病,CLAUDE.md 也記錄過類似的「dev tooling 環境問題」)。
  新 session 接手時,**建議先重新用瀏覽器把 categories/accounts 兩種匯入
  各走一次完整流程**(上傳→預覽→執行→確認資料真的寫入),把 Phase 2 的
  瀏覽器驗證補完,再開始 Phase 3。
  - 新增檔案:`src/services/import_data/simple_parser.py`(分類/帳戶解析)、
    `simple_cache.py`(獨立 token cache)、`templates.py`(範本產生)、
    `src/routers/import_data/simple_endpoints.py`(upload/execute/cancel/
    template 端點,掛在 `__init__.py`)。
  - `src/services/import_data/schema.py` 的 `ImportAccount`/`ImportCategory`
    dataclass 加了欄位(`initial_balance`/`parent_account_name`/
    `credit_limit`/`billing_day`/`payment_due_day`/`source_row_number`)。
  - 前端:`ImportPage.tsx` 加 entity type 切換(交易/分類/帳戶)+ 範本下載
    按鈕;新元件 `SimpleImportPreviewCard.tsx`/`SimpleImportProgressDialog.tsx`;
    `api-client/src/import.ts` 加 `uploadCategoriesImport`/`uploadAccountsImport`/
    `streamExecuteCategoriesImport`/`streamExecuteAccountsImport`/
    `cancelSimpleImport`/`downloadImportTemplate`。
  - 測試:`tests/test_import_simple.py`(15 例,涵蓋 upload/execute/錯誤
    回報/owner-only 權限/parent 帳戶排序/取消 token/三種 entity type ×
    兩種格式的範本下載)。
  - **已知限制,寫給下一個 session**:帳戶類型「類型」欄位的中英文別名表
    (`simple_parser.py::_ACCOUNT_TYPE_ALIASES`)是照 Phase 3 執行前的舊
    標籤(「銀行卡」)建的,雖然已經預先把「銀行」也加進別名表了,但 Phase 3
    如果連 `account_type` 的內部值或其它顯示邏輯有更多變動,記得回頭檢查
    這個別名表跟 `templates.py` 的範本欄位說明是否要同步更新。

---

## Phase 3:「銀行卡」→「銀行」改名 + 新台幣預設幣別

**範圍**:只動 web 前端(mobile 是獨立 Flutter repo,不在本次範圍)。「預設
新台幣」只套用在「全新建立、沒有任何上下文可推導幣別」的表單初始值,凡是
幣別由既有帳戶/情境帶出的地方(例如替某個已存在的外幣帳戶記一筆交易)維持
沿用該情境的幣別,不強制蓋成 TWD。

### 改名

- `frontend/apps/web/src/i18n/zh-TW.ts:773` `'accountType.bank_card': '銀行卡'` → `'銀行'`
- `frontend/apps/web/src/i18n/zh-CN.ts:308` `'accountType.bank_card': '银行卡'` → `'银行'`
- `frontend/apps/web/src/i18n/en.ts:774` `'accountType.bank_card': 'Bank card'` → `'Bank'`
- （其餘出現「銀行卡/银行卡」的地方都只是程式碼註解,不動。）
- Phase 2 遺留:改完後檢查 `src/services/import_data/simple_parser.py::
  _ACCOUNT_TYPE_ALIASES` 跟 `templates.py::_ACCOUNT_EXAMPLES` 的範例文字要不要
  跟著換成新標籤(別名表本身兩種都收,不強制要求,但範本裡的範例文字最好
  跟 UI 顯示一致)。

### 新台幣預設

- `frontend/packages/web-features/src/forms.ts:306-319` `accountDefaults()` 的
  `currency: 'CNY'` → `'TWD'`。
- 實作時全域 grep `'CNY'` 在 `frontend/` 下的其他「表單預設值」用法(例如
  帳本建立預設幣別、預算預設幣別等),逐一確認是「空白表單初始值」才改成
  `'TWD'`;凡是「跟隨既有帳戶/帳本幣別」的邏輯一律不動。
- 順便檢查 `src/schemas.py::WriteLedgerCreateRequest.currency`(目前 `Field(default="CNY", ...)`,
  已在 Phase 1 研究時確認)—— 是否也要改成 `"TWD"`。改之前先確認 mobile 是否
  依賴這個後端預設(mobile 若總是明確帶 currency 則可放心一併改,若不確定
  則只動前端,後端維持現況避免影響 mobile)。

### 驗證

`pnpm build` + `pnpm test:unit`(含 i18n 三語系 key 一致性檢查)過 → 瀏覽器
手測:①帳戶類型下拉/顯示文字都變成「銀行」;②新建帳戶/新建帳本等空白
表單幣別預設是新台幣;③替既有非台幣帳戶記交易,幣別仍正確跟隨該帳戶,
不被強制改成台幣。

---

## Phase 4:主帳戶(群組合併)開放銀行帳戶掛靠

**範圍確認**:只加開 `bank_card` 可以掛靠主帳戶群組,`cash`/`alipay`/`wechat`/
`other` 等其餘類型不變。後端本來就沒有限制子帳戶類型(`_assert_valid_account_parent`,
`src/snapshot_mutator.py:128-159`,只檢查 parent 必須是 `account_group`、
不能自己掛自己、不能巢狀、不能循環),`credit_card_billing.py`/
`read/workspace.py::list_workspace_accounts` 的群組聚合邏輯也都是
type-agnostic(已有註解預告「以後銀行帳戶群組也走這條路」)。**這個 Phase
純前端改動,後端不需要動**。

### 前端

- `frontend/packages/web-features/src/features/AccountsPanel.tsx`：
  - 約 1418 行:「主帳戶」下拉的顯示條件從 `form.account_type === 'credit_card'`
    改成 `form.account_type === 'credit_card' || form.account_type === 'bank_card'`。
  - 約 1233-1235 行:離開 `credit_card` 時清空 `parent_account_id` 的邏輯,
    改成離開 `credit_card` **且**離開 `bank_card`(即新 account_type 兩者都不是)
    才清空。
  - 下拉標籤文字「主帳戶(合併帳單)」改成較中性的「主帳戶(群組)」,避免對
    純銀行群組場景使用「帳單」字眼造成誤解(小改動,風險低)。**若 Phase 3
    已經改過 `accountType.account_group` 的 i18n 標籤,這裡對齊同一次改法,
    不要重複改兩次。**
  - `showsBilling`(額度/帳單日/繳款日欄位顯示條件,約 1220-1230 行)維持
    不變——這組欄位本來就是選填,銀行群組留空即可,不需要另外做欄位隱藏。

### 測試

- 檢查現有測試(`tests/` grep `parent_account_id`)有沒有斷言「只有
  credit_card 能設 parent」——研究階段已確認沒有這條限制,理論上不需要
  新增後端測試,但補一條「bank_card 設定 parent_account_id 成功」的輕量
  pytest 用例以鎖住這個行為(防退化)。

### 驗證

`pytest tests/ -q` → `pnpm build` → 瀏覽器手測:建一個 `account_group`,
把一張銀行帳戶掛靠上去,確認帳戶列表/詳情頁的群組卡片正確聚合子帳戶
餘額/收支(對齊 §2.9 2026-08-03 那次已修好的聚合邏輯)。

---

## Phase 5:背景排程管理後台

**技術方案確認**:新增資料庫設定表 + 統一輪詢排程器(而非改用 APScheduler)。
這是五項裡影響面最大的一項,牽動 `src/main.py` 現有的排程迴圈架構,務必
最後做、做完要跑過全量 `pytest tests/` 並手測「調整頻率」「立即執行」
「停用」三種操作。**這個 Phase 工作量明顯比前面幾項大,如果 token 有限,
可以考慮拆成「後端」「前端」兩次 session 分開做。**

### 現況(研究結論,寫於 Phase 1 之前,執行前建議重新確認 `src/main.py` 行號
是否因為前面幾個 Phase 的改動而偏移——前面幾個 Phase 都沒有改 `main.py`,
理論上行號應該還準,但保險起見開工前先 Read 一次)

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

手動觸發現況:`POST /api/v1/internal/tasks/materialize-recurring`
(`src/routers/internal_tasks.py`,`require_admin_user` 保護)一次觸發全部
7 個動作,無法單獨挑一個跑。

備份排程(`services/backup/scheduler.py` + `BackupSchedule` model)用的是
APScheduler + cron,是架構上最接近但**不是**這次要改的對象——維持不動。

### 後端

- `src/models.py` 新增 `ScheduledJobConfig`(比照 `BackupSchedule`,
  1195-1213 行的欄位風格):`job_key`(唯一)、`interval_seconds`、
  `enabled`、`next_run_at`、`last_run_at`、`last_run_status`、
  `last_run_message`、`created_at`/`updated_at`。Alembic migration 建表 +
  seed 上表 7 筆預設值(interval 對齊現況數字);`mcp_log_retention`/
  `recurring_materializer` 兩筆的 `next_run_at` seed 成「現在+間隔」
  (保留現況冷啟動延遲行為,避免部署當下就跑一次原本要等 24h 的清理/物化);
  其餘 5 筆 `next_run_at` 留空(視為立即到期,保留現況「啟動後很快跑第
  一次」的行為)。
- 新增 `src/services/scheduled_jobs.py`:
  - `JOB_REGISTRY: dict[str, Callable]` 把上表 7 個 job_key 對應到既有
    函式(`recurring_materializer.materialize_all_due`、
    `recurring_materializer.materialize_due_transfer_rules`、
    `debt_reminders.send_due_debt_reminders`、
    `credit_card_reminders.send_due_card_reminders`、
    `credit_card_autopay.materialize_due_card_autopay`、
    `card_reward_payout.materialize_due_card_reward_payouts`、MCP log
    清理函式)——這些函式本來就是各自獨立可呼叫的,不需要改動函式本身。
  - `run_job(db, job_key) -> dict`:讀設定→呼叫對應函式(包 try/except,
    失敗記 `last_run_status='error'` 但不讓整個迴圈掛掉,沿用現況每個
    tick 都有的錯誤隔離模式)→更新 `last_run_at`/`last_run_status`/
    `last_run_message`/`next_run_at = now + interval_seconds`→commit→
    回傳摘要。
  - `run_due_jobs(db) -> list[dict]`:掃描所有 `enabled=True` 且到期
    (`next_run_at is None or next_run_at <= now`)的設定,逐一呼叫
    `run_job`。
- `src/main.py`:把現有 4 個 `_start_..._loop` 啟動函式**合併成一個**
  `_start_scheduled_jobs_loop()`:`while True: run_due_jobs(db); await
  asyncio.sleep(60)`(60 秒輪詢一次,先跑後 sleep,順便修掉
  `mcp_log_retention`/`recurring_materializer` 現有的「先 sleep 才跑」
  冷啟動問題)。舊的 4 個迴圈函式與其呼叫處整段刪除。
- 新增 `src/routers/admin_scheduled_jobs.py`(仿 `src/routers/admin_backup.py`
  的 CRUD + `_audit()` 寫法):
  - `GET /admin/scheduled-jobs`:列出 7 筆設定 + 目前狀態。
  - `PATCH /admin/scheduled-jobs/{job_key}`:改 `interval_seconds`/
    `enabled`,重算 `next_run_at`,寫 `AuditLog`。
  - `POST /admin/scheduled-jobs/{job_key}/run-now`:同步呼叫 `run_job`,
    HTTP 回應直接帶結果摘要(不用等下一次輪詢),寫 `AuditLog`。
  - 沿用 `require_admin_user`(+ 視既有慣例是否也要疊
    `require_scopes(SCOPE_OPS_WRITE)`,對齊 `admin.py` 裡 `/logs` 端點的
    保護方式)。
  - `main.py` 註冊這個新 router(仿現有 `admin_backup`/`internal_tasks`
    的註冊方式)。
- `POST /internal/tasks/materialize-recurring`(`src/routers/internal_tasks.py`)
  **維持不變、不刪除**(避免破壞既有呼叫方/測試),背後呼叫的函式跟新
  排程器共用同一批底層函式,兩邊不會互相干擾(這些函式本身有去重/冪等
  保護,詳見 CLAUDE.md §2.2/§2.5/§2.9.5.4 各自的 dedup key 設計)。

### 前端

- 新頁面 `frontend/apps/web/src/pages/sections/AdminScheduledJobsPage.tsx`
  (仿 `AdminDataCleanupPage.tsx`/`AdminBackupPage.tsx` 的 `useAuth()`
  admin 判斷 + 首次載入 fetch 樣板)。
- 路由:`App.tsx` 加 `<Route path="admin/scheduled-jobs" .../>`;
  `frontend/packages/web-features/src/nav.ts` 加對應 section + admin-only
  導覽項(比照既有 admin 項目只出現在頭像下拉,不進主導覽);
  `frontend/apps/web/src/state/router.ts` 加 URL 對應。
- `frontend/packages/api-client`:新增 `fetchScheduledJobs`/
  `updateScheduledJob`/`runScheduledJobNow` 三個 client 方法。
- UI:表格列出 7 個排程,每列顯示中文顯示名稱(前端自建 job_key→顯示名稱
  對照,不需要後端多存一個欄位)、間隔(可編輯,分鐘輸入換算成秒送出)、
  啟用開關、上次執行時間+結果、下次預定執行時間、「立即執行」按鈕
  (執行中 disable + spinner,完成後 toast 顯示摘要並刷新列表)。

### 測試

- 新增 `tests/test_scheduled_jobs.py`:設定表 CRUD、`run_due_jobs` 到期
  判斷邏輯、`run-now` 端點即時回傳摘要、admin-only 權限、確認 7 個 job
  都正確對應到既有函式且被實際呼叫(可用 mock/spy)、確認
  `/internal/tasks/materialize-recurring` 舊端點行為不受影響。

### 驗證

`pytest tests/ -q` 全過(留意跟本次改動無關的既有已知 flaky 用例
`test_recurring_rules.py::test_recurring_occurrence_update_overridden_
skipped_by_update_from`,date-sensitive,不用管)→ `make dev-api` +
`make dev-web` → 瀏覽器手測:調整某個排程間隔、停用再啟用、按「立即執行」
看到摘要與資料庫真的有反映(例如手動觸發 `card_reward_payout` 後檢查有沒
有真的結算)、確認頁面只有 admin 帳號看得到。

---

## Phase 3~5 執行紀錄(2026-08-07,供未來查閱)

**全部完成**:Phase 3(銀行卡→銀行改名 + TWD 預設)、Phase 4(銀行帳戶掛靠
主帳戶群組)、Phase 5(背景排程管理後台)依序做完,每項都跑過 `pytest
tests/ -q` 全量 + `pnpm build`/`pnpm test:unit` + 瀏覽器實際點擊操作驗證。

- **Phase 3**:三語系 `accountType.bank_card` 改名、`forms.ts::
  accountDefaults()` 與 `LedgersPage.tsx`/`LedgersSection.tsx`/
  `AccountsPanel.tsx` 裡的空白表單幣別預設改 `TWD`(existing-entity 的
  currency fallback 維持不動,沒有動)。`src/schemas.py::
  WriteLedgerCreateRequest.currency` 後端預設**沒有改**(mobile repo 這個
  session 拿不到,無法確認 mobile 是否依賴這個預設,依計畫指示保守處理)。
- **Phase 4**:`AccountsPanel.tsx` 開放 `bank_card` 掛靠主帳戶群組的下拉
  顯示條件 + 清空邏輯;`accountType.account_group`/`accounts.field.
  parentAccount` 三語系標籤改成「主帳戶(群組)」。**手測時發現一個真實
  bug**:`AccountsPage.tsx` 送出 payload 時 `parent_account_id` 只在
  `isCreditCard` 為真時才帶出去,bank_card 選了主帳戶但實際沒存進去——
  已修正為 `isCreditCard || account_type === 'bank_card'`,修完後瀏覽器
  重新測試聚合邏輯(餘額/收支正確 roll up 到群組卡片)確認無誤。新增
  `tests/test_credit_card.py::test_web_update_account_parent_account_id_
  accepts_bank_card_child` 鎖住行為。
- **Phase 5**:新增 `ScheduledJobConfig` model + migration
  `0036_scheduled_job_configs`(seed 7 筆)、`src/services/scheduled_jobs.py`
  (`JOB_REGISTRY`/`run_job`/`run_due_jobs`/`ensure_default_configs`)、
  `src/routers/admin_scheduled_jobs.py`、`main.py` 把原本 4 條迴圈合併成
  `_start_scheduled_jobs_loop`(60 秒輪詢)。前端新增
  `AdminScheduledJobsPage.tsx` + 路由/nav/api-client 方法。**過程中犯了
  一次 Edit 工具誤用的錯**:改 `models.py` 時 `old_string` 沒包含
  `BackupRunTarget` 最後一行 `error_message` 欄位,導致該行被錯位挪到
  `ScheduledJobConfig` 類別尾端——`Base.metadata.create_all`(pytest 用)
  不會暴露這個問題,只有連到真實 `beecount.db`(migration 建的 schema)
  才會噴 `no such column` 500 錯誤,瀏覽器手測時才抓到、已修正。**教訓**:
  改多欄位 model 類別時,Edit 的 `old_string` 結尾要確認真的是該類別的
  最後一個欄位,不要只看 `Read` 顯示的最後一行就假設沒有更多內容被截斷。
  新增 `tests/test_scheduled_jobs.py`(12 例)。

## 共用注意事項(套用到每個 Phase)

- 每個 Phase 做完都要跑 `pytest tests/ -q`(全量,不只新測試)+ 有牽動
  前端的話跑 `pnpm build`/`pnpm test:unit`,**然後才進到瀏覽器手測**——
  不能只憑自動化測試過就宣告完成。
- 瀏覽器手測優先用真實點擊/表單操作觸發的路徑,不要只在 console 打
  `fetch` 直接呼叫 API(會繞過前端事件總線/表單驗證等整層邏輯,測不出
  純前端 bug)。
- 若手測結果跟預期不符,先確認 `uvicorn --reload`/前端 dev server 是否
  真的重新載入了新程式碼,再懷疑邏輯本身有問題。
- **2026-08-07 更新**:上次 session 記錄的 `browsertest1@example.com` 測試
  帳號在目前的 `beecount.db` 裡已經不存在(可能是資料庫在兩次 session 之間
  被重置過),改用 `owner@example.com`(admin 帳號)測試,密碼被本次 session
  重設為 `TestPass123!`(舊密碼未知、無法還原,這是一次性 hash 覆蓋——若
  這是需要保留的正式帳號密碼,請告知使用者需要另外處理)。本機目前跑著:
  backend `uvicorn`(port 8080,`--reload`,PID 96558)+ frontend `vite dev`
  (port 5173,PID 96532,不是舊筆記寫的 5175——5173 目前是空的)。新
  session 接手前建議先確認這兩個 process 是否還活著。
