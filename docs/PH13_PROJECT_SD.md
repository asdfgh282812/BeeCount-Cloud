# 專案（Project）功能 SD（Phase 13）

本文件是「專案」功能的設計文件（SD）。目的是把 entity 設計、跨端影響、SOP
checklist 盤點清楚，**本文件不包含任何程式改動**，實作留待下一輪按此文件
展開（比照 `docs/PH6_USER_FEEDBACK_2026-08_SD.md` 的既有慣例：先寫 SD、
之後分段做）。

> **2026-08-08 UI 位置修正**：本文件 §0/§3.1/§4.1 原本把「專案」設計成
> 「標籤」分頁底下的子分頁，這是當時使用者指定的 UI 位置。實作完成後使用
> 者改變主意，要求把「專案」分開成獨立入口、緊鄰頂部導航「標籤」右側（見
> `frontend/packages/web-features/src/nav.ts` NAV_GROUPS 的 bookkeeping 組、
> `frontend/apps/web/src/pages/sections/ProjectsPage.tsx` 新路由
> `/app/projects`）。以下內文提到「標籤分頁底下的子分頁」的地方，UI 位置
> 已改成如上所述，其餘設計（entity 結構、資料模型獨立於標籤）不變。

---

## 0. 背景

`docs/MOZE_FEATURE_GAP_SD.md`（§1 現況總覽表、§2.11 記帳模式）已經指出這個
缺口：

> 分類與專案 — 專案/預算：🟡 部分（有 `budgets` 總額/分類預算，**沒有獨立
> 「專案」概念**——§2.11 記帳模式依賴這個缺口，需要先決定要不要做真正的
> project entity）

`docs/MOZE_FEATURE_GAP_SD.md:762-770` 當時列出兩個選項並保留未決：(a) 做一
個正式的 `project` entity，或 (b) v1 先用既有 `tags` 頂替（風險：tag 無層
級、無預算掛勾，語意跟 Moze 的「專案」不完全等價）。

**本文件定案採用選項 (a)：新增正式的 `project` sync entity**，理由：
- 使用者這次的需求明確要「專案」功能本身（放在標籤分頁底下、手機版也要能
  用），不是要「讓標籤兼職專案」；tag 目前是多對多、無預算欄位，硬套會讓
  `UserTagProjection` 混雜兩種不同語意的資料，之後難以拆分。
- 專案有自己的預算金額/期間/結轉這些欄位，跟 tag 的資料形狀差異大，做成
  獨立 entity 才能乾淨地擴充統計（§4）。

放在「標籤分頁底下」是使用者指定的 UI 位置（見 §3.1），不代表資料模型上
專案依附於標籤——兩者是同一頁面下的兩個平行分頁，資料互相獨立。

---

## 1. Moze 官方文件調查結果

