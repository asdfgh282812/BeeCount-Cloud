# 專案詳情頁分類子預算 + 日期區間切換（比照 Moze）

日期：2026-09-06
狀態：待實作
對標來源：Moze（doc.moze.app/prepare/project/overview、/prepare/project/budget）+
使用者提供的 Moze 截圖（生活開銷頁：期間切換／出帳入帳總計／分類拆解）

## 0. 背景與決策摘要

目前 [project_detail_page.dart](lib/pages/project/project_detail_page.dart) 只有
「單一進度條 + 交易流水」，沒有期間切換、沒有分類拆解、也沒有分類層級的子預算。本次
比照 Moze 的「三層預算」概念（專案→類別→子類別，本次只做到類別這層，子類別見 §11）
補齊：

1. **分類子預算**：專案總預算可以進一步分配給一級分類，支援「固定金額」與「按照
   比例」兩種分配方式，比例模式在專案總預算變動時即時重算（不存快照）。
2. **期間切換**：詳情頁頂部加左右箭頭 + 可點擊的期間文字，點擊彈出「選擇區間」
   對話框（可捲動清單 + 取消/確定），對應到 `getProjectUsage(project, now)` 的
   `now` 錨點——後端計算邏輯已支援任意期間，只是從未有 UI 呼叫過。
3. **分類拆解畫面**：出帳/入帳/總計統計條 + 「已分配預算」/「未分配預算」/「未
   設定預算」三組分類卡片，點分類鑽入該專案＋該分類＋該期間的交易明細。
4. **每日預算 / 收入併入預算 / 預算超標推播提醒**：專案層級的三個附加設定，推播
   走**本機通知**（非 Cloud 通知中心，理由見 §6.3）。
5. **全部功能都要跨裝置同步**，包含全新的分類子預算實體——這比「加欄位」工程量
   大得多，App 端本次先把契約定義完整、把 App 端全部改完；**Cloud 端
   （`/Users/andy/BeeCount-Cloud`，獨立 git repo）的實作是本 spec 範圍外的
   後續工作**，本 spec 的 §7 只負責把 wire contract 定義清楚，讓對岸照著實作。
   在 Cloud 端完成前，這些新欄位/新實體會被本機正常寫入 Drift、也會被
   `ChangeTracker` 記錄成 pending change，但推送到 Cloud 後 Cloud 目前不認得
   這些新欄位/新 entity type，會被忽略（不會報錯，因為 `_MergeSpec` 是白名單
   機制，沒列的 key 直接被丟棄）——也就是說**同步真正生效要等 Cloud 端補完**，
   這段時間內這些設定實質上是「本機優先寫入，多裝置間暫不同步」，跟本機功能
   體驗上無感，但要跟使用者說清楚這個過渡狀態。

## 1. 現況（App 端相關程式碼盤點）

- 資料表：`Projects`（`lib/data/db.dart:943-983`）：`budgetAmount`（nullable=純
  記錄）、`periodType`（`monthly`/`yearly`/`fixed`）、`periodStart`/`periodEnd`
  （僅 fixed）、`carryoverEnabled`、`visibleOnHome`、`enabled`、`sortOrder`。
- Repository 介面：[project_repository.dart](lib/data/repositories/project_repository.dart)。
  `getProjectUsage(Project project, DateTime now)`（介面 128 行）已經吃任意
  `now` 算出「`now` 所屬那一期」的用量（`ProjectUsage.periodStart/periodEnd`
  也會回傳），**只是目前所有呼叫端都固定傳 `DateTime.now()`**：
  - `projectUsagesProvider`/`projectUsageProvider`（
    [project_providers.dart:12-44](lib/providers/project_providers.dart)）
  - 完全沒有「換到別期」的 provider/state。
