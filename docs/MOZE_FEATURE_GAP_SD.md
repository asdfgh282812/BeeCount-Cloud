# Moze 功能對標 SD（差距分析與 server 端修改規格）

本文件盤點 [Moze 教學文件](https://doc.moze.app/)(索引見
[llms.txt](https://doc.moze.app/llms.txt))列出的功能項目，逐一對照
BeeCount Cloud 目前的實作範圍，列出**缺少的功能**、**建議的 server 端
修改內容**，以及跨端（mobile/web）需要配合的部分。

**範圍界定**：本倉庫是 server 端(FastAPI + SQLite/Postgres)。凡是純
iOS 系統整合類功能(Shortcuts App、Lock Screen Widgets、Apple Watch)
不需要 server 改動，只在文件末尾列出、標記「client-only」。真正需要
server 資料模型/API 變動的項目才會展開「修改內容」小節。

改動前請先讀 [SYNC_ARCHITECTURE.md](./SYNC_ARCHITECTURE.md)（新增 entity
的標準流程、LWW 契約、`_MERGE_SPECS` 登記法都在那）。下面每個新 entity
的「修改內容」都是照那份文件第 5 節「新增 entity」checklist 展開的。

---

## 1. 現況總覽表

對照 Moze 文件的 8 大分類，BeeCount 現況：

| Moze 分類 | 現況 |
|---|---|
| 帳戶設置 | ✅ 已支援(`accounts`：type/currency/初始餘額/note/信用額度/帳單日/繳款日/末四碼/隱藏) |
| 分類與專案 — 分類 | ✅ 已支援(`categories`：expense/income/transfer，含父子階層) |
| 分類與專案 — 專案/預算 | 🟡 部分(有 `budgets` 總額/分類預算，**沒有獨立「專案」概念**——§2.11 記帳模式依賴這個缺口，需要先決定要不要做真正的 project entity) |
| 記帳功能 | 🟡 部分(基本欄位、跨幣種、圖片/文字 AI 記帳、**週期性/分期/退款(Phase 1，server + web UI 已完成，mobile UI 待排期)**已有；拆帳/範本/語音記帳/記帳模式缺) |
| 信用卡管理 | 🟡 部分(帳戶已有信用卡欄位，**繳款/分期(分期本身已做通用版)/折抵/紅利/免息期建議全缺**) |
| 分析與對帳 | 🟡 部分(統計報表、淨值歷史已有；**比較報表、對帳模式(含延後入帳)、餘額調整缺**) |
| 同步與雲端 | ✅ 已支援(本倉庫就是這塊：登入/2FA/裝置管理/離線佇列/刪除帳號) |
| 捷徑功能 | ⚪ Client-only，server 不需改動 |
| 其他功能 | 🟡 部分(備份還原、搜尋、批次改刪、匯入匯出、**通知中心(Phase 0)**已有；台灣電子發票視市場決定要不要做) |

---

## 2. Gap 詳細清單（依建議優先順序）

### 2.1【基礎設施，建議最先做】通知中心 (Notification Center)

Moze: [feature/notification.md](https://doc.moze.app/feature/notification.md)

**現況**：`src/` 完全沒有 notification/reminder 相關 model 或 router。
後面「提醒入帳」「信用卡自動扣繳提醒」「預算超支提醒」都要靠這層，
建議第一個做，其它功能才有地方掛。

**修改內容**：
1. 新表 `notifications`（user-global，非 ledger-scoped）：
   `id, user_id, category(reminder/budget_alert/card_due/system), title,
   body, payload_json, read_at, created_at`
2. `alembic/versions/` 加 migration
3. `src/routers/notifications.py`（仿 `admin.py` 的獨立 router 風格，
   非 sync 實體，不進 `sync_changes`/projection，走一般 REST：
   `GET /notifications`、`POST /notifications/{id}/read`）
4. 產生通知的來源分散在各功能裡（budget 超支判斷、recurring 到期、
   信用卡繳款日前 N 天），不集中成一個 job，避免耦合
5. 是否要接 Web Push / APNs 由 mobile/web 端決定，server 端這層只負責
   落地一份「通知記錄」，跨端各自 poll 或收 WS 推播

---

### 2.2 週期性收支 (Recurring Transactions)

**✅ Phase 1 已實作(2026-07-30）**：`read_recurring_rule_projection` +
`src/routers/write/recurring_rules.py`(POST/PATCH/DELETE)+
`src/routers/read/ledgers.py` 的 `GET /ledgers/{id}/recurring-rules` +
`src/services/recurring_materializer.py`（asyncio 定時 loop,15 分鐘一次,
main.py 註冊；也可手動 `POST /internal/tasks/materialize-recurring` 立即
觸發）。到期生成交易時順帶寫 §2.1 notification。測試見
`tests/test_recurring_rules.py`。**web UI 已完成(2026-07-30）**：
`/app/recurring-rules`(`RecurringRulesPage` + `RecurringRulesPanel`），入口
在頭像下拉「工具」組，僅帳本 owner 可寫。mobile UI 仍待排期。

Moze: [record/recurring.md](https://doc.moze.app/record/recurring.md)

**現況**：完全沒有。`WriteTransactionCreateRequest` 只能建立單筆立即
發生的交易。

**修改內容**（照「新增 entity」六步）：
1. 新表 `read_recurring_rule_projection`：`sync_id, ledger_id, tx_type,
   amount, category_sync_id, account_sync_id, from/to_account_sync_id,
   note, frequency(daily/weekly/monthly/yearly), interval, next_run_at,
   end_at, enabled` + alembic migration
2. `src/projection.py` 加 `upsert_recurring_rule` / `delete_recurring_rule`
3. `src/sync_applier.py` 的 `_MERGE_SPECS` / `_UPSERT_DISPATCH` /
   `_DELETE_DISPATCH` 三張表登記 `recurring_rule`
4. `src/routers/write/recurring_rules.py`：POST/PATCH/DELETE
5. **排程執行**：新增 `src/services/recurring_materializer.py`，一個
   定時任務（複用現有 cron/背景任務機制，若無則需新增，比如 APScheduler
   或外部 cron 打一個 `/internal/tasks/materialize-recurring` 端點）掃
   `next_run_at <= now()` 的規則，各自產生一筆真正的 `sync_changes`
   交易（走跟 mobile push 相同的 `apply_change_to_projection`），並把
   `next_run_at` 推進到下一週期
6. 到期時順帶寫一條 §2.1 的 notification（"提醒入帳"對應的就是這裡）
7. pytest：一個 rule 到期 → 產生交易 → projection 可見；`next_run_at`
   正確推進；停用後不再產生

跨端：mobile/web 都要能建立/檢視/停用規則，並顯示「即將產生」清單。

---

### 2.3 分期付款 (Installment)

**✅ Phase 1 已實作(2026-07-30）**：`read_installment_plan_projection` +
`read_tx_projection.installment_plan_sync_id` 反查欄位 +
`src/routers/write/installment_plans.py`(POST 建計畫時同事務生成第一期
交易 / PATCH 可提前結清 / DELETE)+ `GET /ledgers/{id}/installment-plans`。
剩餘各期由 `src/services/recurring_materializer.py` 共用同一個排程 worker
按月推進(`add_months` 假設每期間隔一個月，跟信用卡帳單週期常見場景對齊；
未做免息期/折抵等信用卡專屬計算，那些排在 §2.9)。測試見
`tests/test_installment_plans.py`。**web UI 已完成(2026-07-30）**：
`/app/installment-plans`(`InstallmentPlansPage` + `InstallmentPlansPanel`），
建計畫後 total_amount/periods/first_period_at 不可改(對齊 server 約束),
只能提前結清或改備註。mobile UI 仍待排期。

Moze: [record/installment.md](https://doc.moze.app/record/installment.md)、
信用卡的「[帳單分期](https://doc.moze.app/credit-card/statement-installment.md)」
是同一機制在信用卡場景的特化。

**現況**：完全沒有。一筆交易目前是原子的，沒有「拆成 N 期分別記一筆」
的概念。

**修改內容**：
1. 新表 `read_installment_plan_projection`：`sync_id, ledger_id,
   total_amount, periods, period_amount, first_period_at,
   account_sync_id(通常是信用卡), category_sync_id, note, status`
2. 子項：每期到期時實際生成一筆 `transactions`（`tx_type=expense`），
   並帶 `installment_plan_sync_id` 反查欄位 —— 這代表 `read_tx_projection`
   要加一個 nullable 欄位 `installment_plan_sync_id`（跟 §2.5 拆帳/退款
   的「反查欄位」模式一致，見下面 2.4/2.6 的共通設計筆記）
3. 產生時機：可以跟 §2.2 共用同一個排程 worker（installment 本質是
   固定期數的 recurring）
4. `src/routers/write/installment_plans.py`：POST(建立計畫，通常伴隨
   建立第一期交易)/PATCH(提前結清/修改期數)/DELETE
5. 信用卡場景的「免息期」計算(§2.9)要讀這裡的 `first_period_at` +
   account 的 `billing_day`/`payment_due_day`

---

### 2.4 拆帳 (Split a Transaction Across Multiple Categories)

Moze: [record/split-categories.md](https://doc.moze.app/record/split-categories.md)

**現況**：`ReadTxProjection` / `WriteTransactionCreateRequest` 都是
一筆交易對一個 `category_id`，沒有「一筆 200 元拆成餐飲 150 + 交通 50」
的資料結構。

**修改內容**（這個影響面最大，動到核心 tx 契約，建議獨立一個 PR）：
1. `WriteTransactionCreateRequest` / `Update` 新增可選欄位
   `splits: list[{category_id, amount, note}] | None`；不傳 → 維持現行
   單一 category 行為（向下相容）
2. 新表 `read_tx_split_projection`：`tx_sync_id(FK), category_sync_id,
   amount, note, sort_order`，一對多掛在父交易上
3. `src/sync_applier.py` upsert 交易時，若 payload 帶 `splits`，額外
   寫入/覆蓋 `read_tx_split_projection` 的對應行(先 delete 該
   tx_sync_id 下所有舊 split 行再重新插入，避免 diff 複雜度)
4. **統計報表(§2.10)要改**：目前 `workspace_analytics` 按
   `ReadTxProjection.category_sync_id` 分組，拆帳交易要改成先展開
   split 行再分組，否則會把整筆金額全部歸到父交易的(此時應為
   null 或 "混合")category 上
5. mobile 端既有的本地 SQLite schema 也要加對應子表，這塊要跟
   `../BeeCount/CLAUDE.md` 的 mobile 契約一起改，不是 server 單邊能完成

---

### 2.5 借還款追蹤 (Payables / Receivables)

Moze: [record/payables-receivables.md](https://doc.moze.app/record/payables-receivables.md)

**現況**：有 `transfer` tx_type(帳戶互轉)，但沒有「欠某人 / 某人欠我」
這種跟**第三方(非自己帳戶)**的往來追蹤，也沒有「部分還款」狀態機。

**修改內容**：
1. 新表 `read_debt_projection`：`sync_id, ledger_id, direction
   (payable/receivable), counterparty_name, principal_amount,
   remaining_amount, due_at, status(open/partial/settled), note`
2. 每次還款/收款是一筆交易，帶反查欄位 `debt_sync_id`（同 §2.3/2.4
   的「反查欄位」模式），寫入時同步更新對應 debt 行的
   `remaining_amount`/`status`（跟 rename cascade 類似，需要在
   `apply_change_to_projection` 裡加一段 debt 餘額重算邏輯）
3. `src/routers/write/debts.py`：POST(建立欠款)/PATCH(改到期日/備註)/
   DELETE(僅允許 `remaining_amount == principal_amount` 時，即尚未還款)
4. 到期前提醒走 §2.1 notification

---

### 2.6 退款 (Refund)

**✅ Phase 1 已實作(2026-07-30）**：`read_tx_projection.refund_of_sync_id` +
`WriteTransactionCreateRequest/UpdateRequest.refund_of_id`。統計口徑淨額
在三處生效：`read/_shared.py::_projection_totals`(`/summary` 與
`list_ledgers` 共用)以及 `read/workspace.py::workspace_analytics`(含
income/expense 總額、series、分類排行）。`balance` 口徑不受影響(退款仍按
income 記正號，數學上等價)。測試見 `tests/test_refund_stats.py`。**web UI
已完成(2026-07-30）**：交易表單新增「退款對象」下拉(僅 income 類型顯示，
`TransactionsPanel.tsx`)，候選來自當前已載入的交易列表(非全量搜尋，已知
限制)；交易詳情彈窗顯示「退款」徽章。**尚未做**的:CSV 匯出欄位、跨月退款
回溯到原支出月份的口徑(目前退款淨額算在退款自己發生的那個月/分類，不會
回溯修正原支出那個月)、全量交易搜尋 picker(mobile UI 也待排期)。

Moze: [record/refund.md](https://doc.moze.app/record/refund.md)

**現況**：沒有「這筆收入是對某筆支出的退款」的關聯，統計上退款會被
當成一筆普通收入，拉高當期收入而不是沖銷原支出。

**修改內容**（比前面幾項輕量）：
1. `ReadTxProjection` 加 nullable 欄位 `refund_of_sync_id`
2. `WriteTransactionCreateRequest` 加 `refund_of_id: str | None`
3. `src/routers/read/workspace.py` 的統計聚合：退款交易預設從「當期
   收入」裡挪走，改成沖抵 `refund_of_sync_id` 指向那筆支出的淨額
   （這是統計口徑變動，需要在 `workspace_analytics` / `summary.py`
   兩處都改，並補測試鎖住新舊兩種口徑的預期輸出）
4. 不需要新表，複用 `read_tx_projection` 加欄位即可，遷移成本低

---

### 2.7 範本 (Templates) / 範本記帳

Moze: [record/template.md](https://doc.moze.app/record/template.md)、
[shortcuts/template-entry.md](https://doc.moze.app/shortcuts/template-entry.md)

**現況**：沒有「存一組常用的 tx_type+amount+category+account 組合，
下次一鍵套用」的機制。

**修改內容**：
1. 新表 `read_tx_template_projection`：`sync_id, ledger_id, name,
   tx_type, amount, category_sync_id, account_sync_id, note, tag_sync_ids_json,
   sort_order`
2. `src/routers/write/tx_templates.py`：POST/PATCH/DELETE + 一個
   `POST /ledgers/{id}/tx-templates/{template_id}/apply` 端點，直接把
   範本內容套進一筆新交易(複用現有 `_commit_write` 交易建立邏輯)
3. 是新 entity 但寫入頻率低、無跨帳本共享/rename cascade 需求，是六步
   checklist 裡最簡單的一種

---

### 2.8 語音記帳 (Speech Entry)

Moze: [record/speech-entry.md](https://doc.moze.app/record/speech-entry.md)、
[shortcuts/speech-entry.md](https://doc.moze.app/shortcuts/speech-entry.md)

**現況**：`src/routers/ai/parse_tx_image.py` / `parse_tx_text.py` 已有
「圖片/文字 → 結構化交易」的 AI 解析管線，語音只差「語音轉文字」這一步
沒接。

**修改內容**（是現有管線的擴充，不是新概念）：
1. `src/routers/ai/parse_tx_speech.py`：接收音檔(或 mobile 端先轉好的
   文字，看要不要 server 側跑 STT)，複用 `parse_tx_text.py` 現有的
   「文字 → 交易 JSON」prompt/parsing 邏輯
2. 若 server 端要跑語音轉文字，需在 `src/services/ai/` 新增 provider
   (對照 `test_provider.py` 現有的 provider 測試模式)
3. 大部分工程量在 mobile 端(錄音 UI + 上傳)，server 端改動相對小

---

### 2.9 信用卡管理整組功能

Moze 分類：[credit-card/*](https://doc.moze.app/credit-card/statement-combined.md)

**現況**：`accounts` 表已經有 `credit_limit`/`billing_day`/
`payment_due_day`/`bank_name`/`card_last_four`，但這些欄位目前只是
「存起來顯示」，沒有任何計算或工作流程邏輯掛在上面。

逐項：

| 子功能 | 現況 | 修改內容 |
|---|---|---|
| 主帳戶(合併帳單) | 缺 | 帳戶需要一個 `parent_account_id`(自我參照)把附卡/子卡掛在主卡下；讀路徑合併計算帳單 |
| 信用卡繳款 | 部分(可用 transfer 手動記) | 加一個「繳款」語意化端點 `POST /accounts/{id}/card-payment`，本質是產生一筆 transfer，但額外做「衝抵當期應繳金額」的計算與標記 |
| 自動扣繳 | 缺 | 依賴 §2.2 recurring 機制：一條 `frequency=monthly` 的 recurring rule，`next_run_at` 算法要對齊 `payment_due_day` |
| 帳單分期 | 缺 | 就是 §2.3 installment，`account_sync_id` 指向信用卡即可，不需要獨立資料結構 |
| 帳單折抵 | 缺 | 需要「折抵券/回饋金餘額」概念，見下一行紅利回饋，折抵是紅利餘額的一種消費 |
| 免息期推薦 | 缺，但資料都在(billing_day/payment_due_day) | 純計算端點，不需新表：`GET /accounts/{id}/interest-free-suggestion`，用 `billing_day`+`payment_due_day` 算「這個月哪天前買，下次繳款日最遠」 |
| 紅利回饋 | 缺 | 新表 `read_card_rewards_projection`：`account_sync_id, balance, rule_note`；每筆信用卡交易產生時按規則(可先手動輸入回饋比例)累加，這塊業務規則因發卡行而異，建議先做「手動記錄回饋金額」的簡化版，不做自動比例計算 |

信用卡這組建議整體排在 §2.2~§2.7 之後，因為「免息期建議」「自動扣繳」
都依賴 recurring/installment 先落地。

---

### 2.10 分析與對帳（含「延後入帳」——必做）

| 子功能 | 現況 | 修改內容 |
|---|---|---|
| 統計報表 | ✅ `workspace_analytics` / `summary.py` 已覆蓋大部分 | 若要對齊 Moze 的[statistics-report.md](https://doc.moze.app/analysis/statistics-report.md) 細節維度(如按 tag 交叉分析)，屬於既有端點加參數，不需新架構 |
| 比較報表 | 🟡 只有 `net-worth-history`，沒有「本月 vs 上月/去年同期」結構化比較 | 新端點 `GET /workspace/comparison?period=month&offset=1`，複用 `workspace_analytics` 的聚合邏輯跑兩個區間再算 diff，不需新表 |
| 對帳模式 | 缺 | 新表 `read_reconciliation_projection`：`account_sync_id, statement_date, statement_balance, reconciled_at`；核心邏輯是比對 `statement_balance` vs 該帳戶截至該日的交易加總，差額提示使用者去補交易或走下一項「餘額調整」 |
| **延後入帳** | 缺 | 見下方獨立小節 —— 這是對帳模式能不能對得準的關鍵前提，**必做**，不是可選項 |
| 餘額調整 | 缺 | 交易層面加一個 `tx_type=adjustment`(目前 Literal 只有 expense/income/transfer)，語意是「直接把帳戶餘額修正到指定值」，差額系統自動算出寫成一筆特殊交易；`snapshot_mutator.py` 幾處 `tx_type not in {"expense","income","transfer"}` 的白名單檢查都要加上 `adjustment` |

#### 延後入帳 (Deferred Posting) —— 對帳模式的必要子功能

Moze: [record/postpone.md](https://doc.moze.app/record/postpone.md)

**這不是 client-only 的草稿狀態**，重新讀了原文後更正：這是在**信用卡
對帳流程**中使用的功能 —— 信用卡消費日跟銀行實際請款日常常有時間差
（店家批次彙整消費後才跟銀行請款），對帳時如果某筆交易還沒出現在當期
帳單，使用者就把該筆交易標記「延後入帳」並填入實際入帳日，系統對帳時
「自動略過尚未入帳的交易」，避免帳單對不上。這個標記跟日期**必須跟交易
一起同步到其他裝置**(不然在別的裝置對帳會看到不一致的結果)，所以是
`read_tx_projection` 的正式欄位，不是本地暫存。因此**移出「client-only」
分類，列為對帳模式的必做前置項**：

**修改內容**：
1. `ReadTxProjection` 加 nullable 欄位 `deferred_posting_at: datetime`
   （有值 = 該筆交易處於「延後入帳」狀態，值是使用者填的實際入帳日；
   `None` = 正常，維持現行行為）
2. `WriteTransactionCreateRequest` / `Update` 加對應欄位
   `deferred_posting_at: datetime | None`
3. §2.10「對帳模式」比對邏輯：計算某帳戶「這期帳單應含哪些交易」時，
   排序/篩選鍵改用 `COALESCE(deferred_posting_at, happened_at)`，
   而不是單純 `happened_at`；也就是延後入帳的交易會被歸到
   `deferred_posting_at` 那一期帳單，而不是原本記錄的那一期
4. 統計報表(§2.10 統計報表列、`workspace_analytics`)若也要看「入帳日」
   口徑(而非「消費日」口徑)，同一個 `COALESCE` 邏輯要複用，避免兩處
   算法各寫一套导致對不上
5. §2.9 信用卡「主帳戶(合併帳單)」的帳單彙總也要套用同一個
   `COALESCE` 規則，三處(對帳/統計/信用卡帳單彙總)共用同一個 helper
   函式，不要各自實作一份

---

### 2.11 記帳模式 (Entry Mode Profiles)

Moze: [record/entry-mode.md](https://doc.moze.app/record/entry-mode.md)

**重新讀過原文後更正**：這不是單純的 UI 呈現方式，而是「使用者依情境
(日常/旅遊/工作/娛樂…)自訂一組**預設值 + 過濾規則**，記帳時套用」的
功能，具體是：

- 建立模式時可指定：預設幣種、預設帳戶、預設專案、預設類別範圍
- 套用某個模式後，記帳表單的類別/帳戶/專案頁籤會**過濾掉跟這個情境
  無關的選項**（例如「旅遊模式」只顯示旅遊相關類別跟外幣帳戶）
- 首頁長按＋號可快速切換目前使用的模式
- 這些「模式定義」是使用者設定，**必須跨裝置一致**(不然手機建的旅遊
  模式，換平板記帳就看不到)，所以定義本身要跟其它 entity 一樣走同步，
  不是單裝置本地設定

**現況**：完全沒有這個概念，也沒有「模式」對應的 entity。

**重要依賴**：Moze 的模式定義裡包含「預設專案」，但 BeeCount 目前
**沒有獨立的「專案」entity**（見 §1 總覽表，`budgets` 只有
total/category 兩種類型，沒有 project 類型）。要嘛先做一個真正的
`project` entity(範圍比本文件其它項目大，等於要再過一次 Moze
[prepare/project.md](https://doc.moze.app/prepare/project.md) 那組
文件才能定案)，要嘛 v1 先用現有的 `tags` 頂替「專案」語意(風險：
tag 目前是多對多、無層級、無預算掛勾，跟 Moze 的「專案」語意不完全
等價，只能算暫代方案)。這個依賴沒解掉之前，模式定義只能先做
幣種/帳戶/類別三項過濾，專案過濾留空。

**修改內容**：
1. 新表 `read_entry_mode_profile_projection`：`sync_id, ledger_id, name,
   default_currency, default_account_sync_id, default_project_id(待
   project entity 定案前先留空/null), category_filter_json(允許的
   category_sync_id 陣列，空陣列=不過濾), sort_order`
2. `src/projection.py` 加 `upsert_entry_mode_profile` /
   `delete_entry_mode_profile`
3. `src/sync_applier.py` 三張表(`_MERGE_SPECS`/`_UPSERT_DISPATCH`/
   `_DELETE_DISPATCH`)登記 `entry_mode_profile`
4. `src/routers/write/entry_mode_profiles.py`：POST/PATCH/DELETE
5. 「目前使用中的模式」這個選取狀態**不需要 server 儲存**(純 UI 狀態，
   可以是本地 per-device，也可以是簡單塞進 §... 現有的
   `UserProfile`/`Device` 表加一個 `active_entry_mode_sync_id` 欄位，
   看 mobile/web 需不需要跨裝置記住上次選的模式再決定要不要做這步)
6. 這個功能大部分工作量在 mobile/web 的記帳表單過濾邏輯(跟
   `../BeeCount/CLAUDE.md` 的 mobile 契約一起排期)，server 端只負責
   把模式定義存好、同步好

---

## 3. Client-only，本倉庫不需要動的項目

以下是 Moze 文件裡的功能，但屬於 iOS 系統整合層，跟 server 端資料模型
無關，若要做也是在 `../BeeCount` (mobile) 那個倉庫：

- [捷徑功能整組](https://doc.moze.app/shortcuts/download.md)(iOS Shortcuts App)
  —— 概念上最接近的是本倉庫已有的 `src/mcp/`（給 AI agent 呼叫的工具
  介面），若要支援 iOS Shortcuts，等於是幫 mobile 端暴露幾個輕量寫入
  端點，端點本身多半已存在(`/write/ledgers/{id}/transactions` 等)
- [Lock Screen Widgets](https://doc.moze.app/feature/lock-screen-widgets.md)
- [Widget 小工具](https://doc.moze.app/feature/widget.md) —— 若既有的
  `/summary` 回應太重，可以考慮加一個輕量 `GET /widget-summary` 端點，
  但這是「效能優化」而非「新功能」，先確認 mobile 端實測需求再做
- [Apple Watch](https://doc.moze.app/feature/apple-watch.md)

> 原本這裡也列了「記帳模式」和「延後入帳」，重新看過原文後兩者都需要
> server 端資料支援，已移到 §2.10 / §2.11 展開，不再算 client-only。

**視市場決定，暫不建議做**：

- [台灣電子發票](https://doc.moze.app/feature/invoice.md) —— 需串接
  財政部電子發票平台 API，是台灣在地法規功能，跟記帳核心邏輯無關，
  建議等有台灣市場需求時再獨立立項

---

## 4. 建議實作順序

```
Phase 0(地基）: §2.1 通知中心 ✅ server + web UI 已完成(2026-07-30);mobile UI 待排期
Phase 1(核心記帳擴充）: §2.2 週期性收支 → §2.3 分期付款 → §2.6 退款(輕量，可插隊)
                        ✅ server + web UI 已完成(2026-07-30);mobile UI 待排期
Phase 2(統計口徑動刀，建議獨立 PR）: §2.4 拆帳
Phase 3(往來/範本，彼此獨立可並行）: §2.5 借還款追蹤、§2.7 範本
Phase 4(信用卡整組，依賴 Phase 1 的 recurring/installment）: §2.9
Phase 5(分析對帳，必做）: 延後入帳(必做前置) → §2.10 對帳模式/餘額調整 → 比較報表(輕)
Phase 6(AI 擴充，跟前面無依賴，可隨時插入）: §2.8 語音記帳
Phase 7(依賴專案 entity 決策，排期最晚）: §2.11 記帳模式
```

每個 Phase 落地時記得照 CLAUDE.md 的檢查表：
`alembic migration` + `projection.py` + `sync_applier.py` 三張登記表
+ write endpoint + read endpoint + `test_projection_consistency.py`
風格的 merge 契約測試 + 多帳本 dedup 測試。拆帳(§2.4)、退款(§2.6)、
延後入帳/餘額調整(§2.10)三項會動到既有統計聚合邏輯，改動時要跑一次
`pytest tests/` 全量回歸，確認舊有統計數字沒被新欄位影響。

Phase 7 排最晚是因為 §2.11 記帳模式明確依賴「專案」這個目前不存在的
entity —— 開始做之前要先跟產品面確認：專案是要做成正式 entity(工程量
比照 §2.2~§2.7 任一項)，還是拿現有 `tags` 暫代(工程量小很多但語意
不完全對)。這個決策沒定案，§2.11 沒辦法排進 sprint。
