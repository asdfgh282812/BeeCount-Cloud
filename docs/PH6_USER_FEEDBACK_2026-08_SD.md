# 2026-08 使用者回饋改善 SD（Phase 6~12 分段規格）

本文件是 2026-08-07 使用者一次提出的 17 項回饋（對帳漏單、信用卡卡片顯示範圍、
紅利回饋計算/時機、欠還款自動分類、帳戶列表改版、新增交易表單改造、系統性
清理項）的**設計文件（SD）**。目的是先把每一項的現況根因、修改範圍、跨端
影響講清楚，再分段（Phase 6~12）逐項實作 — 依照使用者指示「先寫 SD，之後
分段做」，本文件只做規格盤點，**不包含任何程式改動**。

實作前請重讀 [CLAUDE.md](../CLAUDE.md) 的「新增或修改 Sync Entity 檢查清單
(SOP)」— Phase 8（紅利回饋規則新增欄位）、Phase 9（欠還款）都會動到既有
sync entity 的欄位，必須照 7 個位置逐一確認，尤其 `snapshot_builder.py`
最容易漏。所有動到 Web UI 的 Phase 完成後都要**實際在瀏覽器操作過一遍**才
能算完成，不能只憑 `pytest`/`pnpm build`。

Phase 3~5（銀行帳戶改名、TWD 預設、主帳戶掛靠銀行卡、背景排程管理後台）已於
2026-08-07 全部完成，詳見 `.claude/beecount-phase3-5-remaining-plan.md`。本文件
的 Phase 編號接續其後，從 **Phase 6** 開始。

---

## 0. 總覽：17 項需求 → 7 個 Phase

| 原始需求編號 | 摘要 | 所屬 Phase | 影響範圍 |
|---|---|---|---|
| 1 | 轉入信用卡的轉帳沒出現在對帳模式 | Phase 6 | 後端 |
| 7 | 回饋金應該出現在對帳明細裡並可核對 | Phase 6 | 後端 |
| 2 | 單卡片詳情不該顯示主帳戶/其它子卡金額 | Phase 7 | 前端 |
| 3 | 主帳戶頁面的紅利回饋清單太長，需收合成按鈕 | Phase 7 | 前端 |
| 4 | 「四捨五入」設定了但實際回饋還有小數 | Phase 8 | 後端 |
| 5 | 交易日期事後修改，已入帳的回饋金是否跟著動 | Phase 8 | 後端 |
| 5-1 | 回饋金入帳時間應對齊原交易時間，而非固定 08:00 | Phase 8 | 後端 |
| 15 | 「週期結束後一次結算」要能選回饋日期（當月X日/次月Y日…） | Phase 8 | 後端+前端 |
| 16 | 紅利規則有交易掛著後除起訖日外都鎖編輯；結束的規則收進可折疊區；共用上限群組欄位依勾選顯示；帳戶選擇比照 Phase 11 | Phase 8 | 後端+前端 |
| 6 | 欠還款交易應自動帶備註/分類 | Phase 9 | 後端 |
| 8 | 帳戶列表改版成 Moze 風格緊湊清單 | Phase 10 | 前端 |
| 9 | 新增交易「帳戶」改必選，選擇改彈窗（比照 Phase 10 樣式） | Phase 11 | 前端 |
| 10 | 新增交易的分類/標籤加搜尋，且可在表單內直接新增 | Phase 11 | 前端 |
| 11 | 新增交易加「商店」欄位（選填），備註往上移 | Phase 11 | 前端 |
| 12 | 拿掉系統中所有 ¥ 符號 | Phase 12 | 前端 |
| 13 | 交易搜尋日期預設改「全部」 | Phase 12 | 前端 |
| 14 | 系統自動產生的交易不該漏分類（報表出現 Uncategorized） | Phase 12 | 後端 |
| 17 | 系統中百分比輸入統一寫成 `X%`（不要 `0.0X`） | Phase 12 | 前端 |

**依賴關係**：Phase 11（新增交易表單）的帳戶選擇彈窗要重用 Phase 10 新做的
帳戶列表樣式元件，**Phase 10 必須先做**。Phase 8 內部（4/5/5-1/15/16）互相
牽動同一批檔案（`card_rewards.py`/`card_reward_payout.py`/
`CardRewardRulesSection.tsx`），建議合併成一次 session 依序做完，不要拆散，
否則同一個檔案要來回讀好幾次。其餘 Phase 彼此獨立，可任意調整順序。

---

## Phase 6：對帳模式漏單（需求 #1、#7）

### 現況根因

對帳清單由 `src/routers/read/ledgers.py::get_account_statement`
（查詢本體約 1487-1497 行）組出，關鍵過濾條件：

```python
ReadTxProjection.tx_type.in_(["expense", "income"]),   # 排除了 "transfer"
ReadTxProjection.account_sync_id.in_(member_ids),       # 轉帳交易這欄是 NULL
```

兩個獨立原因疊加，導致**任何轉入信用卡的轉帳**（例如從計算回饋金的戶頭轉進來的錢）完全不會進對帳清單：

1. `tx_type` 白名單不含 `"transfer"`。
2. 就算放行 `transfer`，轉帳交易的金額歸屬欄位是 `from_account_sync_id`/
   `to_account_sync_id`（`src/projection.py:319/321`），不是
   `account_sync_id`，原本的 `account_sync_id.in_(member_ids)` 篩選對轉帳
   交易永遠不成立。