- 實作：
  [local_project_repository.dart](lib/data/repositories/local/local_project_repository.dart)。
  `_periodRange`（206-223 行）依 `periodType` 算當期區間；`_previousPeriodRange`
  （226-242 行，私有）只給 carryover 用，`fixed` 回傳 `null`；`_sumExpenses`
  （185-204 行）是 raw SQL `SUM(...) WHERE project_sync_id=? AND type='expense'
  AND happened_at BETWEEN`，**沒有 GROUP BY**，也沒有算收入。
- 詳情頁：
  [project_detail_page.dart](lib/pages/project/project_detail_page.dart)。
  `_periodLabel`（75-89 行）純文字渲染，無互動；下方 `_TransactionsSection`
  透過 `getTransactionsByProject(syncId)`（
  [transaction_repository.dart:549](lib/data/repositories/transaction_repository.dart)，
  實作見
  [local_transaction_repository.dart:1901-1907](lib/data/repositories/local/local_transaction_repository.dart)）
  取交易，**沒有日期篩選**——目前顯示的交易列表其實是全時間範圍，跟頁面頂部
  「本月」用量數字口徑不一致，這次順手修正。
- 編輯頁：
  [project_edit_page.dart](lib/pages/project/project_edit_page.dart)，4 個
  `SectionCard`（名稱/icon、預算金額+純記錄開關、週期+結轉、顯示設定），純
  `StatefulWidget`，`_saveProject`（359-435 行）打包呼叫
  `createProject`/`updateProject`。
- 期間導航 UI 樣板（可抄但非共用元件）：
  `account_detail_page.dart:1064-1094` 的信用卡帳單週期 chevron（page-local
  `_billingPeriodOffset` state，`next` 箭頭在 offset=0 時鎖住）。
- 「選擇區間」清單彈窗：**全專案沒有**任何字面符合的元件，最接近的
  `analytics_page.dart:35` `_showPeriodPicker()` 呼叫的是滾輪式
  `wheel_date_picker.dart`（年/月滾輪，非清單），跟截圖的清單+取消/確定不同款，
  需要新寫。
- 通知：`flutter_local_notifications`（`pubspec.yaml:39`）已接好，
  `lib/utils/notification_util.dart` 的 `NotificationFactory.getInstance()
  .showNotification(...)` 可直接立即跳通知；`lib/services/system/
  reminder_monitor_service.dart` 是「記得記帳」的每日提醒，跟預算無關。App
  從未做過「預算超標提醒」。另有一個「通知中心」（server 權威，見
  `docs/changes/2026-08-17-notification-center.md`），`budget_alert` 分類
  在 Cloud 端已預留但零 producer，且只對 BeeCount Cloud 用戶生效——本次不用
  這條路（§6.3 詳述）。
- 分類階層：`Categories` 已支援 `parentId`/`level`（`lib/data/db.dart:121-140`,
  level 1=一級/2=二級），本次分類子預算**只做到一級分類**（§11 說明子分類
  暫緩原因）。
- Cloud 同步現況（跟 `docs/CLOUD_SYNC_INTEGRATION.md` 的描述不符，該文件在
  `project` 這塊過時——**以下以本次直接讀程式碼查到的實況為準**）：`project`
  entity 早已完整雙向同步：push 在
  `sync_engine_serialization.dart:411`（單筆）與 `:772-794`（fullPush），
  序列化在 `entity_serializer.dart:496-515`（`serializeProject`，欄位見上）；
  pull 在 `sync_engine_apply.dart:75-76` → `_applyProjectChange`（1212 行起）。
  `project` 是 ledger-scoped 實體（透過 `recordLedgerChange`，不在
  `change_tracker.dart:36` 的 `_userGlobalEntityTypes` 白名單內）。

## 2. 資料模型（App 端）

### 2.1 `Projects` 表新增欄位（schemaVersion 56）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `incomeIncludedInBudget` | bool | `false` | 收入併入預算 |
| `dailyBudgetEnabled` | bool | `false` | 是否顯示每日預算 |
| `dailyBudgetMode` | text, nullable | `'proportional'` | `'fixed'`／`'proportional'`，`dailyBudgetEnabled=false` 時忽略 |
| `reminderThresholdPercent` | int, nullable | `null` | null=不提醒；否則 1-200 的整數（可超過 100 代表「超支才提醒」） |
| `reminderNotifiedPeriodKey` | text, nullable | `null` | **本機專用欄位，不寫入 sync payload**（§7.1）。記錄「這期已經提醒過」，值＝該期 `periodStart` 的 ISO 字串，換期後跟新算出的 key 不同即可再次提醒 |