來源：[prepare/project/overview](https://doc.moze.app/prepare/project/overview)。

- **欄位**：名稱、圖示（emoji，如 🏠🎪👶）、預算（總預算/已花/剩餘/每日可花）、
  期間（週期性 monthly/yearly，或單次固定起訖日，如旅遊行程）、可選的「預
  算自動結轉到下一期」、「顯示在首頁行事曆」的可見性開關。
- **交易關聯**：**手動指定**——記帳時使用者自己選這筆交易屬於哪個專案，跟
  分類/帳戶同一層級的欄位。
- **統計專案**（v4.0+，進階功能）：另一種「規則式」專案，靠條件（帳戶/分
  類/標籤/商店名稱包含或排除）自動彙總交易，可以用 `#不納入預算` 標籤把
  特定交易排除在預算計算外，也能把其它專案當條件疊加成更高層級的總預算。
  **本 Phase 不含這個模式**，只做手動指定，統計專案列為 v2 才考慮的延伸
  （見 §6）。
- **層級**：專案彼此不分層（標準專案之間平行，只有統計專案能「引用」其它
  專案當條件，不是真的父子結構）。
- **總覽頁**：顯示每個專案的本月花費/預算進度百分比/每日剩餘可花/狀態指標
  （✅ 正常 / ⚠️ 接近上限 / 🚨 超支）。

---

## 2. Entity 設計

### 2.1 新表 `read_project_projection`

Ledger-scoped，PK 形狀比照 `read_budget_projection`（`src/models.py:781-798`）：
`(ledger_id, sync_id)`。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `ledger_id` | FK → `ledgers.id` | PK 之一 |
| `sync_id` | String(255) | PK 之一 |
| `user_id` | FK → `users.id` | 建立者，供權限查詢索引 |
| `name` | Text | 專案名稱，必填 |
| `icon` | String(32) | emoji 字串，選填 |
| `budget_amount` | Float, nullable | 總預算，`None` = 不設預算（純追蹤用途） |
| `period_type` | String(16) | `fixed` \| `monthly` \| `yearly` |
| `period_start` | Date, nullable | `period_type='fixed'` 時必填（起） |
| `period_end` | Date, nullable | `period_type='fixed'` 時必填（迄）；`monthly`/`yearly` 不使用，週期由系統依當下日期滾動計算 |
| `carryover_enabled` | Boolean, default False | 預算是否自動結轉到下一期（僅 `monthly`/`yearly` 有意義） |
| `visible_on_home` | Boolean, default True | 對齊 Moze「顯示在首頁行事曆」；本倉庫首頁沒有行事曆卡片，這個開關先對應「總覽頁要不要顯示這個專案的卡片」 |
| `enabled` | Boolean, default True | 停用 = 保留歷史資料但不再出現在挑選器/總覽 |
| `sort_order` | Integer, default 0 | 使用者自訂排序 |
| `source_change_id` | BigInteger | 既有 sync 慣例 |

### 2.2 交易反查欄位

`ReadTxProjection`（`src/models.py:499`）新增 nullable 欄位
`project_sync_id: str | None`，比照既有 `debt_sync_id`/`installment_plan_sync_id`
反查模式（`src/models.py:555, 552`）：有值 = 這筆交易手動指定屬於哪個專案；
`None` = 沒掛專案。**只支援 `expense`/`income`**（跟拆帳/欠款同款限制，
`transfer`/`adjustment` 沒有「花在哪個專案」的語意，寫入路徑需要比照
`_assert_debt_exists` 加一個 `_assert_project_exists` 檢查並拒絕不符合的
tx_type）。

一筆交易同時只能掛一個專案（跟 `debt_id`/`refund_of_id` 同款單值語意，不
是像 `tag_ids` 那樣的多對多）——這對齊 Moze「標準專案」手動指定的語意；
統計專案（§6，本 Phase 不做）才是多對多條件式歸類。

---

## 3. 7 步 SOP checklist（照 CLAUDE.md「新增或修改 Sync Entity 檢查清單」）

1. **DB & Migration**：新增 `read_project_projection` 資料表 + Alembic
   migration；`read_tx_projection` 加 `project_sync_id` 欄位（兩個改動可以
   合併成一支 migration，比照 §2.2/2.3 debt/installment 當時的做法）。
2. **Projection**：`src/projection.py` 新增 `upsert_project`/`delete_project`；
   `upsert_tx` 的 `values` dict 補 `"project_sync_id": _as_str(payload.get("projectId"))`。
3. **Sync Applier**：`src/sync_applier.py`
   - `_MERGE_SPECS["project"]`（新 entity）+ `_LEDGER_UPSERT_DISPATCH["project"]`/
     `_LEDGER_DELETE_DISPATCH["project"]` 三張表登記。
   - `_MERGE_SPECS["transaction"]` 加一組 `("projectId", "project_sync_id")`。
4. **Write Routers**：新增 `src/routers/write/projects.py`（POST/PATCH/DELETE，
   結構比照 `src/routers/write/budgets.py`——同樣是「單純的期間+金額設定」
   entity，owner/editor 可寫）；`src/routers/write/transactions.py` 的
   create/update 加 `project_id` 欄位透傳 + `_assert_project_exists` 校驗
   （放在 `_shared.py`，比照 `_assert_debt_exists` 的寫法）。
5. **Read Routers**：`src/routers/read/ledgers.py` 或 `workspace.py` 新增
   `GET /ledgers/{id}/projects`（列表，含每個專案的花費彙總——`SUM(amount)
   WHERE project_sync_id = X`，比照現有 debt 的 `repaid` 累加邏輯做法）；
   `ReadTransactionOut` 加 `project_id`/`project_name`（反查展示欄位，比照
   `debt_counterparty_name` 的既有模式，讓前端不用額外查表）。
6. **Snapshot Builder**（⚠️ 最容易漏）：`src/snapshot_builder.py` 的
   `tx_stmt` SELECT 加 `ReadTxProjection.project_sync_id`，否則下一次
   `_commit_write` 拿 snapshot 當 prev 比對時看不到既有交易的專案歸屬，會
   被靜默撤銷（CLAUDE.md 記過的既有 bug 模式）。**同時** `src/routers/write/
   _shared.py::_projection_row_to_tx_dict`（PATCH 快路徑的 prev_item
   builder）也要補上這個欄位——本 Phase（手續費/折扣）實作時剛好踩到這個
   函式漏補欄位導致 PATCH 部分更新把新欄位吃掉的 bug，是 `snapshot_builder.py`
   之外**第二個**容易漏、但同樣會導致「改別的欄位時把這個欄位悄悄清空」的
   地方，之後任何交易新欄位都要記得檢查這兩處，不是只有 `snapshot_builder.py`。
7. **測試**：`tests/test_projects.py`（CRUD + owner-only 校驗）+
   partial-update 契約測試（比照 `tests/test_tx_merchant.py` 三段式：web
   create/read、web PATCH 缺鍵保留舊值、mobile `/sync/push` merge 契約）+
   交易掛專案的花費彙總正確性測試（多筆交易累加、`monthly`/`yearly` 週期
   邊界切換時彙總只算當期）。

---

## 4. 前端設計

### 4.1 標籤分頁新增「專案」子分頁

`frontend/apps/web/src/pages/sections/TagsPage.tsx`（目前是單一
`TagsPanel` 直接渲染，沒有 tab 結構）改成：

```tsx
<Tabs>
  <TabsList>
    <TabsTrigger active={tab === 'tags'} onClick={() => setTab('tags')}>
      {t('tags.tab.tags')}
    </TabsTrigger>
    <TabsTrigger active={tab === 'projects'} onClick={() => setTab('projects')}>
      {t('tags.tab.projects')}
    </TabsTrigger>
  </TabsList>
  <TabsContent>{tab === 'tags' ? <TagsPanel ... /> : <ProjectsPanel ... />}</TabsContent>
</Tabs>
```

`Tabs`/`TabsList`/`TabsTrigger` 元件（`packages/ui/src/ui/tabs.tsx`）已有
先例：`frontend/apps/web/src/pages/sections/AdminBackupPage.tsx:365-372`
就是同款「本地 state 控制 active tab」的用法，直接重用，不需要新元件。

### 4.2 `ProjectsPanel`（新元件，`packages/web-features/src/features/`）

結構比照 `TagsPanel.tsx`：列表（含每個專案的統計：本期花費/預算/進度百分
比/狀態圖示）+ CRUD 表單（名稱/icon/預算金額/期間類型/起訖日或週期/結轉開
關/首頁可見開關）+ 刪除確認（有交易掛著時的行為對齊 `budgets` 既有慣例，
需要在實作時確認：直接擋刪除，還是允許刪除但保留歷史交易的 `project_sync_id`
變成懸空引用——建議比照 §2.9.5.4 對信用卡回饋規則「有交易掛著時軟刪除
（`enabled=false`）而非物理刪除」的既有先例）。

### 4.3 `TxForm` 新增 `project_id`

`frontend/packages/web-features/src/forms.ts` 的 `TxForm` 加
`project_id: string`，只在 `tx_type !== 'transfer' && tx_type !== 'adjustment'`
時顯示。UI 元件新增 `ProjectSelector`/`ProjectPickerDialog`，比照 Phase 11
（`docs/PH6_USER_FEEDBACK_2026-08_SD.md` Phase 11 段落）新做的
`CategorySelector`/`TagSelector` 搜尋 + 表單內「新增「xxx」」內嵌建立模式
——選擇器元件本身只管 UI 觸發，實際呼叫 `createProject` API 的邏輯由呼叫方
（`TransactionsPage.tsx`/`GlobalEditDialogs.tsx`）實作並往下傳，跟既有
`onCreateTxCategory`/`onCreateTxTag` 分工模式一致。

### 4.4 手機版驗證（使用者明確要求）

這是使用者這次提出的明確要求（「請注意，在手機版也是正常運行」），實作完
成後**必須**在窄螢幕寬度（比照本 Phase 13 之前的手續費/折扣功能驗證用的
390×844 viewport）額外測過一輪，檢查項目：
- 標籤/專案分頁切換按鈕在窄螢幕不換行、不溢出。
- `ProjectsPanel` 列表卡片、CRUD 表單在窄螢幕的欄位排列正常（不要求跟桌
  面版一樣的多欄 grid，允許收成單欄，但要確認沒有橫向捲動或文字被裁切）。
- `ProjectPickerDialog` 跟既有 `CategoryPickerDialog`/`TagPickerDialog` 一
  樣，在窄螢幕測過搜尋輸入、內嵌新增按鈕都可點擊到。

這項要求疊加在 CLAUDE.md 既有的「動到 Web UI 必須瀏覽器手測」鐵律之上，本
Phase 額外強調必須包含手機寬度，不能只測桌面寬度就視為完成。

---

## 5. 與既有 `budgets` 的關係

`read_budget_projection`（`src/models.py:781-798`）是 category-scoped 的
預算（`budget_type`/`category_sync_id`/`amount`/`period`/`start_day`），跟
新的 `read_project_projection` 是**兩條完全獨立的邏輯**：
- 專案有自己的 `budget_amount` + 花費彙總（`SUM(amount) WHERE
  project_sync_id = X`），不讀也不寫 `read_budget_projection`。
- 既有的分類預算功能（`BudgetsPanel`/`BudgetsPage`）不需要改動，兩者在
  UI 上也是分開的入口（預算在既有的「預算」頁面，專案在「標籤」分頁底下
  新的子分頁）。
- 之後若要做「專案底下再細分各分類花多少」這種交叉統計，屬於 §2.10 統計
  報表的延伸需求，不在本 Phase 範圍。

---

## 6. Out of scope（明確排除，列為未來延伸）

- **統計專案**（§2.11 提到的 Moze v4 條件式自動歸類）：需要規則引擎（帳戶/
  分類/標籤/商店名稱 include/exclude 條件 + 用其它專案當條件疊加），工程
  量比照本文件其它項目更大，且依賴本 Phase 先把「標準專案」entity 定案，
  排在之後。
- **記帳模式（Entry Mode Profiles，`docs/MOZE_FEATURE_GAP_SD.md` §2.11）**：
  當初卡住的原因就是缺「專案」entity，本 Phase 做完後這個依賴解掉了，但
  記帳模式本身（預設幣種/帳戶/專案/類別過濾 + 首頁快速切換）仍是獨立的新
  entity + 大量前端表單過濾邏輯，需要另開一份 SD，不在本文件範圍內。
- **首頁行事曆卡片**：Moze 的「顯示在首頁行事曆」是嵌在其行事曆型首頁裡，
  本倉庫首頁是總覽儀表板、沒有行事曆卡片概念，`visible_on_home` 欄位這次
  先實作成「總覽頁要不要顯示這個專案的卡片」，若之後首頁改版出現行事曆式
  排版，語意可能需要重新對齊，屆時再調整。

---

## 7. 測試計畫（實作時展開，本文件僅列覆蓋點）

- **pytest**：CRUD（含 owner-only 寫入權限）、partial-update 契約測試
  （`project_sync_id` 缺鍵保留舊值）、花費彙總正確性（`monthly`/`yearly`
  週期邊界、`fixed` 起訖日邊界）、`transfer`/`adjustment` 交易帶
  `project_id` 應該被拒絕、刪除有交易掛著的專案時的行為（依 §4.2 決定的
  策略驗證）。
- **前端 `pnpm test:unit`**：`ProjectSelector`/`ProjectPickerDialog` 的搜尋
  + 內嵌新增邏輯單元測試。
- **瀏覽器手測**（桌面 + 手機寬度都要）：標籤分頁新增專案子分頁、建立/編輯/
  刪除專案、新增交易掛專案、專案總覽的花費/預算進度顯示正確、視窗縮到手機
  寬度時上述操作都能正常完成。
