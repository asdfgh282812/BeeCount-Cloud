# Phase 13 專案功能（docs/PH13_PROJECT_SD.md）— 測試報告 + 手動測試清單

- 測試日期：2026-08-08
- 測試範圍：[PH13_PROJECT_SD.md](./PH13_PROJECT_SD.md) 全文 —— 新增
  `project` sync entity（server 7 步 SOP + 前端「標籤」分頁底下的「專案」
  子分頁 + 交易表單掛專案）。
- **2026-08-08 UI 位置修正**：使用者後續要求把「專案」從標籤分頁底下的子
  分頁改成獨立入口，緊鄰頂部導航「標籤」右側（新增 `/app/projects` 路由 +
  `ProjectsPage.tsx`，`TagsPage.tsx` 恢復成純標籤管理）。以下第二節「已在
  瀏覽器裡點過一輪」的操作路徑（點「/app/tags」頂部分頁按鈕切換到「專案」）
  已對應改成直接點頂部導航的「專案」按鈕或前往 `/app/projects`，UI 呈現與
  操作結果本身不變，僅入口位置不同；已重新在桌面（1280×900）與手機
  （390×844）寬度確認新入口正常（桌面：頂部導航「專案」緊鄰「標籤」右側，
  點擊後渲染獨立頁面；手機：底部導覽 5 個固定分頁不含「專案」，但 ⌘K
  命令面板「頁面導覽」分組新增了「專案」項目可跳轉）。
- 本輪環境：`make dev-api`（`--reload`）/ `make dev-web` 都在背景跑著，
  **server 端契約用 pytest 自動化驗證，web UI 用 Safari 瀏覽器自動化工具
  對著真實跑起來的 dev server 實際點過一輪完整流程（桌面 1280×900 +
  手機 390×844 兩種寬度）**，不是只跑過 `pnpm build`。

---

## 一、已自動化驗證的部分（這次會話跑過，全部通過）

### 1. Backend — pytest

```
JWT_SECRET=test-secret .venv/bin/python -m pytest tests/ -q
```

- 新增 `tests/test_projects.py`（23 個用例，全過）：
  - CRUD（含 owner-only 寫入權限、多帳本隔離）
  - `budget_amount`/`period_type`/`period_start`/`period_end`/
    `carryover_enabled`/`visible_on_home`/`enabled` 欄位落庫與回讀正確
  - `period_type='fixed'` 建立時缺 `period_start`/`period_end` → 400；
    帶齊才成功
  - `budget_amount` 傳 `null` 可清空（改回純追蹤用途）
  - 交易掛 `project_id`：只支援 `expense`/`income`，`transfer`/
    `adjustment` 帶這個欄位一律 400
  - `project_id` 指向不存在的專案 → 400
  - PATCH 交易顯式清空 `project_id`（傳空字串）/ 不帶這個 key 時保留舊值
    的 partial-update 契約（比照 `debt_id`/`refund_of_id` 同款語意）
  - `GET .../transactions`、`GET /workspace/transactions` 都回傳
    `project_id`/`project_name` 反查欄位
  - 花費彙總正確性：`fixed` 起訖日邊界（含頭尾兩天）、`yearly` 只算當年
    交易、預算門檻狀態切換（`ok`/`warning`≥80%/`over`≥100%）
  - 刪除：沒有交易掛著時物理刪除；已有交易掛著時軟刪除
    （`enabled=false`，清單保留但標記停用）
  - mobile `/sync/push` 的 `project` merge 契約（partial update 保留舊
    值）+ `transaction` entity 的 `projectId` 反查欄位同款保留語義 +
    `delete` action 正確清掉 projection 行
- **本輪意外發現並修好的既有問題**：撰寫 `WriteProjectCreateRequest`/
  `WriteProjectUpdateRequest` 時，插入點誤把既有 `WriteDebtUpdateRequest`
  的 `closed_at` 欄位切斷到新類別外面，導致 `PATCH .../debts/{id}` 的
  結案/重新開啟功能整個失效（`closed_at` 靜默不生效）。已發現並修正，
  `tests/test_debts.py::test_close_and_reopen_debt_overrides_status`/
  `test_send_due_debt_reminders_skips_closed_debt` 兩個既有測試從失敗
  改回通過，其餘全量回歸沒有新增失敗。
- 全量回歸：除兩個**跟本次改動無關的既有 flaky/预期外用例**外全過
  （`git stash` 驗證過，不帶本次改動的 `main` 分支跑這兩條測試同樣失敗）：
  - `tests/test_import_simple.py::test_accounts_parent_before_child_required`
  - `tests/test_recurring_rules.py::
    test_recurring_occurrence_update_overridden_skipped_by_update_from`
    （對「現在」日期敏感的既有用例）
  - 另有 `src/routers/ai/test_provider.py::test_provider` 是既有的
    collection error，跟本次改動無關。
- Alembic 遷移 `0040_projects` 在乾淨的臨時 SQLite 檔案上跑過
  `upgrade head` 成功；也在你本機真實的 `beecount.db`（不是測試庫）上
  跑了 `upgrade head`，純新增表 + 新增欄位，無破壞性操作。

### 2. Frontend

```
pnpm -C apps/web build      # tsc -b && vite build，無錯誤
pnpm -C apps/web test:unit  # 11 個既有測試檔、79 個用例全過
```

---

## 二、本次會話已經在瀏覽器裡實際點過一輪的部分

用真實跑著的 `make dev-api`（`--reload`，已對本機 `beecount.db` 跑過
migration）+ `make dev-web`，登入既有測試帳號、對著「測試帳本」操作：