沿用既有 `MigrationStrategy.onUpgrade` 的 switch 寫法，在最新版本號後加一個
`from < 56` 分支，五個欄位都用 `ALTER TABLE projects ADD COLUMN ...`（都有
預設值，不需要 backfill）。

### 2.2 新表 `ProjectCategoryBudgets`

```dart
class ProjectCategoryBudgets extends Table {
  IntColumn get id => integer().autoIncrement()();
  TextColumn get syncId => text().nullable()();       // 跨裝置同步用
  IntColumn get projectId => integer()();              // FK -> Projects.id
  IntColumn get categoryId => integer()();              // FK -> Categories.id，僅一級分類
  TextColumn get mode => text().withDefault(const Constant('fixed'))(); // 'fixed'/'percentage'
  RealColumn get fixedAmount => real().nullable()();    // mode='fixed' 用
  RealColumn get percentage => real().nullable()();     // mode='percentage' 用，0-100
  BoolColumn get carryoverEnabled => boolean().withDefault(const Constant(false))();
  IntColumn get sortOrder => integer().withDefault(const Constant(0))();
  DateTimeColumn get createdAt => dateTime().withDefault(currentDateAndTime)();
  DateTimeColumn get updatedAt => dateTime().withDefault(currentDateAndTime)();
}
```

- 索引：`idx_project_category_budgets_project(projectId)`，唯一約束
  `(projectId, categoryId)`（一個分類在同一專案下只能有一筆分配）。
- 記得把 `ProjectCategoryBudgets` 加進 `@DriftDatabase(tables: [...])` 列表
  （`lib/data/db.dart:427` 附近，仿照既有 `Projects,` 那一行）。
- `carryoverEnabled` 語意同專案層級：monthly/yearly 才有意義，「上一期」定義
  沿用 `_previousPeriodRange` 的邏輯（下移一層套到分類，見 §3.4）；`fixed`
  週期的專案，其下分類一律忽略此欄位。
- 刪除規則：分類本身被刪除，或使用者手動移除分配 → 直接刪列（沒有軟刪除
  必要，這張表不像 `Projects` 本身要保留歷史交易的可回溯性）。
- 分類子預算**只認一級分類**（`Categories.level == 1`）；`categoryId` 若指向
  二級分類視為資料錯誤，UI 層的分類選擇器只列一級分類，不需要額外 DB
  constraint 擋（沿用其餘 repository 對 level 的處理慣例，靠呼叫端守規矩）。

## 3. Repository 變更

### 3.1 `ProjectRepository` 新增方法

```dart
/// 建立/更新/刪除一筆分類子預算分配。
Future<int> upsertProjectCategoryBudget({
  required int projectId,
  required int categoryId,
  required String mode,       // 'fixed' / 'percentage'
  double? fixedAmount,
  double? percentage,
  bool carryoverEnabled = false,
});
Future<void> removeProjectCategoryBudget(int projectId, int categoryId);
Future<List<ProjectCategoryBudget>> getProjectCategoryBudgets(int projectId);
```

`updateProject`/`createProject` **不**塞入分類分配（現有具名參數已經 9-10
個，不再膨脹），分類分配一律走上面獨立方法，UI 端在專案本身存檔後另外批次
呼叫（§5 分類子預算編輯頁）。

### 3.2 分類拆解 + 期間篩選：新增 `getProjectCategoryBreakdown`

