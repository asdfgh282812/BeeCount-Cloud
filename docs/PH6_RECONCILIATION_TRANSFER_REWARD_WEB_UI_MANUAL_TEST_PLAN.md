# Phase 6 對帳模式漏單（需求 #1、#7）— 測試報告 + 手動測試清單

- 對應設計文件：[PH6_USER_FEEDBACK_2026-08_SD.md](./PH6_USER_FEEDBACK_2026-08_SD.md) Phase 6
- 修改範圍：
  - 後端 `src/routers/read/ledgers.py::get_account_statement`：查詢條件從
    單一 `tx_type.in_(["expense","income"])` 改成 `OR` 分支，放行「轉入這張
    卡/群組」的轉帳（轉出不放行）；`signed`/分組小計改用轉帳專屬邏輯
    （轉入比照 income 記為負值）；`statement_total`（「新增消費」）刻意排除
    轉帳，對齊 `credit_card_billing.compute_cycle_period_billing.new_spend`
    的既有口徑。
  - 後端 `src/schemas.py::StatementTransactionOut` 新增 `is_reward: bool`
    欄位（分類名稱等於 `services.card_rewards.REWARD_CATEGORY_NAME` 時為
    `true`），前端顯示「回饋」標籤辨識來源。
  - 前端 `frontend/apps/web/src/components/dialogs/AccountReconciliationSection.tsx`：
    `signed` 正負號計算新增 `transfer` 分支；清單列新增「轉帳」「回饋」
    徽章。
  - `frontend/packages/api-client/src/types.ts::StatementTransaction` 新增
    `is_reward` 欄位；三語系（en/zh-TW/zh-CN）新增
    `statement.row.transferBadge`/`statement.row.rewardBadge` 文案。
- **需求 #7 二次改版（同一天，使用者看過第一版後提出的修改）**：回饋金
  雖然要出現在對帳明細裡，但同一個回饋方案（rule）這期帳單內的所有回饋
  交易要合併成一列只顯示總金額，點進去才展開看這個方案這期賺到哪些消費
  的回饋（比照使用者附的 Moze 截圖）。修改範圍：
  - 後端 `src/schemas.py::StatementTransactionOut` 新增
    `reward_rule_id`/`reward_rule_label`/`member_tx_ids` 三個欄位。
  - 後端 `src/routers/read/ledgers.py::get_account_statement`：新增查
    `CardRewardPayout.payout_tx_sync_id → rule_sync_id` 反查表，把這期帳單
    內同一個 `rule_sync_id` 的回饋交易合併成一列（金額加總、`reconciled_at`/
    `deferred_posting_at` 要全部成員都有值才算合併列本身「已確認/已延後」）；
    查不到對應規則的回饋交易（手動記的「回饋金」分類交易）維持合併前的
    單筆顯示。
  - 前端合併列的確認/延後入帳改成對 `member_tx_ids` 裡每一筆各自呼叫既有
    的 `PATCH .../transactions/{id}`（沒有新增批次 write endpoint）；點擊
    合併列開一個新的唯讀明細彈窗（`RewardGroupDetailDialog`），重用既有的
    `GET .../card-reward-rules/{rule_id}/transactions` 端點（`period_offset
    = cycleOffset - 1`，跟 `AccountDetailDialog` 既有換算慣例一致），不是
    新增後端 endpoint。