### 2.1 桌面寬度（1280×900）

1. `/app/tags` 頁面頂部出現「標籤」/「專案」分頁按鈕，切換正常，不影響
   既有標籤分頁的既有行為。
2. 「專案」分頁：空狀態文案正確；點「新增專案」開對話框，填名稱「日本
   旅行」+ icon「✈️」+ 預算「5000」+ 週期「每月」，送出後列表卡片正確
   顯示 icon/名稱/週期/狀態「正常」/花費「0」/預算「5000」，toast 顯示
   「專案已建立」。
3. `/app/transactions` 建立交易表單裡出現「關聯專案」欄位（緊接在「關聯
   欠款」之後），點開彈出 `ProjectPickerDialog`，能看到剛建的「日本旅行」
   chip、搜尋框、「不掛專案」清空按鈕；選中後表單欄位正確回顯專案名稱。
4. 送出這筆掛專案的交易（金額 888、分類「飲食」、帳戶「測試信用卡」）成
   功，交易列表新增一行；點開該筆交易的交易詳情彈窗，正確顯示「關聯
   專案：日本旅行」列。
5. 回到「專案」分頁，卡片花費即時更新成「888」，進度條正確按
   888/5000 比例顯示。
6. 刪除「日本旅行」專案（此時已有交易掛著）→ 卡片**沒有消失**，改為
   顯示「已停用」標記，花費數字保留，toast 顯示「專案已刪除」（軟刪除
   行為符合 §4.2 設計）。
7. 回到交易表單重新打開「關聯專案」picker → 已停用的專案**不再出現**
   在可選清單裡（顯示「該帳本還沒有專案。」空狀態），確認
   `ProjectSelector` 正確過濾 `enabled=false`。
8. 編輯已停用的專案 → 表單多出「啟用中」checkbox（未勾選，對應目前
   `enabled=false`），勾選後儲存 → 卡片「已停用」標記消失，確認
   PATCH `enabled=true` 的重新啟用路徑可用。
9. 清理測試資料：刪除剛才建的測試交易 + 專案（此時專案已無交易掛著，
   刪除後從列表**整個消失**，確認沒有交易時走物理刪除路徑）。

### 2.2 手機寬度（390×844，CLAUDE.md 對本 Phase 額外要求的項目）

1. 標籤/專案分頁按鈕在窄螢幕不換行、不溢出（截圖確認）。
2. 「新增專案」對話框收成單欄版面，所有欄位（名稱/icon/預算/週期三選一
   按鈕/兩個 checkbox）完整可見可點擊，週期按鈕文字換行但沒有裁切，沒
   有橫向捲動。
3. 交易表單裡的「關聯專案」欄位需要在表單捲動容器內往下滑才會出現
   （表單本身就很長，這是既有行為，不是本次改動引入的問題），滑到後
   正常顯示與可點擊。
4. `ProjectPickerDialog` 在手機寬度下彈窗置中、搜尋框跟專案 chip 都完整
   可見可點擊，沒有被裁切或需要額外橫向捲動（截圖確認）。

---

## 三、建議你自己再抽查的項目（本輪沒有覆蓋到，但風險較低）

以下場景 pytest 已經覆蓋邏輯正確性，但這次會話沒有在瀏覽器裡實際點過，
建議你有空時抽查一次：

1. **`period_type='fixed'` 的日期輸入 UI**：新增專案時選「單次（起訖
   日）」，確認兩個日期輸入框正確顯示、送出後卡片正確顯示「起始日 ~
   結束日」文字（`ProjectsPanel.tsx::ProjectCard` 有這段邏輯，pytest 已
   驗證後端邊界正確，但沒有實際在瀏覽器裡選過日期輸入框）。
2. **`carryover_enabled`（預算結轉）checkbox**：目前 UI 只是存這個
   欄位，花費彙總（`list_projects`）**沒有**真的把上一期結轉金額疊加進
   `remaining`（PH13_PROJECT_SD.md 本身也沒有規定確切的結轉演算法），
   如果你預期這個開關要有計算效果，需要再另外討論演算法後補上。
3. **`GlobalEditDialogs.tsx`（從交易列表點「編輯」以外的全域入口，比如
   從首頁/日曆點交易編輯）**：程式碼改動跟 `TransactionsPage.tsx` 是
   同一套邏輯抄過去的（`onCreateTxProject`/`editTxProjects` 等），但這
   次沒有特別從那些入口單獨點過一輪，抽查一下掛專案在那邊也正常。
4. **手機 App（Flutter）**：本 Phase 只做 Web 端（使用者這次明確要求
   「使用者皆以 web 使用」），沒有動 `../BeeCount/` 專案的任何程式碼；
   mobile 透過既有 `/sync/push`/`/sync/full` 通用同步協議理論上能收到
   `project` entity 的變更（sync applier 已經照七步 SOP 登記），但**沒
   有實機驗證過 App 端能不能正確顯示/操作專案**，如果 App 端 UI 沒有
   對應畫面，這些資料只會靜默同步下去但沒有入口可看。

---

## 四、已知限制 / 故意不做的部分（對齊 PH13_PROJECT_SD.md §6）

- **統計專案**（Moze v4 條件式自動歸類規則引擎）：不在本 Phase 範圍。
- **記帳模式（Entry Mode Profiles）**：不在本 Phase 範圍。
- **首頁行事曆卡片**：本倉庫首頁沒有行事曆概念，`visible_on_home` 目前
  只落欄位、沒有接到任何首頁 UI（總覽頁本身也還沒有「專案卡片」這個
  區塊），這個開關目前是為未來擴充預留的欄位，勾不勾都不影響現有畫面。