```dart
/// 該專案在 [start, end) 期間內，依一級分類分組的支出/收入統計 + 筆數。
/// 回傳所有「在期間內有交易」的分類；沒有交易的分類由呼叫端自行從
/// getAllCategories() 補上「未設定預算」那組（見 project_detail_page 設計）。
Future<List<ProjectCategoryUsage>> getProjectCategoryBreakdown(
  String projectSyncId, {
  required DateTime start,
  required DateTime end,
});
```

`ProjectCategoryUsage`（新的資料類別，比照 `ProjectUsage` 放在
`project_repository.dart`）：`categoryId`、`expenseTotal`、`incomeTotal`、
`recordCount`。實作用 raw SQL（仿 `_sumExpenses` 寫法但補 `GROUP BY
category_id` + 拆 `type='expense'`/`'income'` 兩次 SUM，或一次查詢用
`SUM(CASE WHEN type='expense' THEN ... END)` 條件加總），JOIN 不需要（分類
名稱/icon 由呼叫端自己拿 `getAllCategories()` 合併，跟 `_TransactionsSection`
現有的 `categories` map 合併模式一致）。

### 3.3 `getTransactionsByProject` 加日期篩選

```dart
Future<List<Transaction>> getTransactionsByProject(
  String projectSyncId, {
  DateTime? start,
  DateTime? end,
});
```

可選參數，`start`/`end` 都不傳時維持現有「全時間範圍」行為（避免破壞其他
呼叫點，目前只有 `project_detail_page.dart:247` 一處呼叫，這次會改成一定
帶入當前選定期間）。

### 3.4 專案/分類用量計算的三處調整

1. **收入併入預算**：`getProjectUsage` 內，若
   `project.incomeIncludedInBudget == true`，`effectiveBudget` 額外加上
   `_sumIncome(syncId, range.start, range.end)`（新增一個對稱於
   `_sumExpenses` 但 `type='income'` 的私有方法）。`ProjectUsage` 需要新增
   欄位 `incomeIncluded`（記錄這期實際加了多少收入，UI 顯示用，不是必要但
   有助於除錯/顯示明細）。
2. **分類子預算用量**：`getProjectCategoryBreakdown` 回傳的
   `ProjectCategoryUsage` 只給「花費統計」，跟 `ProjectCategoryBudgets` 的
   分配額度是兩個獨立來源，由 UI 層（或一個新的組合 provider）合併成「已
   分配/未分配/未設定」三組，不在 repository 層做這個分組判斷（分組是展示
   邏輯，見 §4.3）。
3. **每日預算計算**：純前端展示邏輯，不需要新 repository 方法——
   `dailyBudgetMode='fixed'`：`effectiveBudget / 期間總天數`；
   `'proportional'`：`(effectiveBudget - used) / 剩餘天數`（剩餘天數含今天，
   即 `periodEnd.difference(today).inDays`，`today` 用不含時分秒的日期比較，
   避免時區/時分造成剩餘天數算錯 1 天——沿用 `month_range.dart` 現有的日期
   正規化慣例）。

## 4. UI：期間切換元件 + 詳情頁重新設計

### 4.1 新的可重用 widget：`lib/widgets/ui/period_range_selector.dart`

```dart
class PeriodRangeSelector extends StatelessWidget {
  final String label;              // 目前顯示的期間文字
  final VoidCallback? onPrev;       // null = 停用（例如已到專案建立前）
  final VoidCallback? onNext;       // null = 停用（已是當期，不能切到未來）
  final VoidCallback onTapLabel;    // 開「選擇區間」對話框
  ...
}
```

樣式抄 `account_detail_page.dart:1064-1094` 的 `chevron_left` /
`chevron_right` + 置中文字排版，抽成共用元件而非再複製一份。

### 4.2 「選擇區間」對話框：`showPeriodRangeListPicker`

新函式（放 `lib/widgets/ui/period_range_selector.dart` 或獨立
`period_range_dialog.dart`），`showModalBottomSheet` 呈現：
- 標題「選擇區間」
- 可捲動 `ListView`：由目前 offset 往回列出 N 期（monthly 用
  `periodForLabel`/`labelForDate` 往回推算，比照 `_previousPeriodRange` 的
  月份遞減寫法；yearly 逐年遞減；`fixed` 專案不呼叫這個 picker，因為只有
  單一區間，§4.1 的 `onTapLabel` 對 fixed 專案直接不掛，chevron 也不顯示）