需求 #7（回饋金要出現在對帳明細、且能核對是否已入帳單）：紅利回饋入帳本質上
也是一筆 `tx_type=income`（`card_reward_payout.py::_emit_reward_tx`），照理
說已經會被目前的 `tx_type.in_(["expense","income"])` 篩到 —— 但因為回饋入帳
的 `account_sync_id` 通常設在**信用卡本身**（`reward_account_id` 若設成該卡，
或設成別的現金帳戶則不會進這張卡的對帳單，這是預期行為，只有「入到這張卡」
的回饋金才該出現在這張卡的對帳單）。需要跟使用者確認的實際問題是：目前
`get_account_statement` 有沒有特別排除「來源是系統產生（reward payout）」
的交易？初步排查沒發現額外排除，只要 #6-1 的欄位補齊，回饋金交易應該已經會
自然出現在對帳清單——**這項需要在動工前先用瀏覽器實測目前是否真的漏，若
只是需求 #1 的連帶效應（因為回饋金本身不是轉帳，是 income）則可能已經沒事，
若還是漏，要另外抓 `account_sync_id` 有沒有正確寫入。**

### 修改內容

- `src/routers/read/ledgers.py::get_account_statement`：
  - 查詢條件從單一 `tx_type.in_(["expense","income"])` 改成
    `OR` 分支：`(tx_type IN ('expense','income') AND account_sync_id IN member_ids)`
    `OR (tx_type = 'transfer' AND to_account_sync_id IN member_ids)`。
    （只收「轉入」，不收「轉出」——轉出這張卡的錢通常語意上不是消費，維持
    現況排除。）
  - `signed`（約 1505 行）目前只處理 `expense`/`income` 兩種正負號，需新增
    第三種分支：轉入視為「還款/預繳」，比照 `income` 記為負值（減少應繳
    餘額），並在回傳給前端的列項加上可辨識的 `tx_type: 'transfer'` 供
    UI 顯示「轉帳」標籤（對齊使用者截圖 Image #4 的「轉帳」列樣式）。
  - 順帶檢查同一支函式內是否有其它地方假設「對帳清單只有 expense/income
    兩種」（例如彙總 `新增消費`/`已確認金額` 的欄位計算），轉帳分支不應該
    被誤算進「新增消費」的加總（那格語意是花費，不是還款）。
- 需求 #7：先用瀏覽器實測回饋金交易是否出現在對帳清單；若正常，僅需在對帳
  明細列項的顯示上補上「回饋」標籤（辨識來源），不需要動查詢邏輯；若異常，
  比照上面轉帳分支的方式追查 `account_sync_id` 寫入路徑
  （`card_reward_payout.py::_emit_reward_tx`）。

### 測試

- `tests/` 新增／擴充對帳相關測試（沿用 `test_reconciliation` 命名慣例，若
  尚無同名測試檔案則新建），覆蓋：轉入信用卡的轉帳出現在清單且正負號正確、
  轉出不出現、回饋金交易出現且可對帳確認。
- 瀏覽器手測：比照使用者截圖情境（Image #3/#4），實際做一筆轉帳轉入信用卡，
  確認對帳模式清單出現這筆、金額與標籤正確。

---

## Phase 7：單卡片詳情顯示範圍（需求 #2、#3）

### 現況根因

**#2** — `frontend/apps/web/src/components/dialogs/AccountDetailDialog.tsx`
的 `useAccountBilling()`（115-208 行）：

```ts
// 125-128 行附近
const billingAccountId = isBillingChild ? account.parent_account_id : account.id
```

只要這張卡有 `parent_account_id`（掛在主帳戶群組下），整個 billing 資料
（`AccountStatsHeader` 451-477 行、`AccountCardInfo` 578-586 行、
`AccountStatementSection` 374-379 行）全部改抓**主帳戶**的合併資料，而非這張
卡自己的。`CreditCardBillingSection` 更在 908-918 行直接列出
`summary.members`（所有子卡）的各自消費金額——這是使用者截圖 Image #5「永豐
Sport卡」詳情頁卻看到「子卡明細：Green卡/大戶信用卡/永豐Sport卡/永豐幣倍卡」
四張卡金額的直接成因。

現有的「所屬主帳戶」連結（313-322 行 `onJumpToParentAccount`）只能整個跳轉
到主帳戶視圖，無法在停留於子卡視圖時只看自己的數字。

**#3** — `frontend/apps/web/src/components/dialogs/CardRewardRulesSection.tsx`
136-175 行：對 `account_group`（主帳戶）會抓出所有子信用卡，**逐一**渲染
一份完整的 `SingleCardCardRewards`（含 284-308 行的標題列 + 303-307 行的
金額徽章），N 張卡就疊 N 個區塊，把下方交易列表往下推（`AccountDetailDialog.tsx:349`
緊接著就是交易列表 382 行起）。

### 修改內容（純前端）

**#2**：

- `AccountDetailDialog.tsx::useAccountBilling()`：拆成兩個資料來源 —
  1. **自身資料**（永遠用 `account.id` 查，不因 `isBillingChild` 改用
     parent id）：卡片自己的新增花費/已繳/剩餘帳款/信用額度/帳單日/繳款日/
     本期回饋。
  2. **主帳戶合併資料**（僅在使用者主動點「所屬主帳戶」或展開「查看所有
     子卡」時才 fetch，非預設載入）。
- `CreditCardBillingSection` 908-918 行的「子卡明細」整段移除**或**改成
  `account_group` 自己的詳情頁才顯示（子卡自己的詳情頁不該看到兄弟卡）。
- 「所屬主帳戶」連結（313-322 行）保留，維持現有「可以連結過去」的需求，
  但不在本頁預先載入/顯示主帳戶或其它子卡的金額。

**#3**：

- `CardRewardRulesSection.tsx` 163-175 行（`account_group` 逐卡渲染
  `SingleCardCardRewards`）改成：先渲染一個單行摘要按鈕（顯示「紅利回饋
  合計 + 涉及卡片數」，例如「紅利回饋（4 張卡）合計 ¥xxx」— 待 Phase 12
  拿掉 ¥ 後改純數字+幣別），點擊後用既有的 dialog 模式（比照 313-454 行
  單一規則清單彈窗的既有 pattern）展開，內容才是目前這 N 個
  `SingleCardCardRewards` 區塊。
