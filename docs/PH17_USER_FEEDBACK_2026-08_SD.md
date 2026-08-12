# 2026-08 使用者回饋改善 SD（Phase 17~24 分段規格）

本文件是使用者於 2026-08-12 一次提出的 8 項回饋（主帳戶分組歸屬、信用卡回饋
自然月顯示、SwipeSmart 回填正確性、帳戶納入總餘額開關、週期性收支編輯範圍、
新增記帳表單人性化、帳戶選擇器全站統一、回饋帳戶預設值）的**設計文件
（SD）**。目的是先把每一項的現況根因、修改範圍、跨端影響講清楚，再分段
（Phase 17~24）逐項實作——依照使用者指示「先寫 SD，之後分段做」，本文件
只做規格盤點，**不包含任何程式改動**。

Phase 編號接續 `docs/PH16_USER_MANUAL_SITE_SD.md`，從 **Phase 17** 開始。

實作前請重讀 [CLAUDE.md](../CLAUDE.md) 的「新增或修改 Sync Entity 檢查清單
(SOP)」——Phase 18（帳戶新增欄位）、Phase 22（信用卡回饋規則欄位/回傳格式
改動）都會動到既有 sync entity，必須照 7 個位置逐一確認，`snapshot_
builder.py` 最容易漏。所有動到 Web UI 的 Phase 完成後都要**實際在瀏覽器操
作過一遍**才能算完成，不能只憑 `pytest`/`pnpm build`。

---

## 0. 總覽：8 項需求 → 8 個 Phase

| 原始需求編號 | 摘要 | 所屬 Phase | 影響範圍 |
|---|---|---|---|
| 1 | 主帳戶（掛信用卡/銀行子帳戶）應歸類到信用卡/銀行分組，而非獨立的「主帳戶」分組 | Phase 17 | 前端 |
| 4 | 帳戶新增「納入總餘額」開關（對齊 Moze） | Phase 18 | 前端+後端 |
| 7 | 所有選帳戶頁面統一成跟新增記帳一樣的彈窗選擇器 | Phase 19 | 前端 |
| 8 | 新增回饋規則時，回饋帳戶預設帶入目前檢視的帳戶 + 選擇器改彈窗 | Phase 19 | 前端 |
| 6（前半） | 新增記帳表單版面重排 + 開啟時金額框自動 focus | Phase 20 | 前端 |
| 6（後半） | 分類推薦演算法 + 依分類帶入常用帳戶 | Phase 21 | 前端+後端 |
| 2 | 信用卡回饋「自然月」跨帳單週期時要分別顯示兩個月 + 顯示可刷金額上限 | Phase 22 | 後端+前端 |
| 3 | SwipeSmart 使用額度回填要依 `interval`（帳單週期/自然月）正確計算週期，並排除不合資格分類 | Phase 23（依賴 Phase 22） | 後端（+ SwipeSmart 端待開發，見 §Phase23 風險） |
| 5 | 週期性收支：編輯單筆生成的交易時要跳出「修改此記錄／修改連同未來週期」選擇，且「連同未來」要真的更新未來所有設定欄位 | Phase 24 | 前端+後端 |

**依賴關係**：Phase 21（分類/帳戶智慧推薦）在 Phase 20（表單版面重排）完成
後再做，兩者改同一個檔案（`TransactionsPanel.tsx`）的同一段區域，避免行號
互相偏移、來回讀檔。Phase 23（SwipeSmart 回填）依賴 Phase 22 新做出的「自
然月週期拆分」邏輯，必須先做完 Phase 22。其餘 Phase（17、18、19、24）彼此
獨立，可任意調整順序、任選先後。

---

## Phase 17：主帳戶依內容分組（需求 #1）

### 現況根因

`computeTypeGroups`（`frontend/packages/web-features/src/features/
AccountsPanel.tsx:606-633`）純粹用 `row.account_type` 字面值當分組 key
（609 行：`const key = row.account_type || 'other'`）。`account_group`
是 `TRADABLE_TYPES`/`ACCOUNT_ORDER`（572、599-602 行）裡的一個獨立項目，
永遠自成一個「主帳戶」分組區塊，不管底下實際掛的子帳戶是信用卡還是銀行
卡——這正是使用者截圖裡「玉山信用卡」主帳戶希望留在「信用卡」分組、卻被
歸到另一個獨立分組的成因（使用者截圖裡目前是正確的，因為 Moze 本身就是
這樣分；BeeCount 現況才是需要修正的一方）。

`UserAccountProjection`（`src/models.py:711-757`）完全沒有欄位記錄「這個
`account_group` 具體是信用卡群組還是銀行群組」，只能從 `credit_limit`/
`billing_day`/`payment_due_day`（723-725 行）這些欄位「有沒有值」反推。
`AccountListRow.tsx:166-173` 已經有一段用這個推斷法（`isCreditStyleGroup`）
判斷「這個群組的金額要不要用信用卡樣式（紅字待繳）呈現」，但只影響單一
列自己的色調/正負號，完全沒有回饋到 `computeTypeGroups` 的分組邏輯。

連帶問題：`LIABILITY_TYPES`（`frontend/packages/web-features/src/lib/
assetAggregation.ts:34`）目前是 `Set(['credit_card', 'loan'])`，
`account_group` 不在其中——代表就算修正了分組歸屬，掛信用卡子帳戶的主帳戶
群組小計目前仍會被當「資產」而非「負債」計入淨資產，這是同一個根因下的
連帶 bug，需要一併修正。

### 修改內容（純前端，不動 `account_type` 的資料庫實際值）

- 新增一個純函式（例如 `resolveAccountGroupDisplayType(group, childRows)`，
  放 `AccountListRow.tsx` 或 `assetAggregation.ts`）：
  1. 若這個 `account_group` 底下已有子帳戶（`parent_account_id` 指向它），
     取子帳戶的 `account_type`：全部同一種 → 用該種類型分組；混合多種類型
     → 保守 fallback 維持現況的獨立「主帳戶」分組（避免猜錯，需與使用者
     確認實務上是否真的會混掛，本 SD 先假設不會混掛，混掛是邊界情況）。
  2. 若群組底下還沒有任何子帳戶（剛建立），fallback 用既有的
     `isCreditStyleGroup` 推斷法（`credit_limit`/`billing_day`/
     `payment_due_day` 任一有值 → 信用卡分組；否則維持獨立「主帳戶」分組，
     等使用者掛上子帳戶後才會正確歸類——這是可接受的過渡狀態，不影響資料
     正確性，只影響新建當下的視覺分組）。
