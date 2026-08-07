# Phase 11 新增交易表單改造（需求 #9、#10、#11）— 測試報告 + 手動測試清單

- 測試範圍：[PH6_USER_FEEDBACK_2026-08_SD.md](./PH6_USER_FEEDBACK_2026-08_SD.md) Phase 11
- 依賴 Phase 10：帳戶選擇彈窗重用了 Phase 10 做的 `AccountListRow` 緊湊列樣式
  （已抽成共用元件 `frontend/packages/web-features/src/components/AccountListRow.tsx`，
  `AccountsPanel.tsx` 與新的 `AccountPickerDialog.tsx` 都從這裡 import，行為
  100% 沿用 Phase 10）。
- 本輪環境：**有本機瀏覽器（Safari + MCP 自動化）+ 後端 pytest**，已對照
  `localhost:5173`（前端）+ `localhost:8080`（後端，已跑過
  `alembic upgrade head` 套用新 migration）實測操作一輪，見下方「一」。用的
  是既有「測試帳本」，測試完已把新建的交易/分類/標籤/備註改動全部刪除或還原，
  帳本狀態與測試前一致。

---

## 一、已自動測試/驗證過的部分

### 後端（pytest）

- 新增 `alembic/versions/0038_tx_merchant.py`：`read_tx_projection` 加
  `merchant`（nullable Text）欄位，`alembic upgrade head` 在本機 SQLite 執行
  成功，`heads` 只有一個（無分叉）。
- 依 CLAUDE.md SOP 的「新增/修改 Sync Entity 檢查清單」7 個位置全部確認並更新：
  1. **DB & Migration**：`src/models.py::ReadTxProjection.merchant` +
     上述 migration。
  2. **Projection**：`src/projection.py::upsert_tx` 新增
     `"merchant": _as_str(payload.get("merchant"))`。
  3. **Sync Applier**：`src/sync_applier.py::_MERGE_SPECS["transaction"]`
     新增 `("merchant", "merchant")`（mobile `/sync/push` partial update
     缺鍵保留既有值的核心註冊點）。
  4. **Write Routers**：`src/schemas.py`
     （`WriteTransactionCreateRequest`/`WriteTransactionUpdateRequest`/
     `ReadTransactionOut` 都補上 `merchant`）、
     `src/snapshot_mutator.py`（`create_transaction` 寫入邏輯 +
     `update_transaction` 的 partial-update `mapping` 字典）、
     `src/routers/write/_shared.py::_projection_row_to_tx_dict`（web PATCH
     快路徑的「prev_item」還原，這處最容易漏，漏了會導致下一次只改別的欄位
     時把 `merchant` 靜默清空）、`src/routers/write/transactions.py`
     （建交易同時建週期性規則時的逐期 payload）、
     `src/routers/write/transactions_batch.py`（批次匯入交易的 schema +
     payload 組裝）。
  5. **Read Routers**：`src/routers/read/ledgers.py` /
     `src/routers/read/workspace.py` 的 `ReadTransactionOut`/
     `WorkspaceTransactionOut` 建構處補 `merchant=row.merchant`；順手把
     `merchant` 也加進兩處既有的關鍵字搜尋 `ilike` 條件（跟 `note` 同組），
     讓「商店」也能被交易搜尋框找到。
  6. **Snapshot Builder**：⚠️ `src/snapshot_builder.py` 的 `tx_stmt` SELECT
     + tuple unpack + item dict build 三處都加了 `merchant`（這是 CLAUDE.md
     特別警告最容易漏的一步）。
  7. **測試**：新增 `tests/test_tx_merchant.py`，覆蓋：
     - web POST 建交易帶 `merchant`，read 端點能讀回、projection 落庫正確。
     - web PATCH 只改 `note`（不帶 `merchant`）時 `merchant` 保留舊值；
       顯式傳 `merchant: null` 能清空。
     - mobile `/sync/push` 對同一筆交易做 partial update（只帶 `note`）時
       `merchant` 保留舊值（同 `test_deferred_posting.py` 的既有測試模式）。
- `pytest tests/ -q` 全量跑過：**除了兩個跟本次改動完全無關、在改動前
  （`git stash` 驗證過）就已經失敗的既有測試**（`test_import_simple.py::
  test_accounts_parent_before_child_required`、`test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`，前者
  疑似既有 bug、後者疑似跟系統當下日期有關的既有 flaky 測試），其餘全部通過。

### 前端（pnpm build / test:unit）

- `pnpm -C apps/web build`（`tsc -b && vite build`）多次跑過，全程 0 型別
  錯誤。
- `pnpm -C apps/web test:unit`：73 個既有測試全部通過（含 i18n 三語 key
  完整性測試 —— 本次新增的所有 i18n key 三語都已補齊）。
- **已知落差**：本專案目前沒有 `@testing-library/react`/`jsdom` 之類的元件
  測試基礎設施，既有 `test:unit` 全部是純函式測試（`forms.ts`/
  `assetAggregation.ts` 等）。這次新增的互動邏輯（帳戶彈窗選擇、分類/標籤
  搜尋、表單內新增、帳戶必選校驗）大多是元件內的 state/事件處理，沒有拆成
  獨立可單元測試的純函式，所以**沒有**新增對應的 `pnpm test:unit` 案例 ——
  改用下方「瀏覽器自動化」的方式做端對端行為驗證，涵蓋面比淺層 unit test
  更貼近真實使用情境，但嚴格來說不是 SD 原文要求的「單元測試」形式，如果你
  希望之後補上元件測試基礎設施，這是一個可以討論的後續項目。

### 瀏覽器自動化（Safari MCP，操作方式：既有「測試帳本」，測完已清空還原）

