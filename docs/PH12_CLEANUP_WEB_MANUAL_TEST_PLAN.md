# Phase 12 系統性清理（需求 #12、#13、#14、#17）— 測試報告 + 手動測試清單

- 測試範圍：[PH6_USER_FEEDBACK_2026-08_SD.md](./PH6_USER_FEEDBACK_2026-08_SD.md) Phase 12
- 四項需求彼此獨立，合併在同一輪一次處理完。本輪環境：**有本機瀏覽器
  （Safari + MCP 自動化）+ 後端 pytest**，已對照 `localhost:5173`（前端）+
  `localhost:8080`（後端）實測操作一輪，見下方「一」。測試帳本用既有「測試
  帳本」，測完已把新建的規則/計畫刪除還原，帳本狀態與測試前一致。

---

## 一、已自動測試/驗證過的部分

### #12 拿掉 ¥ 符號

- **程式碼層**：4 套各自獨立的 `currencySymbol()` 實作（`format.ts`、
  `Amount.tsx`、`lib/currencies.ts` 原本的 Intl 版本、annual-report 5 個檔案
  各自內嵌的對照表）收斂成 **1 份**，放在 `lib/currencies.ts`，其餘全部改
  import。CNY/JPY 固定回傳空字串（不顯示符號），USD/EUR/HKD/GBP 維持原本
  固定符號不變（沒有改用 Intl 動態推導，避免像 `US$`/`JP¥` 這種依 locale
  變動的國家碼前綴讓其它幣別的顯示比現況多長一截）。
- ✅ `pnpm test:unit`：新增 `currencySymbol` 專屬測試（CNY/JPY 空字串、大小
  寫不敏感、其它已知幣別維持原符號、未知幣別空字串），既有
  `i18n.test.ts` 的 `formatBalanceCompact` 三個測試案例改成斷言不帶 ¥ 前綴
  （原本斷言 `'¥5万'`，現在斷言 `'5万'`）。全部通過。
- ✅ `pnpm build` 通過，全域 grep 確認程式碼裡已無 `¥` 字面量殘留（只剩註解
  裡的舉例文字，不影響 UI，予以保留）。
- **瀏覽器手測**：測試帳本本位幣是 TWD，不在 CNY/JPY 範圍內，這次操作沒有
  改變前後可見的視覺差異（TWD 本來就不顯示符號），沒有踩到真正的「之前
  顯示 ¥、現在不顯示」對照組——這部分主要靠上面的單元測試鎖住行為，見下方
  「二」請你在你自己 CNY/JPY 計價的帳本裡實際看一次。

### #13 交易搜尋日期預設改「全部」

- ✅ **有搜尋關鍵字時自動切「全部」**：交易頁搜尋框輸入關鍵字（例如
  「還款」），日期篩選立即從「今日」自動跳到「全部」（chip 高亮跟著切換），
  重新打 API 拿到全時間範圍內符合的交易。
- ✅ **關鍵字清空時自動回復「今日」**：清空搜尋框後，日期篩選自動回到
  「今日」。
- ✅ **使用者手動覆蓋會被尊重**：先輸入關鍵字（日期自動變全部），接著手動
  點擊「今日」chip，該次搜尋期間繼續輸入更多字元不會把日期篩選撤回「全部」
  ——只在「有沒有關鍵字」這個狀態真的發生轉換的那一刻才會自動套用，使用者
  手動選過之後在同一次搜尋內會一直保留到關鍵字被清空為止。
- ✅ 清空關鍵字後再重新輸入新的關鍵字，自動切「全部」的行為會重新生效
  （確認「手動覆蓋只在當次搜尋內有效」，不會永久卡住）。
- ✅ `pnpm build` 通過（這項改動主要是 `TransactionsPage.tsx` 內部 state
  邏輯，沒有拆成獨立可單元測試的純函式，靠上面瀏覽器操作直接驗證行為）。

### #14 系統自動產生的交易漏分類