- `computeTypeGroups`（609 行）計算 bucket key 前，對 `account_type ===
  'account_group'` 的列先呼叫上述函式取得「顯示用類型」，取代直接使用字面
  值；分組的圖示/顏色（`TYPE_ICON_URL`/`TYPE_COLORS`，`AccountListRow.tsx:
  28-62`）也比照顯示用類型呈現。
- `LIABILITY_TYPES` 判斷（`assetAggregation.ts:34`、`computeCurrencySummary`
  69-78 行）需要同步使用「顯示用類型」而非原始 `account_type`，讓信用卡主
  帳戶群組的小計正確計入負債。
- `buildAccountChildrenMap`（`AccountListRow.tsx:95-113`）的巢狀縮排渲染
  邏輯不變——子帳戶依然縮排顯示在父列底下，只是父列現在會出現在正確的分組
  區塊裡。

### 測試

- 新增/擴充前端單元測試（`resolveAccountGroupDisplayType`/
  `computeTypeGroups`）：掛信用卡子帳戶的主帳戶歸類到信用卡分組且小計計入
  負債；掛銀行子帳戶的歸類到銀行分組；沒有子帳戶時退回既有的 credit-fields
  推斷；混合子帳戶類型時的 fallback 行為。
- 瀏覽器手測：比照使用者截圖情境，建立一個掛信用卡子卡的主帳戶，確認它出
  現在「信用卡」分組（不是獨立的「主帳戶」分組），分組標題徽章數字與小計
  金額（含正負號）正確。

---

## Phase 18：帳戶「納入總餘額」開關（需求 #4）

### Moze 官方文件調查結果

