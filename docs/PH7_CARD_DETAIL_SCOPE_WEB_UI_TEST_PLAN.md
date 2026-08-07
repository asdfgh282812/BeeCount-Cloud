# Phase 7 單卡片詳情顯示範圍（需求 #2、#3）— 測試報告 + 手動測試清單

依 [`PH6_USER_FEEDBACK_2026-08_SD.md` § Phase 7](./PH6_USER_FEEDBACK_2026-08_SD.md#phase-7單卡片詳情顯示範圍需求-23) 實作，範圍：

- **#2**：已掛靠群組的子卡打開自己的詳情頁時，不該借用主帳戶/其它兄弟卡
  的合併金額（使用者截圖「永豐Sport卡」詳情頁卻看到四張卡的子卡明細）。
- **#3**：主帳戶（account_group）詳情頁的紅利回饋清單太長（N 張子卡各自
  疊一個區塊），需收合成一顆摘要按鈕。

## 程式改動摘要

### 後端（`period_new_spend`/`remaining_due` 補到 member 層級）

單卡自己的「本期新增花費」在既有 API 裡完全沒有拆到子卡層級（只有整組
合併的 `period_new_spend`），要做到「子卡詳情頁顯示自己的數字」就必須先
補這個缺口——嚴格來說已經超出 SD 原本寫的「純前端」範圍，但沒有這塊資料
前端只能繼續借用合併數字，等於沒真正修好 #2，所以一併做了：

- `src/services/credit_card_billing.py::compute_cycle_period_billing`：
  `new_spend` 查詢改成同時按 `account_sync_id` 分組，新增
  `per_member_new_spend: dict[str, float]` 回傳欄位（單一查詢算完，不多打
  一次 DB）。
- `src/schemas.py::ReadAccountBillingMemberOut`：新增
  `period_new_spend: float` + `remaining_due: float` 兩個欄位（後者取自
  `compute_group_billing` 原本就有算、但沒有對外暴露的
  `per_child_remaining_due`）。
- `src/routers/read/ledgers.py::get_account_billing_summary`：`period`
  改到 `members_out` 組裝之前算，把上面兩個欄位塞進每個 member。
- `frontend/packages/api-client/src/types.ts::AccountBillingMember`：對應
  補上這兩個欄位的型別。

### 前端

- `AccountDetailDialog.tsx::useAccountBilling()`：新增衍生欄位
  `ownMember`（`isBillingChild` 時從 `summary.members` 挑出這張卡自己那筆）。
- `AccountStatsHeader`：`isBillingChild` 時改顯示 2 格「自身資料」（本期
  新增花費 + 本卡應繳），不再顯示整組合併的 6 格帳單網格。
- `CreditCardBillingSection`：
  - 收合狀態的「目前應繳」一行，子卡改顯示自己的 `remaining_due`
    （文案也改成「本卡應繳」）。
  - 展開後看到的仍是整組合併帳單網格（使用者主動點開才看到，符合 SD
    「僅在使用者主動展開才顯示」的精神），子卡情境下加一行提示文字
    「以下為主帳戶合併帳單金額，非本卡自己的數字」，避免跟上面「自身
    資料」的數字對不起來時使用者誤以為是 bug。
  - 「子卡明細」清單（原本不分青紅皂白列出所有兄弟卡)一律只在
    `!isBillingChild`（瀏覽的就是主帳戶/獨立卡本身）時顯示。
- `CardRewardRulesSection.tsx`：`account_group` 分支不再逐卡渲染 N 個
  `SingleCardCardRewards` 區塊，改成新元件 `GroupCardRewardsSummary`——
  一行摘要按鈕（「紅利回饋（N 張卡）」+ 本期回饋合計金額），點擊後彈出
  既有的 Dialog 樣式，內容才是原本的 N 個區塊。摘要金額獨立打一輪
  `fetchCardRewards`（跟展開後每張卡自己重新 fetch 有小重複，換取「按鈕
  不用等點開才有數字」，見程式內註解）。`highlightRuleId`（交易詳情「使用
  回饋」跳轉）比對邏輯同步搬進摘要元件，命中哪張卡就自動展開摘要 dialog。
- 新增 i18n key（zh-TW/zh-CN/en 三語系都補齊）：
  `cardBilling.selfRemainingDue`、`cardBilling.groupMergedHint`、
  `cardRewards.groupSummary.title`。

## 自動化測試

- **後端**：`tests/test_credit_card.py::test_billing_summary_merges_children_and_computes_due_amount`
  擴充驗證 `members[].period_new_spend`/`members[].remaining_due` 各自等於
  這張卡自己的數字（主卡 80/1049，子卡 50/50，加總對得上整組的
  130/1099）。`python -m pytest tests/ -q` 全量跑過，**只有 2 個跟本次改動
  無關的既有失敗**（`test_import_simple.py::test_accounts_parent_before_child_required`、
  `test_recurring_rules.py::test_recurring_occurrence_update_overridden_skipped_by_update_from`
  ——已用 `git stash` 確認在改動前的乾淨樹上就會失敗，不是這次引入的）。