- ✅ **帳戶必選改彈窗**：交易表單「帳戶」欄位從原生 `<Select>` 改成彈窗
  按鈕，點開後彈窗內容跟 Phase 10 帳戶列表頁一致的樣式（分組 + 圖示 + 名稱
  + 巢狀子帳戶），有搜尋框、有「不選擇帳戶」選項；轉帳的「轉出帳戶」/
  「轉入帳戶」各自開一個同款彈窗（不帶「不選擇帳戶」，跟既有轉帳兩端必選
  的規則對齊）。
- ✅ **帳戶必選校驗**：非轉帳交易不選帳戶直接送出 → 跳出「請先選擇帳戶。」
  錯誤提示，擋下送出。
- ✅ **舊資料相容**：既有一筆 mobile 匯入風格的無帳戶交易（本輪用測試帳本
  裡「還款」那筆），編輯它、只改備註（完全沒碰帳戶欄位）→ 能正常存檔，
  沒有被強制要求補選帳戶 —— 驗證了「僅新建或使用者主動變更帳戶時才強制」
  的邊界情境正確。
- ✅ **主動清空既有帳戶會被擋**：另建一筆本來有掛帳戶的交易，編輯時把帳戶
  改選成「不選擇帳戶」（主動清空）後存檔 → 一樣跳出「請先選擇帳戶。」被擋
  下，驗證「使用者主動變更帳戶」這個分支（不只是「從無到有」,「從有變無」
  也算主動變更）判斷正確。
- ✅ **分類搜尋 + 表單內新增**：分類選擇彈窗新增搜尋框，輸入不存在的分類名
  後出現「新增「xxx」」內嵌按鈕，點擊後即時建立分類（呼叫既有
  `createCategory` API）並自動寫回表單的分類欄位（不用使用者再手動點一次
  選取），彈窗內清單也同步出現剛建立的分類供之後重複使用。
- ✅ **標籤搜尋 + 表單內新增**：同上，標籤選擇彈窗輸入不存在的標籤名後
  出現「新增「xxx」」，點擊後建立成功並自動勾選進表單的標籤清單。
- ✅ **商店欄位**：新增交易表單「商店」輸入框（選填），填入後送出，
  交易列表/交易詳情彈窗（新增了「商店」一列，只在有值時顯示）、編輯表單
  重新開啟後都正確顯示剛才填的商店名稱 —— 驗證了建立 → 落庫 → 讀取 → 編輯
  回填的完整迴路。
- ✅ **備註欄位位置**：備註從原本緊鄰「實際入帳日」的位置，往上移到跟
  「商店」相鄰、緊接在帳戶欄位之後（分類/帳戶/商店/備註/關聯欠款 的順序）。
- ✅ 整輪操作過程 console 沒有出現跟本次改動相關的 JS 錯誤（有零星
  `Failed to load resource: 500` 訊息，但時間點對應到後端 `uvicorn --reload`
  因為持續改 `src/` 檔案而重啟的窗口，改動穩定後的功能操作全部回應
  200/成功，判斷是開發環境重載造成的暫時性雜訊，不是本次改動引入的
  真實錯誤）。

---

## 二、需要你在正式帳本手動複測的項目

以下場景這輪測試帳本資料量小、或屬於「風格/手感」判斷，需要你在正式帳本
用截圖情境再走一遍：

1. **帳戶彈窗在大量帳戶時的手感**：你的正式帳本有 20 張信用卡、15 個銀行
   帳戶，記一筆交易時打開帳戶彈窗，確認：
   - 搜尋框能快速定位到想要的帳戶（尤其子帳戶，搜尋是攤平顯示、不需要先
     展開父帳戶）；
   - 分組 + 巢狀子帳戶的視覺跟 Phase 10 帳戶列表頁一致，沒有因為在彈窗裡
     （空間更小）而顯得擁擠。
2. **分類/標籤表單內新增的實際手感**：正式帳本分類/標籤數量多的情境下，
   搜尋 + 新增的操作流程是否夠順手；新增後的分類/標籤要在**下一次**打開
   分類/標籤管理頁時確認有正確同步過去（本輪只驗證了同一個 session 內立即
   可見，沒有驗證跨頁面/重新整理後的一致性，理論上跟既有 create API +
   `retryOnConflict` 走的是同一條路徑，應該沒問題，但建議你實際走一遍）。
3. **轉帳交易的必選校驗**（本來就存在，這次沒改動）維持原樣，建議順手
   確認一次沒有被連帶改壞。
4. **Phase 8 的信用卡回饋規則帳戶選擇**：SD 文件裡提到 Phase 8 的規則帳戶
   選擇（`CardRewardRulesSection.tsx`）預計等 Phase 11 完成後也改用同一個
   `AccountPickerDialog`，**這次沒有做**（範圍限縮在 Phase 11 明確列出的
   #9/#10/#11 三項），如果你希望現在一併做掉這一小塊，請另外告訴我。
5. **商店欄位的既有交易回填**：正式帳本裡歷史交易（新欄位上線前建立的）
   商店欄位一定是空的，屬預期行為（`merchant` 欄位是新增的，舊資料 NULL），
   確認這類舊交易的詳情頁/編輯表單顯示正常（不顯示商店那一列，不會顯示
   "null" 之類的字樣）。
6. **CSV 匯出**：這次**沒有**把 `merchant` 加進 CSV 匯出的欄位（
   `src/routers/read/workspace.py` 的 CSV export 那段沒有動,只加了交易
   關鍵字搜尋支援商店），如果你需要匯出檔案也帶上商店欄位，請告訴我再補。

如果以上任何一項跟預期不符，麻煩告訴我具體是哪一項 + 截圖，我再接著修。