來源：[prepare/account/settings#balance-included](https://doc.moze.app/prepare/account/settings#balance-included)。

- 帳戶設定裡有一個獨立開關「納入總餘額」，關閉後這個帳戶的餘額不列入總覽
  的資產/淨值加總，但帳戶本身、其個別餘額顯示不受影響（跟「隱藏」是兩件
  事：隱藏管的是要不要出現在列表，這個開關管的是要不要計入總數）。
- 適用所有帳戶類型（一般帳戶、信用卡皆可關閉）。
- 文件特別提到：封存（archived）帳戶就算開著「納入總餘額」，也一樣會被計
  入總額——換句話說「封存」跟「納入總餘額」是兩個獨立維度，不會因為封存
  就自動排除在外，使用者要自己關掉這個開關才會被排除。

### 現況

BeeCount 已有的 `hidden` 欄位（`src/models.py:748-752`）是**同一個模式的先
例**，但語意不同：程式碼註解（748-749 行）明講「只影響前端選擇器/列表呈
現，服務端不做任何統計過濾」；`AccountsPanel.tsx:763-771` 也明確寫「帳戶隱
藏（issue #240）：淨資產 hero / 資產構成餅圖按 D1 用全量 rows 計算（隱藏不
改『錢在哪』）；只有『底部分組列表』拆成在用/已隱藏兩部分展示」——也就是
說**目前隱藏的帳戶仍然計入總資產/淨值**，這是刻意的既有設計決策（D1），跟
Moze「納入總餘額」這個「真的把餘額排除在總數外」的獨立開關是兩個不同概念，
BeeCount 目前完全沒有後者。

總額計算只有一處純邏輯來源：`computeCurrencySummary`（`assetAggregation.ts:
69-78`），被 `AccountsPanel.tsx:771-785`（`currencyBuckets`，驅動淨資產
hero 卡與資產構成圓餅圖）呼叫；跨幣別折算版 `mergeGroupsToBase`（109-139
行）也是疊在同一批 `groups`/`subtotals` 上。經 grep 確認 `computeCurrencySummary`/
`splitByCurrency` 在整個 `frontend/` 只有這一組呼叫鏈用到，沒有其它獨立算
淨資產的地方（若日後新增報表頁也算總資產，需要記得套用同一個過濾）。

### 修改內容

- **Schema**：`UserAccountProjection` 新增欄位 `include_in_total: bool`
  （`server_default=true()`，預設納入，向下相容——既有帳戶升級後預設維持
  現況「全部計入」的行為不變）。
- **7 步 SOP**（比照 `swipesmart_card_id`/`avatar_cloud_file_id` 當初新增
  欄位的改動點）：
  1. Migration：`user_account_projection` 新增 `include_in_total` 欄位。
  2. `src/projection.py::upsert_account`：`values` dict 補
     `"include_in_total": _as_bool(payload.get("includeInTotal"), default=True)`。
  3. `src/sync_applier.py`：帳戶 merge spec 加一組
     `("includeInTotal", "include_in_total")`。
  4. `src/routers/write/accounts.py`：建立/更新 schema 加欄位。
  5. Read Routers：帳戶讀取端點回傳這個欄位。
  6. **`src/snapshot_builder.py`**（⚠️ 最容易漏）：帳戶 SELECT 加這個欄位。
  7. 測試：`test_account_include_in_total_partial_update_keeps_existing_field`
     風格的 partial-update 契約測試。
- **前端**：
  - `AccountsPanel.tsx` 編輯表單比照既有「隱藏」開關（1205-1230 行附近）新
    增一顆雙生開關「納入總餘額」，預設開啟。
  - `currencyBuckets`（`AccountsPanel.tsx:771-785`）計算 `computeCurrencySummary`
    前，先用 `rows.filter(r => r.include_in_total !== false)` 過濾——這個
    過濾**獨立於**現有的 `visibleRows`/`hiddenRows` 切分（788-789 行的
    `listGroups` 仍然用 `visibleRows`，不受這次改動影響：一個帳戶可以「隱
    藏但仍計入總額」，也可以「顯示但不計入總額」，兩個開關互不耦合，對齊
    Moze 的「封存跟納入總餘額是兩個獨立維度」）。
  - `computeCurrencySummary` 函式本身簽章不變（純函式，過濾邏輯放在呼叫端
    組 `rows` 時做，不塞進這個函式內部，維持它原本「資產統計絕不跨幣種相
    加」單一職責的鐵律）。
  - 底部分組列表（`listGroups`）**不**因為 `include_in_total=false` 而消
    失或標記——這個帳戶依然正常顯示、正常可以記帳，只是不計入頂部總額，
    比照使用者需求「不想被統計在總額裡」的字面意思，不是把帳戶隱藏或停用。
  - 待實作時 grep 一次確認沒有其它獨立算「總資產/淨值」的頁面遺漏（目前
    確認只有 `AccountsPanel.tsx` 這一處，但改動前建議重新 grep 一次以防
    這段期間新增了報表頁）。

### 測試

- 後端：partial-update 契約測試（SOP 第 7 點）。
- 前端 `pnpm test:unit`：`computeCurrencySummary`/`currencyBuckets` 過濾邏
  輯的單元測試（關閉開關的帳戶不計入 `assetTotal`/`liabilityTotal`/
  `netWorth`，但仍出現在分組列表裡）。
- 瀏覽器手測：關閉某帳戶的「納入總餘額」，確認頂部淨資產卡片/圓餅圖數字
  減少對應金額，但該帳戶仍正常出現在下方分組列表、仍可正常記帳。

---

## Phase 19：帳戶選擇器全站統一 + 回饋帳戶預設值（需求 #7、#8）

### 現況

`AccountPickerDialog.tsx`（`frontend/packages/web-features/src/components/`）
目前**只有一個消費端**：`TransactionsPanel.tsx`（帳戶/轉出/轉入三處，
428-430、875、889、905 行）。全站其餘所有「選帳戶」的地方都還是原生
`<Select>` 下拉：

| 檔案 | 行號 | 用途 |
|---|---|---|
| `frontend/apps/web/src/components/dialogs/CardRewardRulesSection.tsx` | 1146-1161 | 新增/編輯回饋規則的「回饋帳戶」選擇 |
| 同上 | 1312-1327 | 手動入帳的「入帳帳戶」選擇 |
| `frontend/packages/web-features/src/features/RecurringRulesPanel.tsx` | 298-327、354-368 | 週期規則的帳戶/轉出/轉入選擇 |
| `frontend/packages/web-features/src/features/TxTemplatesPanel.tsx` | 320-332、350-362、394-408 | 範本的帳戶/轉出/轉入選擇 |
| `frontend/packages/web-features/src/features/InstallmentPlansPanel.tsx` | 420-439 | 分期付款的帳戶選擇 |
| `frontend/packages/web-features/src/features/DebtsPanel.tsx` | 352-369 | 欠還款的還款帳戶選擇 |

CLAUDE.md 已經記錄 Phase 8 §16 當時的規劃「帳戶選擇欄位比照 Phase 11」，但
**實際上沒有做**——`CardRewardRulesSection.tsx` 兩處都還是原生下拉，這次一
併補上。

`CardRewardRulesSection.tsx` 的 `rewardAccountId` 狀態初始化
（696 行）：`useState(source?.reward_account_id || '')`——新增規則
（`source` 為 `null`）時固定是空字串，沒有任何預設值；選單本身只有在
`a.id === accountId`（目前正在檢視的這張卡自己）時才在顯示文字加註
「（本卡）」後綴（1155-1157 行），沒有真的預先選中它。

### 修改內容

- 統一改用 `AccountPickerDialog`，取代上表 6 個檔案共 8 處的原生
  `<Select>`。轉出/轉入各自開一個獨立的彈窗實例，行為比照
  `TransactionsPanel.tsx` 既有用法。
- `CardRewardRulesSection.tsx` 兩處新增 `selectMode`/既有 prop 沿用
  （`AccountPickerDialog` 已支援 `selectMode`/`hiddenBadge`，CLAUDE.md
  Phase 11 補充）；帳戶群組（`account_group`）在這個情境維持「點擊只展開
  不選中」的既有規則（回饋帳戶只能是實際帳戶，不能是純管理容器）。
- **回饋帳戶預設值**（需求 #8）：`rewardAccountId` 初始化改成
  `useState(source?.reward_account_id || accountId || '')`——新增規則時，
  若目前是從某張卡的詳情頁開啟這個表單（`accountId` 有值），直接預選這張
  卡自己當回饋帳戶，使用者仍可透過彈窗改選別的帳戶，只是省去「大多數情況
  下回饋就是入到卡片自己身上」這個最常見情境的手動選擇步驟。
- 手動入帳的「入帳帳戶」選擇（1312-1327 行）比照同樣邏輯：預設帶入該規則
  已設定的 `reward_account_id`（而非空白），使用者仍可改選。
- 其餘 5 個檔案的帳戶選擇器改用 `AccountPickerDialog` 後，維持各自原本的
  預設值邏輯不變（本 Phase 只統一 UI 元件、不改動這 5 處既有的預設值行
  為，避免範圍發散）。

### 測試

- 前端 `pnpm test:unit`（若既有元件有對應單元測試，同步更新斷言）。
- 瀏覽器手測：6 個檔案、8 處帳戶選擇入口逐一操作過一遍，確認彈窗樣式/搜尋
  /分組巢狀顯示正常；新增回饋規則時從卡片詳情頁開啟，確認回饋帳戶已預選
  該卡；手動入帳彈窗預選該規則的回饋帳戶。

---

## Phase 20：新增記帳表單版面重排 + 金額自動定位（需求 #6 前半）

### 現況

`TransactionsPanel.tsx` 主要新增交易表單（565 行起）目前欄位順序：帳本選
擇（572-586）→ 交易類型下拉（587-599，**目前是 `<Select>` 下拉，不是分頁
籤**）→ 金額+幣別+手續費/折扣（600-703）→ 時間+商家（704-728，兩者疊在同
一個 grid cell）→ 分類（730-866，圖示網格彈窗）→ 帳戶/轉出轉入（868-914）
→ 備註（916-927）→ 回饋規則勾選（929-993）→ SwipeSmart 建議刷卡（995-1057）
→ 欠款關聯（1059-1135）→ 專案（1137-1154）→ 標籤（1156-1198）→ 延後入帳日
期（1205-1219）→ 週期性/分期開關（1220 起）。

對照使用者提供的 Moze 參考圖：記錄類型（支出/收入/轉帳/應收/應付）是**頁籤
式**排在最上方 → 類別圖示網格 → 金額+幣種 → 帳戶+專案（同一列） → 商家+進
階設定 → 日期+時間 → 標籤/備註。現況差異：①類型是下拉不是頁籤；②分類排
在金額/時間之後，不是最前面；③商家跟時間疊在一起，不是跟「進階設定」放
一起；④帳戶排在備註之前、不是跟專案並列；⑤開啟表單時金額框沒有自動
focus——`TransactionsPanel.tsx` 全檔案（1720 行）grep `autoFocus`/`.focus()`
/`inputRef` 均為零筆，`Dialog` 的 `open`/`setOpen`（328-329、565 行）沒有
任何 `useEffect` 呼叫 focus，`AmountInput`（`@beecount/ui`）元件本身也沒有
內建 `autoFocus`。

### 修改內容（純前端，僅版面/焦點調整，不動欄位資料結構）

- 交易類型（587-599）改成頁籤樣式（比照 Moze 參考圖「支出/收入/轉帳/應收
  款項/應付款項」五個分頁籤），取代下拉選單，視覺上放在表單最上方。
- 分類選擇（730-866）搬到類型頁籤正下方（金額之前），對齊 Moze「先選類別
  再輸入金額」的操作順序——金額輸入框此時仍在使用者視線內，不影響輸入體
  驗，只是操作順序調整。
- 帳戶（868-914）與專案（1137-1154）合併到同一列並排顯示，搬到分類/金額
  之後、商家之前。
- 商家（原 704-728 與時間疊在一起）搬到帳戶/專案之後，跟既有的「商家」進
  階設定概念放在同一區塊（可考慮這裡順帶收斂一個「進階設定」可折疊區塊，
  容納商家/單次記錄等次要欄位，但這屬於錦上添花，非必要，先以「順序調
  整」為主，是否要做可折疊區塊留待實作時視工時決定）。
- 日期+時間搬到表單尾端（貼近儲存按鈕之前），標籤/備註維持在最後一段。
- 開啟新增交易 Dialog 時，金額輸入框自動取得 focus：`useEffect` 監聽
  `open` 由 `false→true`（或使用 `Dialog` 的 `onOpenAutoFocus`，需確認
  shadcn `Dialog` 是否已提供這個 callback，若有優先用內建機制而非自訂
  `useEffect`+`ref`），聚焦到金額 `<Input>`；**編輯既有交易**時不強制清空
  金額重新輸入，仍然 focus 到金額框但保留原有數值全選（方便使用者直接輸
  入新數字覆蓋），對齊一般表單「開啟就能直接打字」的體驗。
- `GlobalEditDialogs.tsx`（另一份獨立維護的編輯表單邏輯）是否需要比照同樣
  的版面/focus 調整——**待與使用者確認**：若編輯彈窗維持現況版面、只有
  「新增」表單改版，兩份表單長相會不一致；若要求一致，`GlobalEditDialogs.tsx`
  需要重複同樣的搬移工作（CLAUDE.md 已記錄過這兩處是分別維護、需同步改的
  既有模式）。本 SD 先假設**兩處都要改**（維持既有「兩處同步」的慣例），
  實作時如工時吃緊可與使用者確認是否可以先only改新增表單。

### 測試

- 前端 `pnpm build`/`pnpm test:unit`。
- 瀏覽器手測：開啟新增交易，確認金額框自動 focus 且游標/選取狀態方便直接
  輸入；欄位順序比照 Moze 參考圖走一遍（類型頁籤→分類→金額→帳戶+專案→商
  家→日期時間→標籤備註）；編輯既有交易時版面同步、金額框內容可直接覆寫；
  桌面與手機寬度都要看過。

---

## Phase 21：分類/帳戶智慧推薦（需求 #6 後半）

**依賴 Phase 20**：兩者改同一段表單區域，Phase 20 先完成並過瀏覽器手測後
再開始，避免行號互相偏移、同一 session 內反覆讀檔案。

### 現況

`CategorySelector.tsx`（376 行）grep「recommend/frequent/recent/最近/常
用/建議」全部零筆——除了「已選分類自動展開父層」與「子字串搜尋」之外沒有
任何使用頻率排序或最近使用排序。`TransactionsPanel.tsx` 除了跟分類/帳戶推
薦完全無關的 SwipeSmart 刷卡建議（386-407 行 debounce fetch，996-1057 行渲
染，只吃金額+商家，不吃分類）以外，沒有任何依「分類→帳戶」或「情境→分
類」的建議邏輯，`AccountPickerDialog.tsx`（194 行）純粹是搜尋+分組的選擇
器，沒有記憶/排序邏輯。

### 修改內容

**分類推薦（新增記帳一打開就看到「常用分類」排前面/加註記）**

- 新增後端唯讀端點 `GET /ledgers/{id}/category-suggestions?tx_type=&account_id=&hour=`
  （放 `src/routers/read/ledgers.py` 或 `workspace.py`，比照既有唯讀彙總端
  點慣例），依下列訊號加權排序回傳 `category_id` 清單（例如取前 8-10 名）：
  1. **同一使用者、同 `tx_type` 的整體使用頻率**（基礎分數，越常用分數越
     高）。
  2. **同一時段**（同小時區間 ±1~2 小時，或粗分早/午/晚/深夜四個時段）過
     去記過的分類加權（對應使用者原話「同時間這個使用者曾經記的帳」）。
  3. **同一帳戶** 過去記過的分類加權（`account_id` 有帶入時，篩選/加權該
     帳戶歷史交易的分類分布，對應「這個帳戶曾經記的帳」）。
  4. **時間衰減**：近期交易權重高於久遠交易（例如簡單的「最近 N 筆」或
     「最近 90 天」窗口，避免半年前的一次性分類長期佔據推薦位置——具體衰
     減公式留待實作時依實際資料量調校，不在 SD 階段定死參數）。
  - 三種訊號（整體頻率／同時段／同帳戶）的加權比例、以及要不要做成使用者
    可調整的設定，**待實作時依實測效果決定**，本 SD 先定調「以上三種訊號
    都要涵蓋，具體公式可迭代」，不鎖死精確權重。
- `CategorySelector.tsx`：接受一個可選的 `suggestedCategoryIds?: string[]`
  prop，命中的分類在圖示網格裡加註「常用」徽章或直接排到最前面（比照現有
  網格排版微調，不整個重做元件結構）；`TransactionsPanel.tsx` 開啟新增交
  易時打這支新 API（帶入目前 `tx_type`/已選 `account_id`/當下小時），結果
  傳入 `CategorySelector`。

**依分類帶入常用帳戶（選了分類後，自動建議近期用這個分類記過的帳戶）**

- 沿用同一支或新增一支端點（例如 `GET /ledgers/{id}/account-suggestions?
  category_id=`），依「該分類最近一次/最常使用的帳戶」排序回傳 `account_id`
  清單。
- `TransactionsPanel.tsx`：使用者選定分類後（`onCategorySelect` 之類的既
  有 callback），若目前帳戶欄位**尚未被使用者手動選過**（避免覆蓋使用者
  已經自己選好的帳戶——比照現有「原始帳戶名」相容邏輯的既有分寸感，只在
  空白/未觸碰時才自動帶入建議值，不強制覆蓋），自動預選建議帳戶清單裡的
  第一名；使用者仍可透過 `AccountPickerDialog`（Phase 19 已統一）自行改
  選。
- 這兩支新端點都是唯讀彙總查詢，不寫入任何資料，不影響現有 sync entity 結
  構，不適用 CLAUDE.md 7 步 SOP。

### 測試

- 後端：`tests/` 新增 `category-suggestions`/`account-suggestions` 端點測
  試（涵蓋：同時段加權生效、同帳戶加權生效、無歷史資料時回傳空陣列不報
  錯）。
- 前端 `pnpm test:unit`：`CategorySelector` 帶入建議清單後的排序/徽章渲
  染。
- 瀏覽器手測：模擬使用者在同一帳戶/同一時段多次記某個分類後，新增交易時
  確認該分類確實被推薦到前面；選定分類後確認帳戶欄位自動帶入近期常用帳
  戶，且使用者手動改選後不會被推薦邏輯覆蓋回去。

---

## Phase 22：信用卡回饋「自然月」跨帳單週期顯示（需求 #2）

### 現況根因

`interval` 欄位（`ReadCardRewardRuleProjection`，`src/models.py:1191`，值
`"billing_cycle"`/`"calendar_month"`，前端顯示文案「帳單週期」/「自然
月」，`zh-TW.ts:794-795`）決定回饋計算要用帳單週期還是自然月當「一期」。
`_resolve_period`（`src/services/card_rewards.py:263-285`）**永遠只回傳單
一個 `(start, end)` 區間**：`billing_cycle` 分支透過
`credit_card.billing_cycle_containing`/`shift_cycle` 算出帳單週期；
`calendar_month` 分支透過 `_calendar_month_containing`/`_shift_calendar_month`
（228-237 行）算出**單一**自然月，完全不管帳單週期實際橫跨了幾個自然月。

前端統一用同一個 `period_offset` 查詢參數（`src/routers/read/ledgers.py:
896` 等處）驅動導覽——docstring（896-909 行附近）講明這個 offset 的語意是
「帳單週期位移」（`period_offset = cycleOffset - 1`）。當規則是
`calendar_month` 時，這個「帳單週期位移」被直接套用到 `_shift_calendar_month`
上，語意錯位：使用者在瀏覽 7/12~8/11 這期帳單時，畫面只會算出「這個
offset 對應到的那一個自然月」（通常是 8 月），7/12~7/31 這段完全沒有被算
到、也沒有顯示——這就是使用者截圖情境「只看得到 8 月，看不到 7 月」的根
因。

前端 `CardRewardRulesSection.tsx` 目前也沒有渲染期間起訖的位置可用：
`renderRuleRow`（412-526 行）在 `status === "ok"` 分支（512-522 行）只顯示
`qualifying_spend`/`capped_reward`，完全不顯示期間；`activePeriodText`
（91-101 行）顯示的是規則本身的起訖日（`starts_at`/`ends_at`），跟「這次
算的是哪個週期」是兩件事，容易混淆。詳情彈窗（`CardRewardRuleTransactionsDialog`，
1200 行起）同樣只在 `status === "expired"` 分支才顯示期間（1380-1385
行），`ok` 分支（1386-1399 行）只顯示 `capped_reward`/`remaining_reward_room`。

「可以刷多少金額」這個需求目前也完全沒有對應計算：`apply_caps`
（606-642 行）跟 `list_rule_qualifying_transactions`（657-736 行）只算
`remaining_reward_room`（723-729 行，回饋金額的剩餘額度），沒有任何「反推
還可以刷多少消費金額才會頂到這個回饋上限」的邏輯。

### 修改內容

**後端**

- 新增 `_resolve_periods`（複數，取代/疊加在 `_resolve_period` 之上，保留
  舊函式名稱給只需要單一期間的既有呼叫點相容，或直接讓舊函式改呼叫新函式
  取第一筆——實作時視既有呼叫點數量決定要不要保留相容 shim）：
  - 統一先算出「使用者目前在瀏覽的帳單週期視窗」（沿用既有
    `credit_card.billing_cycle_containing`/`shift_cycle`，因為前端導覽本
    來就是帳單週期概念，不分規則 `interval` 為何，這是使用者體感上「正在
    看哪一期帳單」的唯一導覽軸）。
  - 若 `rule.interval == "billing_cycle"`：回傳單一元素清單
    `[(cycle_start, cycle_end)]`，行為與現況完全相同。
  - 若 `rule.interval == "calendar_month"`：把上述帳單週期視窗**拆分**成
    這個視窗涵蓋到的每一個自然月子區間（例如 7/12~8/11 拆成
    `(7/1, 7/31)` 與 `(8/1, 8/31)` 兩個**完整自然月**——不是只取視窗內的
    那幾天，因為「自然月」規則的回饋上限本來就是以整個月為單位重置，使用
    者需要看到每個月完整的回饋/上限數字，不是被帳單視窗切掉一截的片段數
    字），回傳這個清單（1~2 筆，視帳單日落在月中哪一天而定，理論上一個
    帳單週期最多橫跨 2 個自然月，因為帳單週期長度通常近似一個月）。
- `compute_account_card_rewards`（520-603 行）與
  `list_rule_qualifying_transactions`（657-736 行）改成對 `_resolve_periods`
  回傳的**每一個**期間各自跑一次既有計算邏輯（`qualifying_spend`/
  `capped_reward`/`remaining_reward_room`），組成一個列表回傳（`ReadCardRewardsOut`/
  `ReadCardRewardRuleTransactionsOut`，`schemas.py:833-874`，新增一個
  `periods: list[...]` 欄位或改成 API 直接回傳陣列，取代現有的單一物件
  ——需要決定是否為了向下相容，`billing_cycle` 規則維持原本單物件 response
  shape，只有 `calendar_month` 規則多期間時才用陣列包起來；或乾脆兩種規則
  統一都用「陣列（至少 1 筆）」的 response shape，前端一致處理——**建議統
  一用陣列**，前端邏輯較單純，只是要注意這是一個 API contract 的 breaking
  change，需要前後端同一個 PR 一起改完，不能分開部署）。
- **可刷金額上限（新增計算）**：新增一個「剩餘可刷額度」欄位
  `remaining_spend_room`，只在 `rule.rate_type` 是百分比類（例如
  `"percentage"`，具體字面值以 `card_rewards.py` 現有 `rate_type` 判斷邏
  輯為準，實作時核對）時才有意義且計算：
  `remaining_spend_room = remaining_reward_room / (rate_value / 100)`
  （`rate_value` 現有語意見 CLAUDE.md「貨幣符號單一來源」段落旁的既有換算
  慣例）；`rate_type` 是固定金額類（例如 `"fixed_amount"`，每筆消費固定回
  饋一筆固定金額，不是按比例）時，「還能刷多少錢」這個概念不成立（跟消費
  金額無關，只跟符合資格的**筆數**有關），此欄位回傳 `None`，前端對應隱
  藏這個顯示，不硬湊一個誤導數字。
- 各自算完（billing_cycle 一筆、calendar_month 一或兩筆）後，`remaining_spend_room`
  ——依 `calendar_month` 拆分後的**每個自然月各自獨立計算**，不合併，對
  齊使用者需求「兩個月要分開算，而非算在一起」。

**前端**

- `CardRewardRulesSection.tsx` 的 `renderRuleRow`（412-526 行）與
  `CardRewardRuleTransactionsDialog`（1200 行起）改成迭代渲染
  `periods` 陣列——`calendar_month` 且橫跨兩個月時渲染兩張並列/堆疊的小
  卡片（各自標示月份、`qualifying_spend`/`capped_reward`/
  `remaining_reward_room`/`remaining_spend_room`），`billing_cycle` 規則
  維持現況單一區塊呈現（陣列只有一筆時的自然表現，不需要特殊分支）。
  - 每張期間卡片明確標示期間起訖（例如「7月（7/1~7/31）」/「8月
    （8/1~8/31）」），修正現況「完全不顯示計算期間、只在過期分支才顯示」
    的既有落差（見上面現況根因段落）。
- `remaining_spend_room` 為 `None`（固定金額類規則）時，UI 隱藏「還可以刷
  XX 元」這一行，不顯示、不留空白佔位。

### 測試

- `tests/test_card_rewards.py`（或現有對應檔案）擴充：`calendar_month` 規
  則、帳單週期橫跨兩個自然月時，`_resolve_periods` 回傳剛好兩筆完整自然月
  區間；`billing_cycle` 規則維持回傳一筆、數值與現況一致（回歸測試，避免
  改動破壞既有行為）；`remaining_spend_room` 百分比規則計算正確、固定金額
  規則回傳 `None`；兩個自然月的 `remaining_spend_room`/`remaining_reward_room`
  各自獨立、不互相污染。
- 依 CLAUDE.md SOP 檢查 response schema 改動是否影響其它既有呼叫點（例如
  Phase 15 SwipeSmart 一鍵記帳若有讀取這幾個欄位，需要確認相容）。
- 瀏覽器手測：比照使用者原始情境（帳單日落在月中、跨兩個自然月的
  `calendar_month` 規則），確認畫面同時顯示 7 月與 8 月兩份明細，且金額各
  自獨立、可刷額度各自獨立計算。

---

## Phase 23：SwipeSmart 使用額度回填正確性（需求 #3，依賴 Phase 22）

### 現況根因

實際回填邏輯在 `src/services/swipesmart_backfill.py`：

```python
# _backfill_one_user, 82-103 行附近
schedule = card_rewards.resolve_billing_schedule(db, account=account)
...
cycle_start, cycle_end = credit_card.billing_cycle_containing(now.date(), billing_day)
...
transactions = _collect_cycle_transactions(
    db, user_id=user.id, account_sync_id=account.sync_id, ledger_ids=owned_ledger_ids,
    cycle_start_dt=cycle_start_dt, cycle_end_dt=cycle_end_dt,
)
ok = await swipesmart_client.recompute_usage(api_key, card_id=card_id, transactions=transactions)
```

**問題 1（週期算錯）**：這段邏輯完全不管該帳戶掛的 `card_reward_rules`
的 `interval` 欄位，永遠用 `credit_card.billing_cycle_containing` 算帳單
週期——就算規則是 `calendar_month`，回填視窗依然是帳單週期，不是自然月，
跟 Phase 22 要修的顯示端是同一個根因（`_resolve_period`/`_resolve_periods`
沒被這裡呼叫，回填是另一條完全獨立、自己重算週期的路徑）。

**問題 2（沒有分類過濾）**：`_collect_cycle_transactions`（34-50 行）撈這
個帳戶在時間窗內**所有** `tx_type == "expense"` 的交易（只取
`amount`/`merchant`），不管交易的分類是否真的落在任何一條回饋規則的
`category_sync_ids_json` 篩選範圍內，全部一起送給 SwipeSmart
`POST /api/user/usages/recompute`。

**確認使用者的猜測（SwipeSmart 上限粒度）**：`docs/EXTERNAL_INTEGRATION_SPEC.md:214-216`
明確記載「SwipeSmart 的 `usedCapAmount` 是以『卡片』為單位加總，不分類
別」——`CapAmount`/`UsedCapAmount` 在 SwipeSmart 端**只有卡片粒度**，沒有
拆分到分類/回饋率層級。反觀 BeeCount 這邊 `ReadCardRewardRuleProjection`
（`src/models.py:1160-1215`）的 `cap_amount`/`cap_shared_key` 是**規則
（分類/費率）粒度**，一張卡可以掛多條不同上限/費率的規則（例如使用者截
圖「玉山 U Bear：網路 3% 娛樂 12%」）。**這代表使用者問的「依回饋率找到
對應的回饋率再回填」在現有 SwipeSmart 資料模型下做不到**——SwipeSmart 沒
有地方可以承接「這是屬於哪一條規則/哪個費率的用量」，要嘛全部合併回填成
一個卡片總量（現況），要嘛需要 SwipeSmart 端新增「per-category/per-rule
cap 追蹤」的資料模型（不在 BeeCount 這邊能單方面解決，需要另外排入
SwipeSmart 自己的開發排程，列在下方風險段落）。

### 修改內容

**問題 1 修正（可獨立於 SwipeSmart 端改動，本階段就能做完）**：

- `_backfill_one_user`（`swipesmart_backfill.py:53`）改成：對該帳戶，撈出
  目前生效中（`enabled=True` 且未過期）的 `card_reward_rules`；
  - 若帳戶沒有任何生效中的規則，維持現況用帳單週期回填（沒有規則可參
    考，帳單週期是唯一有意義的預設）。
  - 若帳戶所有生效規則的 `interval` 一致，直接沿用 Phase 22 新做的
    `_resolve_periods` 取得正確的期間清單（`billing_cycle` → 一筆；
    `calendar_month` → 一或兩筆自然月），**每個期間各自呼叫一次**
    `swipesmart_client.recompute_usage`（因為 SwipeSmart 的
    `/api/user/usages/recompute` 語意是「這一次呼叫代表這一期的用量」，
    自然月拆成兩期就要分兩次呼叫，不能合併成一次送兩個月混在一起的總
    量，維持跟 Phase 22 顯示端「兩個月分開算」同一個原則）。
  - 若帳戶同時存在 `billing_cycle` 與 `calendar_month` 兩種 interval 的規
    則（同一張卡掛多條規則、`interval` 不一致）：**待實作時與使用者確
    認**——本 SD 建議的預設策略是「以每一種 interval 各自算一次、各自回
    填」（因為 SwipeSmart 端本來就是卡片總量單一數字，多次呼叫同一張卡不
    同期間會互相覆蓋，最後生效的是最後一次呼叫——這種情況下回填的準確性
    本來就有先天限制，優先權建議給使用者最近一次瀏覽/使用頻率較高的
    interval，或乾脆兩種都定期各跑一次讓數字保持「大致新鮮」）。

**問題 2 部分緩解（無法完全解決，見下方風險）**：

- `_collect_cycle_transactions`（34-50 行）新增分類過濾：只收集分類落在
  「該帳戶任一條生效規則的 `category_sync_ids_json`」範圍內的交易（若某條
  規則沒有設定分類篩選 = 適用所有分類，則該帳戶不過濾，維持現況全收）。
  這只能做到「排除完全不合資格拿回饋的消費」，**做不到**「精準對應到某一
  條規則/某個費率」，因為 SwipeSmart 端沒有承接這個維度的地方（見上方確
  認段落）。

### 風險與待確認事項

- **SwipeSmart 端待開發**：若要真正做到「依回饋率精準回填」，SwipeSmart
  需要把 `CapAmount`/`UsedCapAmount` 從卡片粒度擴充成卡片+分類/規則粒度
  ——這是 SwipeSmart 自己的資料模型與 API 改動，不在本次 BeeCount 改動範
  圍內，需要另外跟 SwipeSmart 那邊排開發（比照 `docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md`
  §3.2 Path B 那種「列清楚需求、留給 SwipeSmart 自己排程」的處理方式）。
  本階段完成後，BeeCount 這邊能做到的最佳狀態是：正確的週期窗口（帳單週
  期 vs 自然月分開算）+ 排除完全不合資格分類的消費，但同一張卡上多條不同
  費率規則的用量仍然只能合併成一個數字回填。
- 同一帳戶多條規則 `interval` 不一致時的處理策略（見上方「待實作時與使用
  者確認」）需要在實作前定案。

### 測試

- `tests/test_swipesmart_backfill.py` 擴充：`calendar_month` 規則的帳戶回
  填時，正確拆成兩次呼叫、各自帶對應自然月的交易；`billing_cycle` 規則行
  為維持現況（回歸測試）；分類過濾生效（不合資格分類的消費不出現在送出的
  `transactions` 清單裡）；沒有任何生效規則時 fallback 帳單週期（回歸）。
- 手測（若有可連線的 SwipeSmart 測試環境）：實際跑一次回填，核對 SwipeSmart
  後台收到的用量數字符合預期窗口。

---

## Phase 24：週期性收支編輯範圍選擇（需求 #5）

### Moze 官方文件調查結果

來源：[record/recurring](https://doc.moze.app/record/recurring)。編輯單筆
生成的週期交易時，使用者可以選「只修改此筆記錄」（只影響這一筆，比照使用
者截圖 Image #3 的彈出選單）或「修改連同未來週期」（這筆與往後所有未發生
的週期一起套用新設定，例如電話費調漲後，往後每期都要用新金額）。另外還有
「終止未來週期」的第三種操作（刪除/停用往後的週期），現有 BeeCount 已有對
應端點（`terminate-future`），不在本次需求範圍內、不用新增 UI。

### 現況根因

後端骨架其實已經存在，只是**沒有從使用者實際會用到的入口接起來**：

- `PATCH/DELETE /ledgers/{id}/recurring-rules/{rule_id}/occurrences/{tx_id}`
  （`src/routers/write/recurring_rules.py:216-312`）——「只改這一筆」，會
  正確設定 `recurring_occurrence_overridden=True`（250 行），避免之後的批
  次更新覆蓋掉這筆手動修改。
- `POST /recurring-rules/{rule_id}/update-from/{tx_id}`（315-405 行）——
  「連同未來週期」，更新規則本身 + 所有 `happened_at >= anchor` 且尚未被
  單獨覆蓋（`recurring_occurrence_overridden=False`）的已生成交易列。

**問題 A（入口沒接上）**：`TransactionsPage.tsx:2066`、
`GlobalEditDialogs.tsx:684,697-699` 這兩處實際的交易編輯入口，目前對任何
交易一律呼叫**一般**的 `updateTransaction`（`src/routers/write/transactions.py:
181-237`），完全沒有檢查該交易的 `recurring_rule_id`（`schemas.py:570`，
讀取端已經會回傳這個欄位，前端拿得到資料，只是沒有拿來做判斷）。也就是
說：使用者從「交易列表」正常點開一筆週期產生的交易去編輯，永遠走的是**既
有那條完全沒有選擇、也不會設定 `recurring_occurrence_overridden` 的純標準
PATCH**——連「只改這一筆」該做的 override 標記都沒設，代表現在只要之後任
何一次「連同未來週期」批次更新掃過這筆交易，會**靜默覆蓋掉使用者原本手動
改過的內容**，這是目前實際存在的資料一致性風險，不只是「少一個選單」而
已。

真正有「選擇範圍」UI 的地方目前只有 `RecurringRulesPanel.tsx:727-795`（展
開規則詳情裡的個別項目列表才有「編輯」/「連同未來」兩顆按鈕），使用者要
先去週期事件管理頁面才摸得到，不是使用者截圖裡「直接點交易→跳選單」的體
驗。

**問題 B（「連同未來」欄位不完整）**：`update_recurring_rule_from_ep`
（315-405 行）與 `WriteRecurringUpdateFromRequest`（`schemas.py:1648-1658`）
目前只轉發 `tx_type`/`amount`/`note`/`category_id`/`frequency`/`interval`/
`advanced_rule_json`（規則本身）與 `tx_type`/`amount`/`note`/`category_id`/
`account_id`（各筆交易）——**規則自己明明已經有的 `account_id`/
`from_account_id`/`to_account_id` 欄位（`models.py:845-893`，`RecurringRule`
定義）都沒有被轉發**，這是單純的既有 bug（欄位已存在，只是沒接進這支
API），比對到規則模型本身：`RecurringRule` **完全沒有** `merchant`/
`tags`/`project_id`/`currency`/`fee`/`discount`/`splits`/`debt_id`/
`reward_rule_ids` 這些欄位——換句話說，就算補齊轉發邏輯，「連同未來週期」
能涵蓋的欄位上限就是 `RecurringRule` 這個 entity 本身有的欄位，這些額外欄
位是 entity 層級本來就不存在，不是轉發邏輯漏掉。

### 修改內容

**前端：編輯入口加選擇彈窗**

- `TransactionsPage.tsx`/`GlobalEditDialogs.tsx` 開啟編輯時，先檢查該交易
  是否有 `recurring_rule_id`：
  - 沒有 → 行為不變，直接開一般編輯表單。
  - 有 → 先彈出比照使用者截圖 Image #3 的選擇彈窗「修改此記錄」/「修改連
    同未來週期」，使用者選完才開編輯表單。
- 「修改此記錄」：使用者存檔時呼叫既有的
  `PATCH .../occurrences/{tx_id}`（而不是目前用的一般 `updateTransaction`），
  正確設定 override 旗標，順帶修正上面「問題 A」提到的資料一致性風險。
- 「修改連同未來週期」：使用者存檔時呼叫既有的
  `update-from/{tx_id}`（`updateRecurringRuleFrom`），以這筆交易當 anchor。
- 兩個入口共用同一個編輯表單 UI（沿用 Phase 20/21 改版後的表單），差別只
  在存檔時呼叫哪一支 API——不需要為週期交易另外做一份表單。

**後端：補齊「連同未來週期」的欄位轉發（問題 B 第一層，不擴充 entity）**

- `update_recurring_rule_from_ep`（315-405 行）的規則 payload 補上
  `account_id`/`from_account_id`/`to_account_id`（`RecurringRule` 已有的
  既有欄位，純粹修正轉發遺漏，不需要 migration）；per-tx payload 同步補
  這三個欄位。

**後端：`RecurringRule` entity 擴充（問題 B 第二層，需要與使用者確認範
圍）**——**待與使用者確認是否要做，做到什麼程度**：

- 使用者原話「連同未來，未來的也要跟著動（所有設定）」，字面上涵蓋商家/
  標籤/專案等欄位，但這些欄位目前 `RecurringRule` entity 完全沒有。本 SD
  建議的範圍是：新增 `merchant`/`project_id`/`tag_sync_ids_json`（比照既
  有交易的標籤欄位形狀）三個欄位到 `ReadRecurringRuleProjection`，讓「未
  來週期」也能涵蓋商家/專案/標籤——這三者概念上比較貼近「重複性交易的固
  定屬性」。
  - `currency_code`/手續費折扣/拆帳/欠款關聯/回饋規則勾選，這些概念更貼
    近「每一筆交易當下的獨立決定」，不建議塞進週期規則的範本欄位（例如欠
    款關聯——不會每期都連到同一筆欠款；拆帳明細——每期實際花費組成可能不
    同），本 SD 建議**排除在外**，維持現況「這些欄位只能逐筆自己改，不會
    被『連同未來』批次覆蓋」，需要與使用者確認這個範圍是否可接受。
  - 若使用者確認要擴充，新增的 3 個欄位需要走完整 7 步 SOP（DB migration、
    `projection.py`、`sync_applier.py`、write router、read router、
    ⚠️`snapshot_builder.py`、partial-update 測試），且 `update-from` 端點
    同步補這三個欄位的轉發。

### 測試

- 後端：`tests/`（`test_recurring_rules.py` 或相關檔案）新增：規則/per-tx
  payload 補齊 `account_id`/`from_account_id`/`to_account_id` 轉發後，
  「連同未來週期」正確更新這些欄位；`terminate-future`/`occurrences` 既有
  測試維持綠燈（回歸）；若擴充 entity，補 partial-update 契約測試。
- 前端 `pnpm test:unit`：編輯入口依 `recurring_rule_id` 有無正確分流（有
  → 彈選擇視窗；沒有 → 直接開編輯表單）。
- 瀏覽器手測：比照使用者截圖情境——編輯一筆週期產生的交易，跳出「修改此
  記錄」/「修改連同未來週期」選單；選「此記錄」只影響這一筆，之後規則批
  次更新不會覆蓋它；選「連同未來週期」後，往後尚未發生的週期交易的帳戶/
  金額/分類等欄位（含本次擴充的欄位，若有做）全部同步更新。

---

## 共用注意事項（套用到每個 Phase）

- 每個 Phase 做完都要跑 `pytest tests/ -q`（全量）+ 有牽動前端的話跑
  `pnpm build`/`pnpm test:unit`，**然後才進到瀏覽器手測**——不能只憑自動化
  測試過就宣告完成（CLAUDE.md 鐵律）。
- Phase 18（帳戶欄位）、Phase 22（回饋規則回傳格式）動到既有 sync
  entity/API contract 時，務必照 CLAUDE.md「新增或修改 Sync Entity 檢查
  清單」7 個位置逐一確認，`snapshot_builder.py` 最容易漏。
- Phase 之間如果發現前一個 Phase 的行號因為改動而偏移，動工前先重新 Read
  一次相關檔案確認行號，不要直接依賴本文件寫的行號動刀。
- 建議實作順序：Phase 17 → 18 → 19（三者互相獨立、風險低，可任選順序）→
  Phase 20 → 21（有依賴，20 先做完）→ Phase 22 → 23（有依賴，22 先做完，
  建議合併在同一次 session 完成，避免同一批檔案來回讀）→ Phase 24（獨
  立，可穿插在任何時間點做）。
- 本文件中標示「待實作時與使用者確認」/「待與使用者確認」的段落（Phase
  17 混合子帳戶類型的 fallback、Phase 20 是否兩份表單都要改版、Phase 22
  API contract 是否統一用陣列、Phase 23 多條規則 interval 不一致的回填策
  略、Phase 24 是否擴充 `RecurringRule` entity 欄位），實作對應 Phase 前
  建議先跟使用者過一輪確認，避免做完才發現方向不對重工。