- **前端**：`pnpm -C apps/web build`（tsc + vite build）與
  `pnpm -C apps/web test:unit`（73 例，含驗證三語系 key 一致的
  `i18n.test.ts`）都過。

## 瀏覽器手測（本輪已由我實際操作驗證，非僅憑自動化測試）

用測試帳本既有資料：主帳戶群組「國泰」底下掛兩張真實子卡「cube」（有欠款
1,716.3）跟「蝦皮聯名卡」（欠款 0）。

- [x] **子卡自己的詳情頁只顯示自己的數字**：分別打開 cube / 蝦皮聯名卡
      的詳情頁，頂部「新增花費/本卡應繳」兩格數字彼此不同（cube:
      1,736.3 / 1,716.3；蝦皮聯名卡：0 / 0），不再兩張卡都顯示相同的整組
      合併數字。
- [x] **子卡詳情頁不顯示兄弟卡明細**：兩張子卡的詳情頁都沒有出現「子卡
      明細」清單。
- [x] **展開合併帳單有提示**：子卡詳情頁點開「信用卡帳單」區塊，看到
      「以下為主帳戶合併帳單金額，非本卡自己的數字」提示 + 完整的整組
      合併數字網格。
- [x] **所屬主帳戶連結維持**：子卡詳情頁「所屬主帳戶：國泰」連結仍在。
- [x] **主帳戶自己的詳情頁維持原樣**：打開「國泰」自己的詳情頁，頂部
      6 格合併帳單網格照舊；展開「信用卡帳單」後「子卡明細」清單正常
      列出 cube(1,736.3) / 蝦皮聯名卡(0)。
- [x] **紅利回饋收合成一顆按鈕**：「國泰」詳情頁的紅利回饋區塊變成單行
      「紅利回饋（2 張卡）」+「本期回饋合計：53.7」，點擊後彈出 Dialog，
      裡面依序是 cube / 蝦皮聯名卡 各自的 `SingleCardCardRewards` 區塊
      （原本會直接在頁面上疊兩個區塊，現在收進 Dialog）。

### 這輪沒有覆蓋到、建議你自己再點一次的情境

- [ ] **帳單週期翻頁（`< 上一期 / 下一期 >`）時子卡自己的數字是否正確
      跟著換頁**：目前只驗證了「目前這期」，`period_new_spend` 理論上會
      隨 `cycleOffset` 變動，但沒有實際切換週期肉眼確認過。
- [ ] **從交易詳情「使用回饋」chip 跳轉**：點某張子卡消費的「使用回饋」
      chip，應該自動打開主帳戶詳情頁的紅利回饋摘要 Dialog，並且直接展開
      命中的那條規則明細——這個自動展開路徑改到 `GroupCardRewardsSummary`
      裡重新實作，建議實際操作一次確認命中不同子卡都能正確跳轉。
- [ ] **獨立信用卡（沒有掛群組）的詳情頁**：這輪測試資料剛好都是「掛靠
      群組的子卡」情境，`isBillingRoot` 但非群組的獨立卡（`is_billing_root`
      = 自己單獨一張卡）理論上不受這次改動影響（`isBillingChild` 恆為
      false），但建議挑一張獨立卡點開順手確認畫面沒有變化。
- [ ] **只有 1 張子卡的群組**：`GroupCardRewardsSummary` 目前只在
      `groupChildren.length > 0` 時渲染（沿用原本的判斷），沒有特別處理
      「剛好只有 1 張子卡」要不要跳過收合直接展開——這次測試資料是 2 張卡,
      沒驗證過剛好 1 張子卡時的畫面觀感是否太過「多此一舉點一下」。

## 環境備註（跟本次程式改動無關，僅記錄給下次遇到類似狀況參考）

這次驗證過程中撞到 CLAUDE.md 已知的「本地 API server 沒帶 `--reload`」
陷阱的變體——本地 `:8080` 有一個啟動很久、`--reload` 掛著但確認殼程序早已
不存在的殭屍監聽 socket（`Get-Process`/`tasklist` 都查不到對應 PID，但
`netstat`/連線都還打得通、回應的是舊版 schema），一般的 `Stop-Process`
清不掉，需要連父行程（reloader 的父 PID）一起用 `taskkill /T` 整棵行程樹
砍掉才真正釋放 port 8080。如果下次又遇到「明明程式碼改了、`--reload` 也
掛著，但 API 回應死活不變」，先懷疑是不是有這種殭屍 listener，不要只懷疑
程式碼邏輯錯誤。