- 單一信用卡（非主帳戶）視圖維持現況（本來就只渲染一個 `SingleCardCardRewards`，
  不受影響）。

### 測試

- 瀏覽器手測：比照使用者截圖情境，開一張有掛主帳戶的子卡詳情，確認只顯示
  自己的金額；點「所屬主帳戶」能跳轉看到合併視圖；主帳戶頁面的紅利回饋
  收合成一顆按鈕，點開才看到各卡明細。

---

## Phase 8：紅利回饋計算與規則管理（需求 #4、#5、#5-1、#15、#16）

這五項全部集中在 `src/services/card_rewards.py`、
`src/services/card_reward_payout.py`、
`frontend/apps/web/src/components/dialogs/CardRewardRulesSection.tsx`，
建議一次做完，避免同一批檔案反覆修改。涉及既有 sync entity
（`ReadCardRewardRuleProjection`）新增欄位，**必須照 CLAUDE.md SOP 的 7 個
位置逐一更新**（DB migration、`projection.py`、`sync_applier.py`、
write router、read router、⚠️`snapshot_builder.py`、partial-update 測試）。

### #4 取整方式沒有真的取整到整數

**現況**：`card_rewards.py:207-212` 的 `_round_amount()`：

```python
def _round_amount(value, rounding):
    if rounding == "floor": return math.floor(value * 100) / 100
    if rounding == "ceil":  return math.ceil(value * 100) / 100
    return round(value, 2)
```

三種模式全部只取整到**小數點後兩位（分）**，不是取整到整數。使用者選了
「四捨五入」，預期的是整數金額（對照 Moze 截圖 Image #16，「單筆」用
「保留小數」、「總額」才用「四捨五入」——**兩段式設計**：單筆先不取整、
總額才取整）。目前 BeeCount 只有一段（單筆就取整到分），且完全沒有「取整
到整數 vs 兩位小數」的精度選項。

**修改內容**：

- Schema：`ReadCardRewardRuleProjection` 新增欄位
  `total_rounding: str`（`round`/`floor`/`ceil`/`keep`，預設 `round`），
  既有 `rounding` 欄位語意改為**單筆**取整方式，新增 `keep`（保留小數，
  不取整，比照 Moze「單筆：保留小數」選項）。
- `card_rewards.py`：
  - `compute_tx_reward_amount`（215-221 行）依 `rule.rounding` 決定單筆是否
    取整（`keep` 時完全不呼叫 `_round_amount`，維持原始浮點數，讓誤差留到
    加總階段一次處理，避免多筆小數各自取整造成總額偏差）。
  - `compute_account_card_rewards`（436-443 行）加總後，依 `rule.total_rounding`
    對**總額**做一次取整——這裡的 `_round_amount` 需要能取整到「整數」
    （目前的 `_round_amount` 只會到分，需擴充：取整到整數時
    `round(value)`/`math.floor(value)`/`math.ceil(value)`，不再固定 `*100`）。
  - `card_reward_payout.py` 實際入帳邏輯（`_materialize_per_tx`）比照調整，
    確保預覽（preview）跟實際入帳（payout）用同一套規則（維持現況「兩邊共用
    同一段計算」的優點，不要分岔出兩套邏輯）。
- 前端 `CardRewardRulesSection.tsx`：「單筆取整方式」下拉新增「保留小數」
  選項；新增「總額取整方式」下拉（緊鄰「單筆取整方式」旁）。

### #5 / #5-1 交易日期事後修改 & 回饋入帳時間對齊原交易時間

**現況（已確認的根因）**：

- `CardRewardPayout.dedup_key` 是交易的 `sync_id`（不會變），一旦這筆交易的
  回饋已入帳，`_materialize_per_tx`（`card_reward_payout.py:262-264`）永遠
  把它視為「已處理」而跳過——**事後修改交易日期／金額／分類，已入帳的回饋
  金完全不會被重算、移動或沖銷**，變成一筆跟修改後交易脫鉤的孤兒交易。目前
  唯一有沖銷邏輯的路徑是**退款**（`reverse_card_reward_payouts_for_refund`，
  `_shared.py:931-933`），直接編輯原交易則沒有對應處理。
- 回饋交易固定顯示 08:00 的成因：`_date_to_utc_dt()`（`card_rewards.py:141-144`）
  只接受 `date`（不含時分秒），一律建構 `00:00:00 UTC`，換算 UTC+8 顯示就是
  `08:00`。原交易 `happened_at` 的時分秒完全沒有被傳遞到回饋交易。

**修改內容**：

- **5-1（時間對齊）**：`compute_settlement_date`（316-341 行）目前回傳
  `date`；改成同時回傳/接受來源交易的時間資訊，`_emit_reward_tx`
  （`card_reward_payout.py:297/345`）改用「結算日期 + 來源交易的
  時:分:秒」組出 `happened_at`，而非固定呼叫 `_date_to_utc_dt(date)` 補
  00:00:00。單筆結算（一筆交易對一筆回饋）直接沿用該筆交易的時間；
  週期結算（多筆交易合併一筆回饋，`period_end` 情境）沒有單一「來源交易」
  可對齊，維持現況固定時間（或改成期末當下的系統時間也可，需與使用者確認，
  本文件先假設「有明確單一來源交易的才對齊，週期彙總的維持固定時間」）。