- **需求延伸（第三輪，使用者反饋）**：對帳畫面除了「新增遺漏的交易」以外，
  也要能「編輯既有明細」——對帳時常發現某一筆金額對不上（少算/多算），
  要能直接改；回饋金這個欄位，明細彈窗要能針對單筆單筆修改回饋金額（銀行
  實際入帳的回饋跟系統算出來的可能有取整差異）。修改範圍：
  - 後端 `src/schemas.py::ReadCardRewardQualifyingTxOut` 新增
    `payout_tx_id`：這筆消費實際結算入帳的回饋交易 sync_id（只有逐筆結算
    且已到期入帳才有值，`period_end` 或還沒入帳固定 `None`）。有值時
    `reward_amount` 改讀該筆交易目前的**實際金額**（不是重新按公式算），
    避免使用者編輯過之後被算回原值。
  - 後端 `src/routers/read/ledgers.py::get_card_reward_rule_transactions`：
    反查 `CardRewardPayout.dedup_key → payout_tx_sync_id`，用來解析每個
    qualifying tx 對應的實際回饋交易 id + 目前金額。
  - 前端 `AccountReconciliationSection.tsx`：
    - `StatementRow` 新增編輯（鉛筆）按鈕——只在這一列本身就對應唯一一筆
      真交易時顯示（`member_tx_ids.length <= 1`，合併後的回饋方案列沒有
      單一交易可編輯，要點進明細彈窗逐筆改）；點擊透過
      `fetchWorkspaceTransactions({ txSyncId })` 補齊完整欄位後
      `dispatchOpenEditTx` 開全域既有的編輯交易彈窗（`GlobalEditDialogs`），
      沒有另外做一個對帳專用的編輯表單。
    - `AccountStatementSection` 新增 `useSyncRefresh(reload)`——全域編輯
      彈窗存檔後不會直接回呼這裡，靠訂閱 `sync_change` 事件自動重新拉一次
      對帳清單（跟其它 `*Page` 同款既有模式）。
    - `RewardGroupDetailDialog` 每個項目新增行內編輯：`payout_tx_id` 非空
      才顯示鉛筆，點擊展開 `<input type="number">` + 確認/取消，直接
      `updateTransaction(...,{amount})` PATCH 那筆回饋交易，成功後重新拉
      這個彈窗自己的明細 + 呼叫 `onChanged`（=父層 `reload`）讓合併列的
      總額也跟著更新。
  - `tests/test_card_rewards.py` 新增
    `test_card_reward_rule_transactions_payout_tx_id_and_editable_amount`：
    驗證還沒入帳時 `payout_tx_id` 固定 `None`；入帳後有值且
    `reward_amount` 等於實際交易金額；PATCH 那筆交易金額後再打一次明細
    端點，看到的是編輯後的值（12.0）而不是重算的公式值（10.0）。

---

## 一、已自動化驗證的部分（pytest，全部通過）

```
. .venv/Scripts/activate && python -m pytest tests/test_reconciliation.py -q
```

`tests/test_reconciliation.py` 新增三個測試（沿用既有檔案，命名對齊既有
慣例）：

- `test_statement_includes_transfer_in_but_excludes_transfer_out`：轉入這張
  卡的轉帳出現在清單、`account_id` 正確填「轉入的那張卡」；轉出這張卡的
  轉帳不出現；`statement_total`（新增消費）不含轉帳金額。
- `test_statement_confirming_transfer_in_reduces_confirmed_total`：確認一筆
  轉入交易後，`confirmed_count`=1、`confirmed_total`=負值（比照 income
  口徑）。
- `test_statement_flags_reward_category_transaction_as_is_reward`：分類名稱
  為「回饋金」的交易在 statement 回應裡 `is_reward=true`。
- `test_statement_merges_same_rule_reward_payouts_into_one_row`（需求 #7
  二次改版）：實際建一條回饋規則、建兩筆消費、呼叫
  `card_reward_payout.materialize_due_card_reward_payouts` 真的觸發自動
  入帳（不是手動塞分類），驗證這期帳單合併成一列
  （`reward_rule_id`/`reward_rule_label`/`member_tx_ids` 長度 2、金額加總
  正確）；確認其中一筆成員時合併列還不算「已確認」，兩筆都確認後合併列
  才變已確認、`confirmed_total` 正負號正確。

全量回歸：

```
. .venv/Scripts/activate && python -m pytest tests/ -q
```

除兩個跟本次改動**無關**、修改前就已存在的既有失敗（已用 `git stash`
在乾淨工作樹上覆核確認，非本次改動引入）之外全數通過：

- `tests/test_import_simple.py::test_accounts_parent_before_child_required`
- `tests/test_recurring_rules.py::test_recurring_occurrence_update_overridden_skipped_by_update_from`

前端：

```
cd frontend && pnpm -C apps/web build && pnpm -C apps/web test:unit
```

`pnpm build` 通過；`pnpm test:unit`（10 個測試檔、73 個測試）全數通過，
包含 `src/i18n.test.ts`（驗證三語系 key 沒有遺漏，含本次新增的
`transferBadge`/`rewardBadge`）。

---

## 二、已在瀏覽器實測過的部分（本輪自動完成，非僅憑程式碼推論）

用 `測試帳本`（既有本地測試資料）操作，信用卡群組「國泰」（帳單日
每月 5 號、還款日每月 20 號）下掛「蝦皮聯名卡」「cube」兩張子卡：

1. 在「蝦皮聯名卡」上建立：①一筆 100 元消費；②一筆現金戶 → 蝦皮聯名卡
   的 40 元轉帳（轉入）；③一筆蝦皮聯名卡 → 現金戶的 15 元轉帳（轉出）；
   ④一筆分類為「回饋金」的 5 元收入。