- ✅ 後端：`WriteRecurringRuleCreateRequest`/`WriteInstallmentPlanCreateRequest`
  維持 schema 欄位選填（避免非轉帳/轉帳兩種語意混在一起用 Pydantic 硬性必填
  卡死），改成在 write endpoint 內用新的共用檢查
  `_assert_category_required(tx_type, category_id)` 擋：非轉帳週期性收支、
  分期付款（恆為 expense）建立時沒帶分類 → 400；PATCH 顯式把分類清空
  （`category_id: null`）也會被擋（同一份檢查函式，`update_recurring_rule_ep`/
  `update_recurring_rule_from_ep` 都有掛）。轉帳規則不受影響。
- ✅ `pytest tests/test_recurring_rules.py`、`tests/test_installment_plans.py`：
  新增 3 個 + 1 個專屬測試（建立不帶分類被拒、轉帳規則不受影響、PATCH 清空
  分類被拒且不動到既有值、分期付款建立不帶分類被拒），既有 20+ 個原本
  「建立時沒帶分類」的既有測試（原本能通過是因為分類還沒必填）全部補上
  `category_id`，確認語意不變、只是多帶一個必填欄位。
- ✅ **一次性回填腳本**（既有資料修復）：`scripts/
  backfill_recurring_installment_categories.py`，把上線前 `category_sync_id`
  是 NULL 的舊規則/計畫歸到使用者名下「未分類」專屬分類（跟
  `ensure_reward_category`/`ensure_refund_category`/`ensure_debt_category`
  同一套「找不到就建」模式，新增 `ensure_uncategorized_category`
  共用擴充）。走跟一般 web 寫入路徑相同的「sync push 等价」局部更新
  （`SyncChange` + `apply_change_to_projection`，只帶 `categoryId`），不是
  直接改 projection 表，其它裝置下次同步也能正確拉到。新增
  `tests/test_backfill_recurring_installment_categories.py`：用 mobile
  `/sync/push` 模擬「上線前建立的缺分類舊規則/計畫」，驗證 `--dry-run` 不寫
  入、正式跑會補上分類且轉帳規則不受影響、重複執行冪等（第二次跑找不到
  任何需要處理的資料）。
- ✅ 前端：`RecurringRulesPage.tsx`（非轉帳）、`InstallmentPlansPage.tsx`、
  `AccountDetailDialog.tsx`「帳單分期」建立表單都補上「未選分類」的送出前
  校驗（重用既有 `transactions.error.categoryRequired` 錯誤提示文案）。
- ✅ 瀏覽器手測：週期性收支頁「新增規則」填完金額但不選分類直接送出 →
  跳出「請選擇分類。」錯誤提示擋下；選了分類後能正常建立。分期付款頁同樣
  流程確認一致：不選分類擋下、選了分類後能正常建立（1200 元分 12 期、
  6% 年利率、等額本金 → 每期 106 元，數字合理，驗證了 #17 換算跟 #14
  校驗同時運作正常）。
- ✅ `pytest tests/ -q` 全量：除了兩個跟本次改動完全無關、在改動前
  （`git stash` 驗證過)就已經失敗的既有測試（`test_import_simple.py::
  test_accounts_parent_before_child_required`、`test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`），
  其餘全部通過。

### #17 百分比輸入統一寫成 `X%`

- 後端 `services/installment_amortization.py` 完全沒動，仍然吃小數分數
  （`0.06` = 6%/年）。前端新增共用轉換函式
  `interestRateToPercentDisplay`/`percentDisplayToInterestRate`（放
  `format.ts`，四個呼叫點共用同一份，避免各自複製轉換邏輯），四捨五入到
  固定精度避免 `0.06 * 100` 這類二進位浮點運算殘留雜訊數字。