- **5（事後修改重算）**：在交易更新路徑（`src/routers/write/_shared.py`
  更新分支，約 1199-1229 行，緊鄰現有的 `_assert_reward_rules_valid`/退款
  沖銷呼叫處）新增檢查：若這筆交易存在對應的 `CardRewardPayout`
  （用 `dedup_key == tx.sync_id` 查）且本次更新有動到會影響回饋計算的欄位
  （`happened_at`／`amount`／`category_id`／`account_id`），呼叫沖銷函式
  （比照 `reverse_card_reward_payouts_for_refund` 的模式新增一個
  `reverse_card_reward_payouts_for_edit`），沖銷後把該筆交易的 dedup 狀態
  清掉，讓下一輪排程（`scheduled_jobs.py` 的 `card_reward_payout` job，
  5 分鐘一次）重新按新日期/金額計算並補發正確的回饋交易。需要決定沖銷方式：
  直接刪除舊回饋交易 vs. 新增一筆反向沖正交易——**建議直接刪除舊回饋交易**。
  - 這項邏輯要小心避免無限迴圈：刪除本身產生的交易/回饋交易不能再觸發同一
    段「編輯偵測」。
  - 若編輯只是改備註/商店欄位等不影響回饋計算的欄位，不要觸發沖銷重算
    （避免使用者隨手改備註就把回饋金搞亂）。

### #15 週期結束後一次結算的回饋日期要能選

**現況**：`settlement_type = "period_end"` 只會固定在期間結束當天入帳
（`compute_settlement_date` 339-340 行），沒有「當月幾號／次月幾號」的欄位。

**修改內容**：

- Schema 新增兩個欄位（僅在 `settlement_type == "period_end"` 時有意義）：
  - `settlement_month_offset: int`（0 = 當月，1 = 次月，2 = 次二月…）
  - `settlement_day_of_month: int`（1~28，避免月底日期溢出問題，統一限制
    在 28 以內，比照多數帳務系統的保守作法）
  - 兩者皆為 `None` 時維持現況行為（期間結束當天，向下相容既有規則）。
- `compute_settlement_date`：`period_end` 分支新增計算：以期間結束日期為
  基準，加上 `settlement_month_offset` 個月，日期換成
  `settlement_day_of_month`（若當月沒有這麼多天，退回當月最後一天）。
- 前端「回饋入帳時機」選了「週期結束後一次結算」時，展開「回饋日期」欄位：
  月份下拉（當月/次月/次二月...) + 日期下拉（1~28號），組合成好讀的文字
  （例如「次月 1 號」「當月 4 日」），對齊使用者截圖 Image #15/#16 的 Moze
  呈現方式。

### #16 規則鎖編輯 + 已結束規則收合 + 帳戶選擇/共用上限群組欄位優化

**現況**：`snapshot_mutator.py::update_card_reward_rule`（1829-1952 行）
目前**完全沒有鎖**，即使規則底下已經有交易掛著（`reward_rule_sync_ids_json`
參照）或已有 `CardRewardPayout` 記錄，`rate_value`/`rate_type`/`rounding`
等核心計算欄位都能被改掉，且不會補算/重算既有交易，等於默默竄改「已經算過」
的規則基礎。前端「共用上限群組」區塊（`CardRewardRulesSection.tsx` 約
795-820 行）不論有沒有填「本期回饋上限」都固定顯示。

**修改內容**：

- 後端 `update_card_reward_rule`：新增檢查——若規則已有交易參照
  （查 `ReadTxProjection.reward_rule_sync_ids_json` 是否含此規則
  `sync_id`）或已有 `CardRewardPayout` 記錄，鎖定 `rate_type`/`rate_value`/
  `rounding`/`total_rounding`/`calc_basis`/`interval`/`category_ids`/
  `min_spend_threshold`/`min_tx_amount`/`cap_amount`/`cap_shared_key`/
  `settlement_type`/`settlement_days`/`settlement_month_offset`/
  `settlement_day_of_month`/`reward_account_id` 等計算相關欄位，回傳
  422（帶清楚錯誤訊息，前端對應提示「此規則已有交易，僅能調整名稱/
  起訖日期，如需變更計算方式請刪除後新建」）。`starts_at`/`ends_at`/
  `label`/`note`/`enabled` 維持可編輯。
- 刪除規則的既有邏輯需確認：規則已有交易掛著時，刪除行為是否要保留（軟刪除/
  標記停用）而非物理刪除，避免歷史交易的規則參照斷鏈——需與現有
  `delete` 端點的行為核對，若目前允許直接刪，這裡也一併補上「有交易掛著時
  改為停用（`enabled=false`）而非真刪除」的邏輯，呼應使用者原話「只能刪除
  等」的彈性但避免歷史資料斷鏈。
- 前端：
  - 規則列表新增「已結束」判斷（`ends_at` 已過 or `enabled=false`），已結束
    的規則收進一個可折疊區塊（「已結束的活動」），不再與進行中規則混在一起，
    也不從清單直接消失。
  - 編輯彈窗：規則已鎖定時，計算相關欄位全部 disable（保留視覺可見但不可
    改），起訖日期維持可編輯，並顯示提示文案。
  - 「共用上限群組」區塊（約 795-820 行）改成只在「本期回饋上限」欄位有值
    時才顯示，未填時整段隱藏（比照使用者需求，避免空規則也要看到一堆不相關
    的群組勾選 UI）。
  - 帳戶選擇欄位比照 **Phase 11** 新做的帳戶選擇彈窗樣式（見下方 Phase 11），
    取代目前的下拉選單——**這項必須等 Phase 11 做完才能進行**，可留到
    Phase 11 完成後再補這一小塊。

### 測試