2. 打開「國泰」詳情彈窗 → 對帳模式，確認：
   - 帳單筆數 = 3（轉出的 15 元那筆**正確被排除**，沒有出現在清單裡）。
   - 「新增消費」= 95（= 100 − 5，轉入的 40 元**正確被排除**在這個統計
     之外，跟帳單頂部「新增花費」欄位數字一致）。
   - 轉入那筆顯示「轉帳」徽章，金額用綠色（income 同款配色，代表減少
     應繳）。
   - 回饋金那筆顯示「回饋」徽章。
   - 點擊回饋金那筆的確認勾選按鈕：「已確認筆數」變 1、「已確認金額」
     變 −5（正負號符合 income 口徑），標題旁的 `1/3` 標籤同步更新；再點
     一次能取消確認，數字歸零。
3. 確認過程中順手發現並修正一個環境問題（跟本次程式改動本身無關，但
   會導致誤判「改動沒生效」）：本地 Chrome 對 `localhost:5173` 曾註冊過
   PWA Service Worker，快取了舊版前端資源，導致新加的「對帳模式」入口
   完全不出現。已用 `navigator.serviceWorker.getRegistrations()` +
   `caches.delete()` 清掉；**如果你自己手測時也發現改動「看起來沒生效」，
   先在瀏覽器 DevTools → Application → Service Workers 檢查有沒有殘留的
   registration，或直接開無痕視窗測試，不要先懷疑程式碼邏輯。**

### 需求 #7 二次改版（回饋方案合併顯示）實測記錄

用同一份 `測試帳本` 底下既有的「國泰」信用卡群組（子卡「cube」上有一條
`3%` 回饋規則，`settlement_type=immediate_after_tx`）：

1. 打開「國泰」→ 對帳模式，確認 2026-08-03/08-04 兩筆本來各自獨立顯示的
   回饋交易（各 24 元、29.7 元）合併成**一列**：標題顯示規則名稱「3%」、
   帶「回饋」「共2筆」兩個徽章 + 展開箭頭、金額顯示加總 53.7；帳單筆數
   從合併前的 5 筆變成合併後的 4 筆。
2. 點擊這一列，開出明細彈窗：標題「3%」，列出兩筆原始消費（800 元 →
   +24、990 元 → +29.7），數字跟規則本身的 3% 費率吻合，讀取的是既有
   `card-reward-rules/{id}/transactions` 端點，不是新端點。
3. 點擊合併列的確認勾選按鈕:「已確認筆數」變 1、「已確認金額」變
   −53.7（正負號符合 income 口徑）——驗證了前端對 `member_tx_ids` 裡
   **兩筆**回饋交易各自呼叫 `PATCH .../transactions/{id}` 的批次邏輯正確
   生效（不是只確認了其中一筆）；再點一次能整組取消確認，數字歸零。
4. 測試過程中發現並修正兩個問題：
   - **明細彈窗一開始打 400**：`RewardGroupDetailDialog` 原本把對帳彈窗
     查詢用的 `billingAccountId`（account_group 場景下是「國泰」群組本身
     的 id）當成 `accountId` 傳給 `card-reward-rules/{rule_id}/transactions`
     端點，但該端點會校驗 `account_id` 必須等於
     `rule.account_sync_id`（規則實際綁定的子卡,這裡是「cube」),群組 id
     一定對不上,回 400「rule_id does not belong to this account」。修正
     為改傳合併列自己的 `account_id`（已經是後端算好的實際子卡
     id)。**如果之後還有類似「彈窗開著查某個規則/實體明細」的功能,要注意
     account_group 場景下「查詢用的 account_id」跟「實體真正綁定的
     account_id」是兩個不同的東西,不能互換。**
   - **本地後端 dev server 沒吃到新程式碼**：本輪一開始在瀏覽器測到的
     行為完全沒有合併(兩筆回饋金還是分開顯示、`reward_rule_id` 等新欄位
     整個從 API 回應裡消失),一度以為是程式邏輯錯誤,後來用
     `curl .../openapi.json` 比對 `StatementTransactionOut` 的 schema
     發現本地跑著的 uvicorn 進程是**沒有帶 `--reload` 旗標**啟動的舊
     進程(上一輪對話啟動、之後改的程式碼完全沒生效),重啟該進程後才
     恢復正常。**這是繼 Service Worker 快取陷阱之後第二個「改動看起來
     沒生效」的環境陷阱——手測前如果懷疑程式碼沒生效,除了清 Service
     Worker,也要確認本地 API server 是用 `--reload` 啟動、或乾脆手動
     重啟一次,不要花時間排查程式碼邏輯。**

### 需求延伸（第三輪，可編輯既有明細/回饋金額）實測記錄

沿用同一份「國泰」信用卡群組、子卡「cube」上的 `3%` 回饋規則：

1. 打開對帳模式，非合併列（`測試分類1 800`）旁多了一個鉛筆按鈕；合併後
   的回饋方案列（`3% 回饋 共2筆`）**沒有**鉛筆按鈕（符合預期——沒有單一
   真交易可編輯）。