- ✅ 四個輸入框（`TransactionsPanel.tsx` 分期切換區塊、
  `InstallmentPlansPanel.tsx` 建立表單 + 「調整利率」重新分期彈窗、
  `AccountDetailDialog.tsx` 帳單轉分期）欄位標籤都改成「年利率 (%)」，
  `step` 從 `0.001` 改成 `0.1`，顯示/送出換算全部接上共用函式。
- ✅ `pnpm test:unit`：新增 `interestRate.test.ts`，覆蓋小數↔百分比雙向轉換、
  來回轉換無浮點雜訊、非法輸入回退空字串,共 4 個案例全過。
- ✅ 瀏覽器手測：分期付款頁新增計畫，年利率輸入框標籤顯示「年利率 (%)」，
  輸入 `6`（不是 `0.06`）→ 建立成功後每期金額顯示 106（1200 元 12 期
  等額本金 + 6% 年利率的合理計算結果），確認換算全鏈路（UI 輸入 → 送出
  → 後端計算 → 讀回顯示）都正確。「調整利率」彈窗同樣改成「年利率 (%)」
  標籤。

---

## 二、需要你在正式帳本手動複測的項目

以下場景這輪測試帳本資料量小、幣別不對、或屬於長期觀察類，需要你在正式
帳本走一遍：

1. **CNY/JPY 帳本的 ¥ 符號移除**：這次測試帳本是 TWD 計價，沒有真正的
   「之前有 ¥、現在沒有」對照畫面。麻煩你在有 CNY 或 JPY 帳本/交易的地方
   （首頁資產總覽、交易列表、年度報告海報）看一次，確認金額都是純數字、
   沒有殘留 ¥ 符號；同時確認 USD/EUR/HKD/GBP 這幾個幣別的符號（`$`/`€`/
   `HK$`/`£`）維持跟改版前一樣，沒有被連帶拿掉或變成 `US$`/`JP¥` 這種
   意外變化。
2. **既有資料回填腳本**：`scripts/backfill_recurring_installment_categories.py`
   這次**只在 pytest 的隔離測試資料庫跑過**，**沒有對你的正式 / 開發資料庫
   執行過**。如果你的正式帳本裡有上線前建立的週期性收支規則或分期付款
   計畫、分類欄位是空的，需要你自己找時間手動跑一次（建議先
   `--dry-run` 看清單，確認要處理的規則/計畫數量符合預期,再正式執行）：
   ```
   cd /path/to/BeeCount-Cloud
   python -m scripts.backfill_recurring_installment_categories --dry-run
   python -m scripts.backfill_recurring_installment_categories
   ```
   跑完後建議看一下週期性收支/分期付款管理頁，確認那些規則/計畫多了一個
   「未分類」分類標籤，且**下一筆**新產生的交易也正確帶上這個分類（舊的
   已生成交易不會被回頭改寫，見腳本 docstring 說明）。
3. **搜尋日期自動切換的長期觀察**：這次只測了「打字→自動切全部→清空→
   回今日」的即時互動，沒有測試跨瀏覽器分頁/重新整理後，localStorage 裡
   舊版（改版前存的）篩選紀錄的還原情境。如果你電腦上的瀏覽器 localStorage
   裡本來就存有「有關鍵字 + 今日」這種改版前才可能出現的組合，麻煩重新整理
   交易頁一次，確認還原後日期會被自動修正成「全部」（不會維持不一致的
   「有關鍵字卻只看今日」狀態）。
4. **分期付款/週期性收支既有計畫的「調整利率」/「編輯」**：這次只測了
   全新建立的情境，既有計畫按「調整利率」重新分期時，輸入框預設值固定是
   `0`（沿用改版前就有的既有行為，不是 Phase 12 新增的問題），不會自動
   帶入這個計畫原本的利率——如果你覺得這裡應該要預填原始利率的百分比顯示,
   請告訴我,這是可以再補的細節,不屬於這次「拿掉 0.0X 打字困擾」的範圍。

如果以上任何一項跟預期不符，麻煩告訴我具體是哪一項 + 截圖，我再接著修。