- `tests/test_card_rewards.py`（或現有對應檔案）擴充：
  - 單筆「保留小數」+ 總額「四捨五入」的組合，驗證加總後是整數。
  - 修改已入帳回饋的來源交易日期/金額，驗證舊回饋被沖銷、排程重跑後補上
    以新日期為準的回饋（且只沖銷「有影響」的欄位變更，改備註不觸發）。
  - 回饋交易 `happened_at` 的時分秒對齊來源交易。
  - `settlement_month_offset`/`settlement_day_of_month` 各種組合的結算日期
    計算正確。
  - 規則有交易掛著時，PATCH 計算欄位回 422；PATCH 起訖日/label 仍成功。
  - 依 CLAUDE.md SOP 補 partial-update 契約測試（新欄位 partial update 不會
    把既有欄位衝成 null）。
- 瀏覽器手測：比照使用者原始 5 個情境逐一操作驗證。

---

## Phase 9：欠還款交易自動備註/分類（需求 #6）

### 現況根因

欠還款（還款/欠款支付）就是一筆一般 `income`/`expense` 交易，透過
`debt_id` 欄位關聯（`schemas.py:567/1315/1357`）。寫入路徑
`_shared.py:890-892` 只做 `_assert_debt_exists` 存在性檢查，**完全沒有自動
帶入備註或分類**——對照既有的退款交易（`ensure_refund_category`，
`_shared.py:877-889`）跟回饋金交易（`card_reward_payout.py::_emit_reward_tx`
115-127 行）都有自動歸類的先例，欠還款目前是唯一沒跟上的一個。

### 修改內容

- `_shared.py` 建立交易的分支（890-892 行附近）：當 `debt_id` 有值且
  `category_id`/`category_name`、`note` 為空時：
  - 分類：仿 `ensure_refund_category`/`ensure_reward_category` 的模式，新增
    `ensure_debt_category(db, user_id, direction)`——依 `direction`
    （debt 的方向：對方欠我 / 我欠對方）決定用「收款」或「還款」分類
    （自動建立或複用既有的專屬分類，user-global，跟既有兩個 `ensure_*`
    函式共用同一套「找不到就建立」邏輯，避免三份幾乎一樣的程式碼——可以
    考慮把三個 `ensure_*_category` 收斂成一個共用 helper，帶分類名稱/類型
    參數）。
  - 備註：帶入欠款的 `counterparty_name`（例如「王小明 還款」/「向王小明
    借款」），格式需與 debt 的 `direction` 對應，讓使用者一眼看出方向。
  - 兩者都只在使用者「沒有自己填」時才帶入預設值，使用者已填的內容不覆蓋。

### 測試

- `tests/test_debts.py`（或相關檔案）新增：建立欠還款交易未填分類/備註時，
  自動帶入對應分類與備註文字；使用者有填時不覆蓋。

---

## Phase 10：帳戶列表 UI 改版（需求 #8）

### 現況

`frontend/packages/web-features/src/features/AccountsPanel.tsx`：

- 已有的部分（不用大改）：`MobileStyleAssets`（70 行起）依 `account_type`
  分組、每組有 collapsible header（129-215 行，圖示+名稱+數量徽章+
  合計金額+展開收合），跟 Moze 截圖的分組樣式方向一致。
- 需要換掉的部分：展開後內容目前是 `BankCardTile`（562-816 行）——大張
  漸層卡片（`aspectRatio: 16/11`、裝飾性 SVG 圖案、底部三欄統計），排成
  grid（196-212 行）。這是使用者截圖 Image #6 抱怨「太糟」的視覺主因，
  跟 Moze 截圖（Image #11/#12/#13）的**緊湊清單列**（圖示 + 名稱 +
  卡號末四碼/子帳戶數量徽章 + 右側金額，一行一個，子帳戶用縮排巢狀顯示）
  差異很大。
- 目前完全沒有「子帳戶縮排巢狀」呈現：`account_group` 底下的子帳戶
  （信用卡/銀行卡）跟其它同類型帳戶並列在同一個 grid 裡，沒有視覺從屬關係
  （對照 Moze 截圖：主卡是一行，子卡用樹狀線縮排在底下）。
- `HiddenAccountsSection`（236-346 行）已經是列表列樣式（圖示+名稱+金額
  一行），可以作為新元件的樣式基礎/起點。

### 修改內容（純前端，不動後端資料模型——彙總邏輯已存在，只是換渲染方式）

- 新增一個緊湊列表列元件（可從 `HiddenAccountsSection` 既有的列樣式抽出
  共用），取代 `BankCardTile` 在主要列表的用法：圖示、帳戶名稱、右側金額
  （比照 Moze：主帳戶/一般帳戶一行，若有子帳戶則顯示徽章數字 + 可展開）。
- `computeTypeGroups`（951 行）目前只依 `account_type` 分組；需要新增
  「群組內巢狀」的資料結構——同一個 `account_group` 底下的子帳戶
  （`parent_account_id` 指向該群組）在渲染時縮排顯示在主帳戶列下方（可收合，
  比照 Moze 截圖的樹狀線）。
- 保留現有的「Hero/總覽卡」與「多幣別 CurrencyAssetCard」在最上方，這部分
  使用者沒有抱怨，不動。
- `BankCardTile` 元件：確認沒有其它地方複用（若只有 `AccountsPanel` 用到，
  改完後可以整個刪除；若有共用，改成 opt-in 的展示模式保留）。

### 測試

- `pnpm build`/`pnpm test:unit` 過。
- 瀏覽器手測：比照 Moze 截圖排版，確認分組/子帳戶縮排/展開收合/金額對齊都
  正常，桌面與手機寬度都要看過（原本就有 grid 的 responsive breakpoint，
  改版後要重新確認窄螢幕排版）。

---

## Phase 11：新增交易表單改造（需求 #9、#10、#11）

**依賴 Phase 10**：帳戶選擇彈窗要重用 Phase 10 做出來的緊湊列表列樣式，
建議 Phase 10 完成並過瀏覽器手測後再開始本 Phase。