2. 點鉛筆按鈕，開出全域既有的「更新交易」彈窗，欄位正確回顯（金額 800、
   分類、帳戶 cube、已勾選的 3% 回饋規則）；把金額改成 850 存檔後，彈窗
   關閉、跳出「操作成功」toast，對帳清單**自動**重新整理（沒有手動重整
   頁面）：該列變成 850、「新增消費」小計從 1,736.3 變 1,786.3——驗證了
   `useSyncRefresh` 接住全域編輯彈窗的存檔事件。
3. 點合併後的回饋方案列展開明細彈窗，兩筆原始消費（800→+24、990→+29.7）
   旁都有鉛筆按鈕；點第一筆的鉛筆，輸入框帶出目前值 24，改成 25 存檔後
   該行即時變 +25；關掉明細彈窗回到對帳清單，合併列金額從 53.7 自動變
   54.7、「新增消費」小計從 1,786.3 變 1,785.3——驗證了明細彈窗編輯成功
   後透過 `onChanged` 讓父層對帳清單也一起重新整理。
4. 測試結束後把兩筆改動都手動改回原值（850→800、25→24），確認畫面回到
   跟測試前一致的 800 / 53.7 / 1,736.3，避免污染測試帳本既有資料。

### 需求延伸未覆蓋到的角度

- **`period_end` 類型規則的回饋金額編輯**：整期一次性入帳沒有逐筆對應的
  `payout_tx_id`（設計上就不會顯示鉛筆——見 schemas.
  `ReadCardRewardQualifyingTxOut` docstring），這條路徑本輪沒有實際點過
  瀏覽器確認「明細彈窗正確不顯示編輯按鈕」。
- **轉帳列的編輯按鈕**：本輪只實測了一般支出列（`測試分類1`），轉帳列
  （`未分類 轉帳`）理論上鉛筆按鈕行為一致（`dispatchOpenEditTx` 對
  `tx_type='transfer'` 走既有的轉帳表單），但沒有實際點開驗證過。

---

## 三、建議你自己再過一遍的清單（本輪環境/時間限制沒覆蓋到的角度）

1. **深色/淺色主題切換**：確認「轉帳」「回饋」「共N筆」徽章、展開箭頭、
   新增的鉛筆編輯按鈕在兩種主題下對比度都清晰可辨。
2. **語言切換**（简中/繁中/英文）：確認徽章文案都正確顯示對應語言，沒有
   殘留 key 名稱（`statement.row.rewardCount`/`statement.action.edit`/
   `statement.rewardDetail.notice.updated` 是新增的翻譯 key）。
3. **手機寬度**：對帳模式清單一行塞了勾選按鈕 + 方案名稱/分類 + 三個徽章
   + 展開箭頭 + 金額 + 鉛筆編輯 + 延後按鈕，窄螢幕下確認不會擠壓變形或
   文字截斷到看不出徽章；明細彈窗本身、明細彈窗裡的行內編輯輸入框也要在
   窄螢幕下檢查。
4. **獨立信用卡（非群組）的情境**：本輪的合併顯示/編輯測試只在
   `account_group` 場景下的子卡上測過，建議找一張沒有掛靠群組的獨立信用
   卡，重複一次「同方案多筆回饋合併成一列、點開看明細、編輯金額」的驗證。
5. **「延後入帳」按鈕對合併列的行為**：本輪只測了確認勾選的批次生效，
   沒有測試點合併列的「延後入帳」是否正確對 `member_tx_ids` 裡每一筆都
   套用同一個目標日期（程式邏輯跟確認勾選同一套 `Promise.all` 寫法，理論
   上一致，但沒有實測）。
6. **`period_end`（整期結算)類型的回饋規則**：本輪只測了
   `immediate_after_tx`（逐筆結算,明細彈窗直接讀每筆回饋交易自帶的
   `reward_source_tx_sync_id`）。`period_end` 類型的回饋交易沒有這個欄位,
   明細彈窗會改用 `card_rewards._qualifying_transactions` 重新計算這期
   合格消費——這條路徑本輪沒有實際點過瀏覽器,建議額外設一條
   `period_end` 規則測一次明細彈窗是否正確展開、且確認不出現編輯按鈕。
7. **規則被刪除後,舊回饋交易還在的情境**：如果使用者刪掉一條已經入帳過
   回饋的規則,合併列還是會合併顯示(靠 `CardRewardPayout` 表,不依賴規則
   是否還存在),但 `reward_rule_label` 會是 `null`(改顯示分類名稱「回饋
   金」當標題)、點擊展開明細彈窗會打到後端的
   `get_card_reward_rule_transactions`(404 card reward rule not found)、
   前端顯示「載入失敗,請稍後再試」——這是預期內的降級行為,但沒有實際
   刪一條規則測過。