- 每一列可點擊選取（選中列比照截圖用主色高亮），下方「取消」「確定」兩顆
  按鈕，確定才 commit 選擇
- 往回列多少期：monthly/yearly 都先列 12 期（超過 12 期使用者需求極低，
  之後如有反饋再加「更早」的分頁載入，這次不做）

### 4.3 期間 offset 狀態管理

新增（依 [[feedback_riverpod_state_pattern]] 慣例，StateNotifier + 
StateNotifierProvider，不用 Notifier/riverpod_generator）：

```dart
/// key = projectId，value = 往回第幾期（0=當期，1=上一期...）
class ProjectPeriodOffsetNotifier extends StateNotifier<Map<int, int>> {
  ProjectPeriodOffsetNotifier() : super({});
  void setOffset(int projectId, int offset) =>
      state = {...state, projectId: offset};
}
final projectPeriodOffsetProvider =
    StateNotifierProvider<ProjectPeriodOffsetNotifier, Map<int, int>>(
        (ref) => ProjectPeriodOffsetNotifier());
```

`project_detail_page.dart` 改用一個新的 family provider（取代現有直接
`ref.watch(projectUsageProvider(projectId))` 固定 `DateTime.now()` 那條路），
內部依 `periodType` 把 offset 換算成錨點 `DateTime`（monthly：往回位移
offset 個月；yearly：往回位移 offset 年；fixed：offset 恆為 0，因為只有
一期）再呼叫 `getProjectUsage(project, anchor)`。`onNext` 在 offset==0 時傳
`null`（不能切到未來，同信用卡帳單頁邏輯）；`onPrev` 目前不設下限（使用者
可以一路往回翻到專案建立之前，那期用量自然是 0，不特別擋，比照
`getProjectUsage` 對任意 `now` 都能算的通用性，UI 不用額外判斷專案建立日）。

### 4.4 詳情頁版面（由上到下）

1. `PrimaryHeader`：專案名稱 + 齒輪圖示（進「分類子預算編輯頁」，§5）+
   編輯圖示（沿用現有，進 `ProjectEditPage`）。
2. `PeriodRangeSelector`（`fixed` 週期專案整列隱藏，只留靜態文字顯示起訖
   日，比照現有 `_periodLabel` 的 `fixed` 分支）。
3. 統計條：出帳（紅）/入帳（綠）/總計（藍，=入帳-出帳，跟截圧的「總計」同
   算法），各自筆數 + 金額，三條 bar 依三者金額最大值等比例縮放（沒有花費
   時該條退化成一個點，比照截圖入帳金額很小時 bar 幾乎看不見的呈現）。
4. 「專案預算」卡片：`usage.budget == null`（純記錄型）整段隱藏；否則顯示
   「已花費/總預算」進度條（沿用既有 `BudgetProgressBar`）+「未分配」金額
   （= `effectiveBudget - Σ已分配分類的解析後金額`，比例模式即時解析成
   金額參與這個加總）。未分配可以是負數（分配總和超過總預算），此時用警示
   色顯示「超額分配 $X」而非「未分配 $X」。
5. 「已分配預算」分類卡片列（依花費金額由多到少排序）：**本期有交易、且**
   有 `ProjectCategoryBudgets` 列的分類，顯示 icon/名稱/本期花費/筆數/進度條
   （花費 vs 分配額）。
6. 「未分配預算」分類卡片列：**本期有交易、但**沒有 `ProjectCategoryBudgets`
   列的分類，顯示花費/筆數，右側文字固定顯示「未分配」（比照截圖字面）。