### 現況

主要檔案：`frontend/packages/web-features/src/features/TransactionsPanel.tsx`
（表單 UI）+ `frontend/apps/web/src/pages/sections/TransactionsPage.tsx` /
`frontend/apps/web/src/components/GlobalEditDialogs.tsx`（驗證邏輯，兩處
邏輯需同步修改，避免只改一邊）。

- **#9 帳戶必選**：`TransactionsPage.tsx:1619-1620`
  明確註解「非轉帳交易允許不選帳戶」，是 2026 年較早之前為了相容 mobile
  匯入的無帳戶交易而刻意放寬的。`GlobalEditDialogs.tsx` 同步沒有檢查。
  死代碼 `transactions.error.accountRequired`（三語系都有但沒被引用）—
  可以直接重新啟用這把鑰匙。
- **帳戶選擇 UI**：`TransactionsPanel.tsx:729-763`（單一帳戶）、680-725
  （轉帳的轉出/轉入）都是 shadcn `<Select>` 原生下拉，無搜尋、無圖示，跟
  截圖 Image #14 一致。
- **#10 分類/標籤**：分類走 `CategoryPickerDialog`→`CategorySelector.tsx`
  （固定圖示網格，**沒有搜尋**，**沒有內嵌新增**）；標籤走
  `TagPickerDialog`→`TagSelector.tsx`（**已有搜尋**，但 `TagSelector.tsx:34-35`
  明確設計成「不內嵌新增，逼用戶去標籤管理頁」）。兩者都需要補上「表單內
  直接新增」的能力。
- **#11 欄位順序**：`TransactionsPanel.tsx` 目前順序（尾段）：帳戶(729-763)
  → 欠款(765-841) → 回饋規則(843-898) → 標籤(900-942) → **備註**(949-956)
  → **實際入帳日**(957-966)。備註已經緊鄰在實際入帳日之前，新增「商店」
  欄位後，備註要再往上移一層（例如移到分類/帳戶附近），商店欄位放在原本
  備註的位置或備註與實際入帳日之間，讓「商店」跟「備註」相鄰。

### 修改內容

**#9 帳戶必選 + 選擇改彈窗**：

- `TransactionsPage.tsx`/`GlobalEditDialogs.tsx` 的 `handleSaveTx`：加回
  帳戶必選檢查（比照既有分類必選檢查的寫法，709-763 行附近），非轉帳與
  轉帳都要檢查（轉帳的話轉出/轉入至少要選一個，依現有轉帳語意決定是否兩者
  都必填，需與轉帳本身既有規則對齊，不要跟 mobile 匯入的無帳戶舊資料衝突
  ——**這批舊資料是既有資料的編輯，不是新增，必選檢查應只套用在「新建」
  與「使用者主動變更帳戶」的情境，避免既有無帳戶的 mobile 交易被迫在 web
  端強制選帳戶才能存檔，變成使用者被鎖死無法編輯備註等其它欄位**——這點
  需要在動工前跟現有 1619-1620 行的相容性註解對齊，可能需要做成「僅當
  使用者從無到有動過這個欄位或建立新交易時才強制」而非「有交易存在就一律
  擋存檔」）。
- 帳戶選擇改成彈窗（比照 `CategoryPickerDialog`/`TagPickerDialog` 的 Dialog
  模式），內容重用 Phase 10 的緊湊列表列樣式（分組 + 圖示 + 名稱 + 金額，
  子帳戶縮排），取代 `<Select>`。轉帳的轉出/轉入各自開一個一樣的彈窗。
- Phase 8 的規則帳戶選擇（`CardRewardRulesSection.tsx`）與 Phase 16 提到的
  規則帳戶選擇，都改用同一個彈窗元件，避免三處各自維護。

**#10 分類/標籤搜尋 + 表單內新增**：

- `CategorySelector.tsx`：新增搜尋輸入框（比照 `TagSelector.tsx` 現有的
  `showSearch` 邏輯，可直接抽成共用 hook/component）。
- `CategorySelector.tsx`/`TagSelector.tsx`：都新增「找不到時顯示『新增
  "xxx"』」的內嵌建立入口，呼叫既有的分類/標籤建立 API（`api-client` 應該
  已有對應的 create 方法，供 CategoriesPage/TagsPage 使用，這裡改成直接
  複用同一支 API，不用另外開分類/標籤管理頁）。`TagSelector.tsx` 現有
  「刻意不做內嵌新增」的設計註解（34-35 行）需要正式移除並更新註解說明
  為何改變（使用者明確要求）。

**#11 商店欄位 + 備註上移**：

- `forms.ts`：`TxForm` 型別新增 `merchant?: string`（選填），
  `txDefaults()` 補上預設空字串。
- `TransactionsPanel.tsx`：在帳戶/分類附近新增「商店」輸入框（選填，無驗證），
  備註欄位往上移到商店欄位之前或緊鄰，實際入帳日仍維持在最後（時間相關欄位
  放在表單尾端符合現有慣例）。
- 後端：`schemas.py` 交易建立/更新 request 新增 `merchant: str | None`
  欄位，`models.py::ReadTxProjection` 新增對應欄位（純展示用途，資料庫層面
  照 CLAUDE.md SOP 走完整套新增欄位流程——雖然商店不是獨立 sync entity，
  是既有 Transaction entity 的新欄位，一樣要確認
  `projection.py`/`sync_applier.py`/`snapshot_builder.py` 三處都有帶上這個
  欄位，避免 Partial Update 把它衝掉）。

### 測試

- `tests/` 新增交易建立/更新帶 `merchant` 欄位的 partial-update 契約測試
  （比照 SOP 第 7 點）。
- 前端 `pnpm test:unit`：分類/標籤搜尋、內嵌新增、帳戶必選驗證的單元測試。
- 瀏覽器手測：新增交易走一遍，包含帳戶彈窗選擇、分類搜尋+新增、標籤搜尋+
  新增、商店欄位、備註位置、不選帳戶時擋存檔的錯誤提示。

---

## Phase 12：系統性清理（需求 #12、#13、#14、#17）

四項彼此獨立，可以合併在同一個 Phase 一次處理，也可以視 token/時間再拆開。

### #12 拿掉所有 ¥ 符號

**現況**：貨幣符號目前有 **4 套各自獨立**的實作，沒有共用單一來源：

1. `frontend/packages/web-features/src/format.ts:89-106`（本地
   `currencySymbol()`）
2. `frontend/packages/web-features/src/components/Amount.tsx:266-282`
   （**重複**的第二份 `currencySymbol()`，是大多數金額顯示實際會用到的
   那份，因為 `<Amount>` 元件全站廣泛使用）
3. `frontend/packages/web-features/src/lib/currencies.ts:41-53`
   （用 `Intl.NumberFormat` 動態推導），被
   `TransactionRow.tsx:287`、`annual-report/pages/{PageWeekday,PageHabits,
   PageExtremes,PageOverview}.tsx`、`widgets/PosterDialog.tsx` 呼叫
4. 上述 annual-report 5 個檔案**又各自內嵌了第 4 份**寫死的符號對照表
   （`{ CNY: '¥', USD: '$', ... }`），跟第 3 份並存，沒有真的統一使用
   `lib/currencies.ts`。

**修改內容**：

- 先把 4 套實作收斂成 **1 個共用 `currencySymbol()`**（建議放
  `lib/currencies.ts`，其餘 3 處改成直接 import，annual-report 5 個檔案
  內嵌的對照表整段刪除改 import）。
- 收斂後，依使用者需求「不需要¥」：CNY/JPY 一律不顯示符號前綴，只顯示
  數字（+ 幣別代碼徽章，若原本就有的話維持，只拿掉符號本身），其餘幣別
  （USD/EUR/GBP/HKD 等）符號是否一併拿掉需與使用者確認範圍（原始需求寫
  「拿掉系統中所有的¥符號」，字面上只針對 ¥，不是所有貨幣符號——本 Phase
  先只處理 ¥/CNY/JPY，其它符號維持現況）。
- 全域再跑一次 grep 確認沒有遺漏的 `¥` 字面量（注意排除純註解/JSDoc 範例，
  那些不影響 UI，可以不用改，但建議一併順手清掉避免誤導後續維護者）。

### #13 交易搜尋日期預設改「全部」

- ### 步驟一：保持 `defaultTxFilter()` 預設值不變

  - `defaultTxFilter()` 依然保留現有的邏輯（即預設 `dateFrom` / `dateTo` 為 `todayRange()`）。
  - 如此一來，初始載入頁面且搜尋欄為空時，系統天然維持「僅限今日」的視覺與搜尋範圍。

  ### 步驟二：於搜尋觸發點（Search Event Handler）建立連動判斷

  需要在「觸發搜尋」的邏輯點（例如按下 Enter、點擊搜尋圖示、或是帶有 debounce 的輸入事件）注入判斷：

  - **當 `searchKeyword.trim() !== ''`（有搜尋關鍵字）：**
    - 將現有篩選狀態（filter state）中的 `dateFrom` 與 `dateTo` 自動更新為 `""`（空字串，代表「全部」）。
    - 重新打 API 發送請求。
  - **當 `searchKeyword.trim() === ''`（搜尋關鍵字被清空）：**
    - 將 `dateFrom` 與 `dateTo` 自動回復為 `todayRange()`。
    - 重新打 API 發送請求。

  ### 步骤三：UI 狀態與使用者操作覆蓋（Override）權限處理

  需要注意以下 UI 連動細節：

  1. **日期選擇器（Date Picker）與 Chip 狀態同步**：
     - 當系統因為搜尋關鍵字自動將日期切換為「全部」時，畫面上的「今日」快速篩選 Chip 必須自動解除高亮，日期選擇輸入框也應呈現空白。
  2. **使用者手動覆蓋**：
     - 若使用者「先輸入了搜尋關鍵字」（此時日期自動變全部），但「隨後又手動點擊了『今日』Chip 或挑選了特定日期」，系統應尊重使用者**最後的手動操作**，以手動選擇的日期去限制搜尋結果。

  ### 步驟四：LocalStorage 持久化機制評估

  關於 `TX_FILTER_STORAGE_PREFIX` 版本控制：

  - **評估結果**：無需 bump 至 `v3`，繼續沿用 `v2` 即可。
  - **原因**：因為預設狀態（無關鍵字）下的日期範圍依然是「今日」，沒有變動預設值，所以不需要強制覆蓋既有使用者的本地儲存紀錄。
  - **快取還原邏輯修正**：從 LocalStorage 讀取舊篩選條件並初始化時，應額外加上一層校驗——若快取內含有 `searchKeyword`，則自動套用全時間搜尋；若快取無 `searchKeyword`，則維持「今日」。

### #14 系統自動產生的交易漏分類

**現況**：兩個明確缺口（另兩個已有預設，不用動）：

- **週期性收支**：`src/services/recurring_materializer.py:246-247`——
  `rule.category_sync_id` 為空時，生成的交易完全不帶分類欄位。前端
  `RecurringRulesPage.tsx:172,200` 建立規則時分類也是選填，沒有擋。
- **分期付款**：`src/routers/write/installment_plans.py:211-224`——
  每一期生成的交易直接沿用 `req.category_id`，該欄位在
  `schemas.py:1519` 也是選填。