7. 「未設定預算」摺疊區塊：**本期零交易**的分類（不論有沒有設定
   `ProjectCategoryBudgets`——三組分組彼此互斥，判斷順序固定是「先看本期
   是否有交易，沒有一律歸這組；有交易才進一步看有沒有分配」，對齊截圖
   「6 筆」計數方式），預設摺疊，右上角徽章顯示分類數量，點擊展開/收合
   （沿用截圖的 chevron up/down 圖示）。
8. 點任一分類卡片 → 用 `getTransactionsByProject(syncId, start:, end:)` 疊加
   `categoryId` 篩選（可以在既有方法上加 `int? categoryId` 參數，或者直接在
   UI 層對回傳結果 `.where((t) => t.categoryId == x)` 過濾，因為單一專案單期
   交易量通常不大，過濾成本可忽略——選擇後者，不擴充 repository 方法簽章）
   push 一個複用既有交易列表呈現元件（`TransactionListItem`，沿用
   `_TransactionsSection` 現有寫法）的頁面。

## 5. 分類子預算編輯頁（新頁面，齒輪圖示進入）

`lib/pages/project/project_category_budget_edit_page.dart`：
- 列出帳本下所有一級分類（`getAllCategories()` 過濾 `level==1`）
- 每列：分類 icon/名稱 + 開關「設定子預算」；展開時顯示 `SegmentedButton`
  切換 `固定金額`/`按照比例`，對應輸入框（比例模式輸入框限制 0-100，即時
  顯示換算後金額 `= 專案總預算 × %`，方便使用者確認）
- 頁尾固定顯示「已分配 X / 專案總預算 Y」，超額分配時整行變警示色（不擋
  儲存——跟 Moze 一致，允許超額分配作為一種提醒訊號，不是硬限制）
- 若專案是純記錄型（`budgetAmount == null`），比例模式停用（分母是 null
  沒意義），只能用固定金額；`未分配`/`已分配` 底部統計整段不顯示
- 存檔：對每一列的變動分別呼叫 `upsertProjectCategoryBudget`/
  `removeProjectCategoryBudget`（不用整批 diff，遠比在一個大事務裡處理
  簡單，這頁預期分類數量在幾十筆以內，效能無虞）

## 6. 專案編輯頁新增設定（`project_edit_page.dart`）

在既有「顯示設定」`SectionCard`（304-332 行）之後，新增一個 `SectionCard`：

### 6.1 收入併入預算
`AppListTile` + `Switch.adaptive`，對應 `incomeIncludedInBudget`。純記錄型
專案（`_pureTracking==true`）時停用（沒有預算基準可併入）。

### 6.2 每日預算
`AppListTile` 開關 `dailyBudgetEnabled`；開啟後展開 `SegmentedButton`
`固定金額`/`按照比例` 對應 `dailyBudgetMode`。純記錄型/`fixed` 週期停用
（`fixed` 週期沒有清楚的「今天在哪個位置」概念參考 Moze 只在 monthly/yearly
展示每日預算，這裡跟進）。

### 6.3 預算提醒
`AppListTile` 開關（`reminderThresholdPercent != null` 即為開啟），開啟後
選擇門檻百分比（提供 50/80/100/120 幾個常見預設 + 自訂輸入）。**技術路線
選擇本機通知**：
- 理由：Cloud 通知中心（`docs/changes/2026-08-17-notification-center.md`）
  只對「同步後端＝BeeCount Cloud」的使用者生效，其餘 iCloud/Supabase/
  WebDAV/S3 使用者完全看不到；而且 Cloud 端 `budget_alert` 目前零
  producer，要做這條路等於同時要在兩個 repo 生出全新邏輯。本機通知現成
  可用、對所有同步後端一視同仁，本次選這條。
- 觸發點：在「該筆交易寫入/更新時涉及某個掛了 `projectSyncId` 的專案」的
  資料寫入路徑上（`local_repository.dart` 裡交易 create/update 的統一入口，
  依 `docs/CLOUD_SYNC_INTEGRATION.md` §2 提醒的「實際呼叫點集中在
  `local_repository.dart`」去定位，不要假設在 `local_transaction_repository.dart`），
  寫入完成後非同步呼叫一個新的 `ProjectBudgetReminderService.checkAndNotify
  (projectId)`：重新算 `getProjectUsage`，若 `used/effectiveBudget*100 >=
  reminderThresholdPercent` 且 `reminderNotifiedPeriodKey` 不等於這期的
  `periodStart` ISO 字串，呼叫 `NotificationFactory.getInstance()
  .showNotification(...)`，然後把 `reminderNotifiedPeriodKey` 更新成這期
  的 key（純本機寫入，不需要 `ChangeTracker` 記錄，因為這欄位本來就不同步，
  見 §2.1）。
- 換期後 `reminderNotifiedPeriodKey` 自然跟新算出的 key 不同，下次觸發會
  再提醒一次，不用額外重置邏輯。

## 7. Cloud 同步契約（App 端已定義完成，Cloud 端後續實作）

### 7.1 `Projects` 新欄位——走「修改既有 entity」流程

比照 `docs/CLOUD_SYNC_INTEGRATION.md` §4 SOP：
- `entity_serializer.dart:496-515` `serializeProject` 新增
  `incomeIncludedInBudget`、`dailyBudgetEnabled`、`dailyBudgetMode`、
  `reminderThresholdPercent` 四個 key（**不含** `reminderNotifiedPeriodKey`
  ——這欄位刻意本機專用，見 §2.1 理由）。都是「使用者可以清空」的欄位
  （比如關掉每日預算），但都不是「清空代表特殊語意」的欄位（不像
  attachments 那種要用空字串/空陣列表達清空），用第 1/2 態（`if (x != null)`
  有條件送出）即可，bool 欄位無條件送出（`false` 不是 null，不會被
  filter 掉）。
- `sync_engine_apply.dart:1212` `_applyProjectChange` 對稱新增這四個欄位的
  pull 處理（containsKey 缺鍵保留現值語意，跟 `includeInTotal` 那套模式
  一致，見 `docs/CLOUD_SYNC_INTEGRATION.md` §1.3）。
- Wire key 字面字串本次先定為
  `incomeIncludedInBudget`/`dailyBudgetEnabled`/`dailyBudgetMode`/
  `reminderThresholdPercent`——**這是跟 Cloud 端要對齊的契約，Cloud 端
  实现前先用这份 spec 的这四个 key 沟通，不要各自发挥**。

### 7.2 `ProjectCategoryBudgets`——走「新增全新 entity type」流程

新 entity type 名稱：`project_category_budget`。App 端依
`docs/CLOUD_SYNC_INTEGRATION.md` §5 的完整清單：
1. 新 Drift table（§2.2）+ schemaVersion 56 migration + 加進
   `@DriftDatabase(tables:[...])`
2. `lib/data/repositories/project_repository.dart`（或独立
   `project_category_budget_repository.dart`——因为跟 `Project` 强关联，
   放同一个 repository 介面比较不会破坏既有的「一个 domain 一个
   repository」慣例，倾向前者）+ `local_project_repository.dart` 实作
3. `change_tracker.dart`：ledger-scoped（继承 `Projects` 所在专案的
   `ledgerId`），**不**加进 `_userGlobalEntityTypes` 白名单
4. `entity_serializer.dart` 新增 `serializeProjectCategoryBudget`：
   `syncId, projectSyncId, categorySyncId, mode, fixedAmount, percentage,
   carryoverEnabled`（`projectId`/`categoryId` 要转成对应 `syncId` 才能跨
   装置识别，同 `budget` entity 的 `categorySyncId` 转换模式）
5. `sync_engine_serialization.dart` 新增 `case 'project_category_budget':`
   push 分支 + fullPush 覆盖
6. `sync_engine_apply.dart` 新增 pull 分支 + `_applyProjectCategoryBudget
   Change`，注意 `projectSyncId`/`categorySyncId` → 本机 int id 的解析（
   `sync_engine_resolvers.dart` 既有模式）