已經有預設可參考的先例（不用動，但可以當作範本）：`ensure_reward_category`
（`card_reward_payout.py:116`）、`ensure_refund_category`（`_shared.py`）。

**修改內容**：

- 後端：`WriteRecurringRuleCreateRequest.category_id`（非轉帳規則）與
  `WriteInstallmentPlanCreateRequest.category_id` 改成**必填**（轉帳類型
  的週期規則本來就不需要分類，維持現況允許為空，比照現有
  `RecurringRulesPage.tsx:172` 的 `tx_type === 'transfer' ? null : ...`
  邏輯，只在非轉帳時強制）。
- 前端：`RecurringRulesPage.tsx`、`InstallmentPlansPanel.tsx`/
  `InstallmentPlansPage.tsx` 的建立表單，分類欄位改成必選並加上驗證錯誤
  提示（非轉帳情境）。
- 兩者的 API schema 改必填後，需要處理**既有資料**：資料庫裡可能已存在
  `category_id IS NULL` 的舊規則/計畫——這些舊規則繼續產生無分類交易的
  問題不會因為前端擋新建就消失，需要額外一次性資料修復（腳本或後端啟動時
  的一次性遷移）把既有 `category_id IS NULL` 的規則導向一個「未分類/其它」
  分類，避免舊規則持續產生漏分類的交易。具體修復策略（自動指派 vs. 通知
  使用者手動補選）待實作時確認。
- 全面性原則（覆蓋需求 #14 的「所有新增功能」）：往後任何新增的「系統自動
  產生交易」功能，落地前都要走這條規則——要嘛必填分類，要嘛比照
  `ensure_refund_category` 模式自動歸類到專屬分類，不能留空。建議在
  CLAUDE.md 補一條「新增交易產生路徑」的檢查提醒（可在下一次更新
  CLAUDE.md 時一併加入，非本次程式碼改動範圍）。

### #17 百分比輸入統一寫成 `X%`

**現況**：目前並非全面性問題，是**單一欄位**的顯示慣例不一致：

- 信用卡回饋百分比（`CardRewardRulesSection.tsx`）：已經是「輸入 3 代表
  3%」，欄位標籤已明確標「(%)」，跟後端 `card_rewards.py:220-221`
  （`rate_value / 100`）一致，**不需要改**。
- 分期付款年利率（`interest_rate`）：後端明確吃**小數分數**
  （`installment_amortization.py:11`：0.06 = 6%/年），但前端 4 處輸入欄位
  （`TransactionsPanel.tsx:1174-1181`、`InstallmentPlansPanel.tsx:320-329`
  與 908 行、`AccountDetailDialog.tsx:1270-1279`）標籤只寫「年利率」，沒有
  `%` 提示，`step="0.001"`，使用者必須自己知道要打 `0.06` 而不是 `6`——這
  正是使用者截圖抱怨的「打成 0.0X」情境。

**修改內容**：

- 統一原則：**後端資料/計算邏輯維持現況存小數分數**（`installment_amortization.py`
  的既有數學不用動，改動範圍越小越安全），**只在前端 UI 層改成讓使用者輸入
  整數百分比，送出前才 ÷100**：
  - 4 處 `interest_rate` 輸入框：`step="0.1"`（改成百分比精度，年利率通常不
    需要到千分位），欄位標籤加上「(%)」後綴（比照回饋百分比欄位的既有寫法），
    UI 內部用一個「顯示用百分比」state（`interest_rate * 100`），使用者輸入
    看到的是 `6`，送出 API 前換算回 `0.06`，讀取既有資料時反向換算成 `6`
    顯示。
  - 4 處呼叫點需要同步做這個顯示/送出換算，注意 `InstallmentPlansPanel.tsx`
    的 908 行（重新分期彈窗）容易漏改。
- 順手全域再搜一次，確認沒有其它百分比欄位漏網（研究階段已確認回饋百分比
  跟年利率是僅有的兩個百分比欄位，若實作時發現新的欄位，一併套用同一原則）。

### 測試

- 前端 `pnpm test:unit`：貨幣符號收斂後的顯示測試、搜尋預設值測試、年利率
  顯示/送出換算的單元測試。
- 後端 `pytest`：週期性收支/分期付款分類必填的驗證測試、既有資料修復腳本
  測試（若有）。
- 瀏覽器手測：搜尋預設「全部」、金額顯示無 ¥、建立週期規則/分期擋無分類、
  分期年利率輸入 6 顯示 6%（不是 0.06）且計算結果正確。

---

## 共用注意事項（套用到每個 Phase）

- 每個 Phase 做完都要跑 `pytest tests/ -q`（全量）+ 有牽動前端的話跑
  `pnpm build`/`pnpm test:unit`，**然後才進到瀏覽器手測**——不能只憑自動化
  測試過就宣告完成（CLAUDE.md 鐵律）。
- 動到 `ReadCardRewardRuleProjection`（Phase 8）或 `ReadTxProjection`
  新欄位（Phase 11 商店欄位）時，務必照 CLAUDE.md「新增或修改 Sync Entity
  檢查清單」7 個位置逐一確認，`snapshot_builder.py` 最容易漏，漏了會在
  連續 Update 時讀到空基線導致校驗失敗。
- Phase 之間如果發現前一個 Phase 的行號因為改動而偏移，動工前先重新 Read
  一次相關檔案確認行號，不要直接依賴本文件寫的行號動刀。
- 建議實作順序：Phase 6 → 7 → 9（三者互相獨立，風險低，可以任選順序）→
  Phase 10 → 11（有依賴，Phase 10 一定要先）→ Phase 8（範圍最大，建議獨立
  一次 session 做完，不要跟其它 Phase 混在同一個 session）→ Phase 12（清理
  類，最後做，不影響其它 Phase 的正確性判斷）。