7. `lib/providers/` 新增对应 provider
8. `test/sync/`、`test/repositories/` 新增测试
9. **首次全量同步**：确认 Cloud 端 `snapshot_builder.py` 有把这个新 entity
   放进 `/sync/full`，App 端 fullPull 路径也要新增对应写入逻辑——这一步是
   Cloud 端工作，App 端只需要在 fullPull 处理 switch 里预留这个 case（等
   Cloud 真的送这个 entity 时才有内容可处理，这次先把 case 写好、body 对
   齐 §7.2 第 6 点的 apply 逻辑）

Cloud 端需要做的（本 spec 范围外，供 Cloud 端 session 参考）：Alembic
migration 新表、`projection.py` 的 upsert/delete、`sync_applier.py` 的
`_LEDGER_MERGE_SPECS['project_category_budget']`、
`snapshot_builder.py` 的 SELECT、`routers/read/ledgers.py` 的读取端点（
若 web 面板要显示分类子预算的话——是否要做 web UI 由 Cloud 端那次会话
另外跟使用者确认，本 spec 不预设）。

## 8. l10n

新增 key（`lib/l10n/app_en.arb` 先加，`app_zh_TW.arb` 对应中文——依
[[feedback_l10n_policy_change]]，`app_zh.arb`/`app_ko.arb` 不再维护）：
期间选择对话框标题/取消/确定、统计条「出帐/入帐/总计」、「已分配预算/未分
配预算/未设定预算」三组标题、分类子预算编辑页文案、每日预算/收入并入预算/
预算提醒设置项文案、超额分配警示文案。改完跑 `flutter gen-l10n`。

## 9. 测试计划

- `test/repositories/`：`getProjectCategoryBreakdown` 分组正确性（含/不含
  当期交易的分类各自落在哪一组）、`upsertProjectCategoryBudget` 的
  fixed/percentage 两种模式、`getProjectUsage` 的 `incomeIncludedInBudget`
  分支
- `test/sync/`：比照 `test/sync/account_partial_update_apply_test.dart`
  风格，新增 `project_partial_update_apply_test.dart`（验证只带部分新增
  字段时不清空其它字段）+ `project_category_budget_apply_test.dart`
- 手动验证（无法用 Cloud 端联调前）：本机建专案→分配分类子预算→切换期间
  →确认分类拆解分组、每日预算数字、预算提醒本机推送都正确；重新整理 App
  （模拟重开）确认本机数据都还在（因为这次先不依赖 Cloud 同步验证正确性）

## 10. 桌面 Widget / 首页卡片

现有 `lib/widget/views/`（原生桌面 widget）与首页专案卡片
（`project_overview_page.dart`）本次**不**跟进显示分类拆解——这两处维持
「进度条 + 已用/剩余」的既有呈现，只有专案详情页做完整改版。如果之后
需要在这些地方也呈现子预算信息，是独立的后续需求。

## 11. 明确排除的范围

- **子分类预算**（Moze 第三层）：`Categories` 已有 `parentId`/`level`
  架构基础，但本次不做——分类子预算的 UI/资料模型已经因为「固定/比例
  两种模式 + carryover」而有一定复杂度，再加一层子分类会让分配加总校验
  （子分类总和 ≤ 分类分配额 ≤ 专案总预算）变成三层递归，范围明显超出这次
  讨论的「点进去看分类 + 换期间」这个核心诉求。等分类层级的功能上线并观察
  使用情况后，再评估是否要往下做一层。
- 统计专案（Moze 的「依条件自动归类」，标签/账户/名称等条件规则）：跟
  `docs/superpowers/specs/2026-08-27-project-feature-design.md` 决策 1
  一致，本次沿用「不做条件式自动归类规则引擎」的既有决定,不重新论证。
- 分类子预算的到期提醒不做独立的每分类推播（只做专案层级一个提醒门槛,
  §6.3),避免一个专案底下十几个分类各自触发推播造成骚扰。
