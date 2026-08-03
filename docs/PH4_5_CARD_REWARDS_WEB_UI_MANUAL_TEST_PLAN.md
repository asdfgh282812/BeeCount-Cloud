# Phase 4.5 信用卡紅利回饋(§2.9.5)— 测试报告 + 手动测试清单

- 测试日期:2026-08-05(初版)/ 2026-08-06(改版:手动勾选回饋規則)/
  2026-08-07(§2.9.5.4:活動期間、帳單週期同步、交易明細彈窗、跨卡共用
  上限群組、自動入帳)/ 2026-08-03(§2.9.5.4 補強:手動入帳真正的入口 +
  兩個前端 bug 修復)/ 2026-08-03 第二輪(真正走完整瀏覽器手測 + 真實多
  用戶場景)/ **2026-08-04(本輪,見下方新章節):主帳戶回饋不顯示 + 活動
  期間不顯示 + 回饋排程獨立 5 分鐘 + 回饋入帳連結原始消費 + 自動分類**

## 〇之二、2026-08-04 —— 五項使用者回報問題修復(真實瀏覽器驗證)

使用者實測後回報五個問題,全部修復並用 Safari 自動化在真實瀏覽器(而非
只跑 pytest/build)走過一遍驗證,過程直接用瀏覽器 fetch 打 API + 操作
既有測試帳本資料(`信用卡測試帳本`,`cctest@example.com`,`中信`帳戶群組
底下掛靠子卡`line pay 卡`,已有規則`linepay` 2% 回饋)。

### 1. 主帳戶(account_group)信用卡回饋不顯示 —— 真實 bug,已修復

**根因**:`CardRewardRulesSection.tsx` 舊版只在 `accountType ===
'credit_card'` 才渲染,`account_group` 直接跳過整個區塊——但回饋規則綁定
的是子卡(`line pay 卡`),不是群組本身,使用者開「中信」這個主帳戶詳情
彈窗時完全看不到底下 `line pay 卡` 設定的任何回饋規則。

**修法**:`CardRewardRulesSection` 拆成外層分派 + 內層
`SingleCardCardRewards`(原邏輯原樣抽出,單卡場景零行為差異)——外層依
`accountType`:`credit_card` 直接渲染單卡;`account_group` 查詢底下所有
`account_type === 'credit_card'` 且 `parent_account_id` 指向它的子帳戶,
每張子卡各自渲染一份(標題帶卡片名稱「紅利回饋 · line pay 卡」區分),
沒有任何子卡設過規則才整塊不渲染。

**瀏覽器驗證步驟**:
1. 導覽至 `/app/accounts`,點擊「中信」帳戶群組卡片開詳情彈窗。
2. **修復前**:彈窗只有「信用卡帳單」卡片,完全沒有「紅利回饋」區塊。
3. **修復後**:「信用卡帳單」卡片下方出現「紅利回饋 · line pay 卡」
   折疊區塊,展開後正確顯示 `linepay` 規則(2% 回饋)+ 當期符合條件消費
   金額 + 回饋金額,跟直接打開 `line pay 卡` 自己的詳情彈窗看到的內容
   一致。截圖已確認。

### 2. 活動開始日/結束日規則清單完全不顯示 —— 真實 bug,已修復;時區已核對一致

**根因**:`starts_at`/`ends_at` 只在編輯表單裡看得到(拿來做 `isExpired`
內部判斷),規則清單的折疊卡片行從來沒有渲染這兩個欄位,使用者設定了
活動期間也無從得知(除非重新點開編輯表單)。

**時區核對**:後端 `starts_at`/`ends_at` 回傳的是不帶位移標記的 naive
UTC 字串(跟交易 `happened_at` 同一慣例),直接用 API round-trip 驗證:
PATCH 送 `2026-08-01T00:00:00+00:00` ~ `2026-08-31T00:00:00+00:00`,GET
讀回 `2026-08-01T00:00:00` ~ `2026-08-31T00:00:00`,語意一致,沒有偏移。
順帶發現 `isExpired`(判斷規則是否已過期的內部邏輯)沒有套用既有
`isoToDateInput` 已經修過的「缺位移標記強制當 UTC 解析」瑕疵,直接用
`new Date(iso)` 解析會讓 UTC+8 使用者的規則提早 8 小時被判定過期,一併
用同一個 `forceUtcTimestamp` helper 修掉。

**修法**:新增 `activePeriodText(rule, t)` helper,規則清單行在有
`starts_at`/`ends_at` 任一值時顯示「活動期間:YYYY-MM-DD ~ YYYY-MM-DD」
(只有開始/只有結束時顯示對應的單邊文案),三語系(繁中/簡中/英文)都
補上對應 key。

**瀏覽器驗證步驟**:
1. 用瀏覽器 `fetch` 直接 PATCH `linepay` 規則,設定 `starts_at =
   2026-08-01`、`ends_at = 2026-08-31`(繞過原生 `<input type="date">`
   在自動化環境下的分段輸入限制,對後端寫入路徑而言跟使用者手動填表單
   儲存完全等價)。
2. 重新整理帳戶詳情彈窗,展開「紅利回饋」區塊。
3. **修復前**:規則行只有名稱 + 回饋方式 + 符合條件消費金額,完全看不到
   活動期間。
4. **修復後**:規則行新增一行「活動期間:2026-08-01 ~ 2026-08-31」,跟
   PATCH 送出的日期完全一致(無時區偏移)。截圖已確認。

### 3. 回饋金計算排程獨立成 5 分鐘一次

原本 `card_reward_payout.materialize_due_card_reward_payouts` 掛在
`debt_reminders`/`credit_card_reminders`/`credit_card_autopay` 共用的
15 分鐘 loop 上(`main.py::_run_debt_reminders_once`)。改法:`main.py`
新增獨立的 `_start_card_reward_payout_loop`(啟動時立即跑一次,之後每
5 分鐘一次,`_CARD_REWARD_PAYOUT_INTERVAL_SECONDS = 5 * 60`),原本 15
分鐘 loop 裡的呼叫移除。手動觸發沿用既有
`POST /internal/tasks/materialize-recurring`(回傳體
`card_reward_tx_payouts`/`card_reward_period_payouts` 不變)。用這個
端點手動觸發驗證:呼叫後立即產生了一筆回饋 income 交易(見下方第 4 項
的驗證截圖),行為正確。

### 4. 回饋金入帳備註改為結構化連結回原始交易(取代嵌入 tx ID 純文字)

**改動**:新增 `read_tx_projection.reward_source_tx_sync_id` 欄位(跟
`refund_of_sync_id`/`installment_plan_sync_id` 同一套 5-touch-point 模式:
models.py → snapshot_mutator 不需要,因為只由伺服器端 `emit_tx` 產生 →
sync_applier.py 欄位映射 → projection.py → snapshot_builder.py SELECT →
`write/_shared.py::_projection_row_to_tx_dict` fast path → schemas.py →
read/ledgers.py + read/workspace.py → 前端 TS 型別 →
`TransactionDetailDialog.tsx` 新增「關聯消費」可點擊列,對齊既有
`debt_id`/`refund_of_id` 的 `onJumpToTx` 跳轉寫法)。逐筆結算
(`immediate_after_tx`/`after_posting_date`)產生的回饋交易現在會設定這個
欄位指向原始消費,備註文字同時簡化成 `信用卡回饋入帳：{規則名稱}`(不再
把 `tx_xxxxx` 原始 ID 寫進備註)。`period_end`(整期結算)本來就沒有單一
對應的原始交易,備註維持既有的「期間範圍」文案不變。

**過程中意外發現並一併修復的既有 bug**:`snapshot_builder.py` 的交易
SELECT 語句 + `write/_shared.py::_projection_row_to_tx_dict`(web PATCH
快路徑的 prev_item 建構函式)都漏了 `reward_rule_sync_ids_json`(使用者
手動勾選的回饋規則列表)——這是 §2.9.5(2026-08-06 改版)就存在的既有
缺口,這次順手補上,不然使用者透過 web PATCH 改一筆已勾選回饋規則的
交易的其它欄位(金額/備註)時,勾選會被靜默清空。跟本文件之前記錄過的
`auto_pay_enabled`/`settlement_type` 漏 SELECT 是同一類 bug。

**瀏覽器驗證步驟**:
1. 用瀏覽器 fetch 建立一筆新的支出交易(`line pay 卡`,200 元,勾選
   `linepay` 規則)。
2. 呼叫 `POST /internal/tasks/materialize-recurring`(admin token)立即
   觸發回饋入帳,回傳 `card_reward_tx_payouts: 1`。
3. 重新整理帳戶詳情彈窗,新產生的回饋交易:分類顯示「回饋金」、備註顯示
   「信用卡回饋入帳：linepay」(沒有 tx ID)。
4. 點開這筆交易的詳情彈窗:「分類」列顯示「回饋金」,新增的「關聯消費」
   列顯示可點擊的「查看原始消費」連結。
5. 點擊該連結:詳情彈窗原地切換成那筆 200 元的原始消費交易(備註「瀏覽
   器驗證用測試消費」),雙向勾稽正確。截圖已確認整個流程。

### 5. 回饋金交易自動建立/使用「回饋金」分類

**改動**:新增 `services.card_rewards.ensure_reward_category(db, *,
user_id)`——找不到就用跟 `exchange_rate_overrides.py` 同款「sync push
等价」旁路建一個 user-global 的 `income` 分類,名稱固定「回饋金」,同名
同 kind 只會建一次(之後每次呼叫都直接命中既有規則,多條規則/多次入帳
共用同一個分類)。逐筆結算/整期結算(`card_reward_payout.py::
_emit_reward_tx`)+ 手動入帳(`write/card_reward_rules.py::
manual_card_reward_payout_ep`)三處都套用。

**瀏覽器驗證步驟**:見上一項第 3、4 步驟的截圖——新產生的回饋交易分類
欄位顯示「回饋金」,不再是空白的「-」。另外用 pytest 驗證了同一使用者
名下多次觸發回饋入帳只會建立一次「回饋金」分類(不會重複建立)。

### pytest / build 回歸

- `pytest tests/ -q`:除既有已知、跟本次改動無關的 date-sensitive flaky
  用例(`test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`)
  外全過。新增 15 例(`test_card_reward_payout.py`,含逐筆結算回饋交易
  的分類/連結欄位、分類不重複建立、mobile push 與 web PATCH 兩條路徑各自
  的「不帶欄位時保留既有反查值」merge 契約測試)+ `test_card_rewards.py`
  新增 3 個斷言(手動入帳分類欄位)。
- `pnpm -C apps/web exec tsc -b`:通過,無型別錯誤。
- `pnpm -C apps/web test:unit`:73 例全過(含 i18n 三語系 key 一致性
  檢查)。

## 零、2026-08-03 第二輪 —— 真實瀏覽器手測 + 4 個新 bug 修復

**這輪跟之前所有輪次最大的差異**:之前每一輪都只跑 `pytest` + `pnpm
build`,明确写着"没有走完整浏览器手测"。这一輪用 Safari 自动化实际操作
了完整的六大模組(規則管理 CRUD、記交易勾選、帳單週期與額度計算、交易
明細彈窗與跨卡共用上限、四種 settlement_type 自動入帳、例外情況與權限),
过程中触发了 4 个之前从未被抓到的真实 bug(3 个纯前端,1 个后端+前端都
牵涉的多用户场景 bug),已全部修復並跑過 pytest(630 passed,1 个跟本次
改动无关的既有 date-sensitive flaky 用例)+ `pnpm build`/`pnpm
test:unit`(73 passed)確認無回歸。

### 發現並修復的 4 個 bug

1. **日期時區 off-by-one(`CardRewardRulesSection.tsx` +
   `DebtsPanel.tsx` 共用同一個瑕疵)**:規則的 `starts_at`/`ends_at`(以及
   交易明細彈窗裡每筆交易的 `happened_at`)後端回傳的是不帶時區位移的
   naive datetime 字串(SQLite 讀回來就是沒有 tzinfo,`DateTime(timezone=
   True)` 在 SQLite 底下不保證真的帶位移)。前端 `isoToDateInput` 直接用
   `new Date(iso)` 解析——JS 對「有 T 時間但沒有位移標記」的字串會當成
   **本地時間**解析,UTC+8(台灣/中國)使用者編輯/複製規則時活動結束日會
   「少一天」(例如後端存 `2026-08-02T00:00:00`,UTC+8 環境下編輯表單會
   回填成 `2026-08-01`)。這不是只影響負時區使用者的邊緣情況,是這個 app
   主要目標受眾(繁中/簡中使用者,多半在 UTC+8)幾乎每次編輯已有活動期間
   規則都會踩到。修復:`isoToDateInput` 偵測字串缺位移標記時補上 `Z` 強制
   當 UTC 解析,`DebtsPanel.tsx` 的同款函式(`isoToDateInput`/
   `formatDateOnlyUTC`)一併修掉(欠款到期日展示/編輯理論上也有同一個
   bug,只是這輪沒有專門去重現)。

2. **沒設定帳單日的信用卡,紅利回饋卡片預設看到的是「上一期」而非「這
   一期」,且沒有 UI 能切回來(`AccountDetailDialog.tsx`)**:`periodOffset
   = billingCycleOffset - 1` 這個換算式,原本假設 `CreditCardBillingSection`
   內部「上期已繳清就自動跳到累積中那期」的邏輯一定會跑到——但那段邏輯
   掛在 billing summary fetch 的 `.then()` 裡,若這張卡完全沒設定帳單日
   /繳款日,fetch 必定失敗,auto-advance 永遠不會觸發,`billingCycleOffset`
   卡在初始值 0,換算出 `periodOffset = -1`。更嚴重的是,沒有帳單日的卡
   本來就不會渲染 `CreditCardBillingSection`(所以也沒有週期選擇器 UI),
   使用者完全沒有辦法自己切回正確的期——`calendar_month` 規則(文件明確
   說「不受帳單日缺失影响」)因此永遠算錯期,新增交易後回饋卡片會一直
   顯示 0,即使命中的交易金額/規則都設對了。修復:billing summary 不可用
   時,直接回報 `1`(換算後 `periodOffset=0`,對齊「沒有週期概念的卡就該
   看目前這期」的直覺),而不是原樣回報未變動的 `cycleOffset`。

3. **`period_end` 結算的 dedup key 只用日期字串、且零回饋也永久記
   去重,導致補記/回溯進已結束週期的合格交易永遠結算不到
   (`src/services/card_reward_payout.py::_materialize_period_end`)**:跟
   `immediate_after_tx`(dedup key 是交易自己的 sync_id,新增/回溯的交易
   一定會被當成全新項目重新評估)不同,`period_end` 的 dedup key 是「這期
   結束日」這個共用日期字串。背景 15 分鐘 loop 只要在使用者於這期補記合格
   交易**之前**跑過一次(哪怕算出來是 0 元),就會永久把這期記成「已處理」
   ——之後即使把交易回溯進同一個已結束週期,系統也**再也不會**重新結算,
   使用者收不到這筆回饋,也沒有任何補救路徑。這是本輪測試中透過「先建規則
   →背景 loop 先跑一次(0 元)→才回溯記一筆合格交易進已結束週期」這個順序
   意外重現的,現實情境裡「使用者晚幾天才補記一筆消費,而那筆消費剛好落在
   已經結束的帳單週期」完全有可能發生。修復:`reward_amount <= 0` 時直接
   `return False`,不呼叫 `_record_payout`,讓下次 tick 可以重新算,直到
   這期不再是 `period_offset=-1`(自然過期,跟既有的「長時間離線=錯過一次
   自動化」限制一致,不是無限期重試)。

4. **共享帳本裡,非擁有者(editor)記交易時,完全無法勾選帳本擁有者建立的
   紅利回饋規則,會被誤判成「規則不存在」(`_assert_reward_rules_valid`,
   `src/routers/write/_shared.py`)**:這是這輪測試裡影響最大的一個 bug,
   透過**真的註冊第二個帳號、把它加成這個帳本的 editor 成員、直接用它的
   token 打交易寫入 API** 才抓到——之前所有輪次的測試(含這份文件之前幾輪
   的紀錄)都只用單一使用者(帳本 owner)測試,從未驗證過多使用者共享帳本
   場景。根因:`card_reward_rule` 是 user-global 實體(不掛 `ledger_id`),
   `_assert_reward_rules_valid` 校驗 `reward_rule_ids` 存在性時,錯誤地用
   `current_user.id`(**當下操作的人**)去查規則,而不是用
   `ledger.user_id`(**帳本真正的擁有者**,也就是規則的真正歸屬者)。同一
   個檔案裡,借還款的對應校驗 `_assert_debt_exists` 是正確地用 `ledger_id`
   查(`ReadDebtProjection` 剛好自己帶 `ledger_id` 欄位),寫法不一致才讓
   這個 bug 一直沒被抓到。結果是:editor 選了 owner 建的任何回饋規則存交
   易,一律 400 `card reward rule not found`,測試腳本裡「記交易時勾選已
   有規則不受此限制,只要是一般交易寫權限即可」這條假設實際上是**不成立
   的**——editor 根本選不到任何規則。修復:兩處呼叫都把 `user_id=
   current_user.id` 改成 `user_id=ledger.user_id`,已用真實 editor token
   驗證修復後可以正常勾選/儲存,且刻意帶一個不存在的規則 id 仍然正確擋
   400(邊界沒有跟著鬆動)。

### 六大模組完整結果(全部走過真實瀏覽器操作,除非特別註明)

- **模組一(規則管理 CRUD)**:入口/空狀態/百分比+固定金額新增/編輯/停用
  (含記交易時仍可見已停用規則的勾選 chip)/活動期間過期自動隱藏+切換
  顯示/複製(觸發並修復了上面 bug 1)/刪除(含重新整理後端確認真的刪除)
  —— **全部通過**。
- **模組二(記交易手動勾選)**:帳戶=信用卡才顯示勾選區塊、換現金/一般
  帳戶或换收入/轉帳類型立刻消失、複選兩條規則各自獨立算出並加總、不勾選
  完全不計入、勾選但未達 `min_tx_amount` 門檻回饋為 0(門檻由系統判斷)、
  編輯交易改勾選即時反映到帳戶詳情、全局編輯彈窗(從日曆頁開啟)與主表單
  行為一致 —— **全部通過**。
- **模組三(帳單週期與額度計算)**:`cap_amount` 正確截斷(10% 規則+1000
  元消費截斷成上限 5 元而非 100 元)、帳單週期選擇器切換後紅利回饋卡片
  跟著變(觸發並修復了上面 bug 2)、`billing_cycle` vs `calendar_month`
  各自獨立正確計算、沒設帳單日的卡 `billing_cycle` 規則顯示溫和提示不崩潰
  +`calendar_month` 規則正常運作、掛靠群組(此處用「子卡掛靠主卡」情境,
  主卡本身是 credit_card 不是 account_group,但一樣走 `parent_account_id`
  繼承路徑)的子卡正確借用主卡帳單日 —— **全部通過**。
- **模組四(交易明細彈窗與跨卡共用上限)**:點規則本身開明細彈窗(非鉛筆/
  垃圾桶/複製圖示)、切換帳單週期後明細跟著換期、未勾選的交易不出現、
  跨卡建立共用上限群組(無帳單日測試卡 + 子卡,各設 150 上限)、兩張卡
  分別大額消費使理論回饋合計遠超上限,驗證兩卡實際回饋加總精確等於
  150.00(145.63 + 4.37)、勾選分屬兩個不同既有群組的規則時正確跳出衝突
  提示且不儲存 —— **全部通過**。
- **模組五(四種 settlement_type 自動入帳)**:`manual` 手動入帳按鈕+
  金額預填+目的帳戶含「本卡自己」選項+可重複觸發各自產生新交易(無防
  重複機制,設計如此)、`immediate_after_tx`(天數=0)觸發 `POST
  /internal/tasks/materialize-recurring` 後正確生成收入交易+重跑不重複+
  設定 `cap_amount` 後第二筆交易正確被夾到剩餘額度(20 上限,先 15 後
  夾到 5)、`period_end` 已結束週期正確整批結算+發通知+重跑不重複、
  尚未結束的當期不會提前入帳(觸發並修復了上面 bug 3)—— **全部通過**。
- **模組六(例外情況與權限)**:攔截 `fetch` 模擬網路失敗,交易明細彈窗
  正確顯示中性的「載入失敗,請稍後再試」+「重試」按鈕(不會誤顯示成
  「帳戶尚未設定帳單日」等具體業務錯誤),點重試後正確恢復顯示真實資料;
  權限部分**真的註冊了第二個帳號並加為此帳本的 editor 成員**測試(觸發並
  修復了上面 bug 4)——`+ 新增規則`(POST/PATCH/DELETE)對非 owner 一律
  回 404「Ledger not found」而非字面上的 403(`get_accessible_ledger_by_
  external_id` 帶 `roles` 過濾時,不在集合內故意回 None→404,理由是「不
  洩漏帳本存在性」,這是這個 codebase 既有的、其他 owner-only 端點也共用
  的慣例,不是 bug,但跟測試腳本原本預期的「回傳 403」字面上不同,功能上
  一樣是被擋下);修復後,editor 記交易時勾選 owner 建立的既有規則可以
  正常存(一般交易寫權限即可),刻意帶不存在的規則 id 仍正確擋 400 —— **全
  部通過**(含一項已知的措辭差異:404 而非 403,見上)。

### 這輪沒有走的部分(建議之後有空再補)

- **多語言文案檢查**(繁中/簡中/英文切換後確認所有紅利回饋相關文案都有
  對應翻譯,不出現原始 key 或空字串)、**深色/淺色主題視覺檢查**:這兩項
  純粹是視覺/文案巡查,這輪為了優先抓功能性 bug 沒有安排時間走,交易明細
  彈窗/規則表單的新增文案已經在 `pnpm test:unit` 的 i18n key 一致性檢查
  裡覆蓋過(73 例全過),但沒有人眼確認實際顯示效果。
- **`after_posting_date` 獨立驗證**:程式碼層級確認 `compute_settlement_
  date` 對 `immediate_after_tx`/`after_posting_date` 用同一段邏輯(§2.10
  延後入帳落地前兩者行為必然相同),沒有另外重複跑一次瀏覽器操作,屬於
  合理的程式碼審閱代替重複手測。
- **`+ 新增規則` 在 editor 身份下的瀏覽器可見性**:這輪用 API 直接打
  editor token 驗證後端擋下(見上),沒有另外開一個真的 editor 登入的
  瀏覽器分頁去看「點了按鈕之後 UI 上出現的錯誤 toast 文案」長什麼樣子
  ——前端目前完全沒有依角色隱藏這個按鈕(`CardRewardRulesSection.tsx`
  沒有任何 role 判斷),如果想要更好的使用者體驗(讓 editor 一開始就看
  不到這個按鈕,而不是點了才知道被擋),需要額外补一個前端角色判斷,這
  輪只確認了「後端有正確擋下」,沒有處理「前端要不要主動隱藏」這個產品
  決策。
- 測試過程中為了驗證多用戶場景,直接註冊了一個 `editor-test@example.com`
  測試帳號並加成 `ledger_members` 的 editor(role='editor'),以及把
  `cctest@example.com` 提升為 `is_admin=1` 以便呼叫 admin-only 的
  `POST /internal/tasks/materialize-recurring` 端點——這兩個都是本機
  SQLite dev DB 上的異動,沒有動到任何生產環境,但如果之後不想保留這些
  測試帳號/權限,需要手動清理(`delete from users where email=
  'editor-test@example.com'` 會連動 cascade 刪掉 `ledger_members`;
  `update users set is_admin=0 where email='cctest@example.com'`)。
- 测试范围:[MOZE_FEATURE_GAP_SD.md](./MOZE_FEATURE_GAP_SD.md) §2.9.5 信用卡
  紅利回饋(server + web UI):回饋規則 CRUD、記交易時手動勾選規則(可複選)、
  當期回饋計算(percentage/fixed_amount、min_tx_amount/min_spend_threshold
  門檻、cap_amount/cap_shared_key 上限、billing_cycle/calendar_month 週期)、
  活動期間(過期自動隱藏/複製)、帳單週期同步、交易明細彈窗、跨卡共用上限
  群組挑選、自動入帳(四種 settlement_type)。

**2026-08-07 改版重點**:
- 規則新增活動期間(`starts_at`/`ends_at`)UI,過期規則清單預設隱藏(可切換
  顯示)、記交易勾選 chip 也不會再出現過期規則;新增「複製」規則按鈕。
- 帳戶詳情頁的回饋卡片修正為跟著上面的帳單週期選擇器走(之前一直卡在
  「目前這期」的 bug)。
- 點規則可以打開交易明細彈窗,看命中哪些交易 + 各自回饋金額 + 剩餘額度。
- 「共用上限群組」改成跨卡挑選 UI(從這個使用者名下所有信用卡的所有規則
  裡勾選要加入同一組的),不再是自由輸入文字。
- 新增自動入帳:規則可以設定「回饋入帳時機」(手動/消費後幾天逐筆/入帳後
  幾天逐筆/週期結束後一次結算)+「回饋帳戶」(可以是這張卡自己,也可以是
  別的錢包帳戶),到期後系統自動生成一筆收入交易存進去。

**2026-08-03 補強重點**(使用者實測 §2.9.5.4 後回報四個問題):
- 「手動指定」原本設定完全沒有作用——新增交易明細彈窗頂部「手動入帳」
  按鈕(`settlement_type === 'manual'` 才顯示),金額預設帶入這期
  `capped_reward`、可自行修改,目的帳戶每次臨時選擇。
- 修掉交易明細彈窗把任何 fetch 失敗都誤顯示成「帳戶尚未設定帳單日/繳款日」
  的 bug——現在真正的 fetch 失敗會顯示中性的「載入失敗」+ 重試按鈕,不會
  再冒充成某個具體業務狀態。
- 修掉 `CardRewardRuleFormDialog` 的 `Promise.all` 共用 catch 問題——原本
  任一個 fetch(帳戶清單 / 跨卡規則清單)失敗會連帶清空已經成功的另一個,
  導致回饋帳戶下拉選單看起來「什麼都選不到」。
- 覆核確認「共用上限群組顯示沒有其他規則」是正常行為(使用者名下只有一條
  規則時就是空的),不是 bug。

**2026-08-06 改版重點**:使用者反馈初版「規則按 category/金額自動比對交易」
不符合實際使用情境(容易重複計算、使用者比系統更清楚這筆消費該算哪個回饋
方案)。改版後:
- 交易本身新增 `reward_rule_ids`(可複選),記交易時使用者自己勾選要走
  哪幾條規則,系統不再用 `category_ids` 自動判斷。
- `min_tx_amount`(單筆門檻)/ `min_spend_threshold`(當期累積門檻)這兩個
  「金額」判斷維持由系統計算,使用者只負責選規則、不負責算門檻。
- 回饋規則表單拿掉「限定分類」選擇器(`category_ids` 欄位在 DB/API 仍保留
  但不再參與計算,避免多一次 migration)。
- 舊交易(改版前建立、沒有 `reward_rule_ids`)一律不計入任何規則的回饋,
  不做回填。

---

## 一、已自动化验证的部分(这次会话跑过,全部通过)

### 1. Backend — pytest

```
JWT_SECRET=test-secret pytest tests/test_card_rewards.py -q
JWT_SECRET=test-secret pytest tests/ -q
```

- `tests/test_card_rewards.py`(14 个用例):
  - CRUD + owner-only(3 例):建立/列表/更新/刪除規則;拒絕把規則掛在
    `cash`/`account_group` 類型的帳戶上
  - mobile `/sync/push` merge 契約(2 例):
    - `card_reward_rule` 規則本身 partial update 只帶 `label`,其它欄位缺鍵
      時从既有 projection 行保留(CLAUDE.md 要求的新增 entity 模板)
    - 交易的 `reward_rule_ids`(2026-08-06 新增字段)partial update 只帶
      `note` 時,`rewardRuleIds` 缺鍵要保留既有勾選,不能被靜默清空
  - 計算引擎(7 例,改版後全部基於手動勾選 `reward_rule_ids`,不再測試
    category 自動比對):
    - 未勾選任何規則的交易一律不計入(即使金額/時間都符合)+ min_tx_amount
      單筆門檻 + min_spend_threshold 當期累積門檻(先驗未達標、再加一筆湊夠
      門檻驗證正確算出回饋)
    - fixed_amount 費率(單筆固定回饋,min_tx_amount 當門檻)
    - 一筆交易可複選多條規則,各自獨立算出回饋並加總
    - cap_amount 單規則上限截斷
    - cap_shared_key 兩條規則共享上限,先加總再按比例分攤($200+$100 raw
      共享 $150 上限 → $100/$50)
    - `interval=billing_cycle` 但帳戶沒設 billing_day/payment_due_day →
      `status=no_billing_schedule`,回饋強制 0
    - `interval=billing_cycle` 掛靠群組的子卡借用群組的 billing_day 算
      出正確週期
    - `interval=calendar_month`(不需要 billing_day,驗證 period 邊界是
      自然月)
  - write 校驗(1 例):交易的 `reward_rule_ids` 每個 id 必須是使用者名下、
    掛在該筆交易 `account_id` 上的真實規則 —— 不存在的 id / 掛在別張卡上
    的規則都要被拒絕(400)
  - 讀端點對 `account_group` 目標的拒絕(1 例)
- 全量回归:`pytest tests/ -q` 全套用例里唯一失败的
  `test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`
  是**跟本次改动无关的既有 flaky 用例**(对"现在"日期敏感,CLAUDE.md 已
  记录过)。
- `ruff check`/`mypy`:新增/改动的 `src/services/card_rewards.py`、
  `src/routers/write/_shared.py`(新增 `_assert_reward_rules_valid`)等
  文件遵循既有惯例(星号 import 的 F405 噪音是 `routers/write/*.py` 子模块
  的既有惯例,不是新问题)。

### 1.1 Backend — pytest(2026-08-07 §2.9.5.4 新增)

```
JWT_SECRET=test-secret pytest tests/test_card_rewards.py tests/test_card_reward_payout.py -q
JWT_SECRET=test-secret pytest tests/ -q
```

- `tests/test_card_rewards.py` 新增 12 例:結算欄位(settlement_type/
  settlement_days/reward_account_id)round-trip;寫入校驗(缺 settlement_
  days、缺 reward_account_id、reward_account_id 指向 account_group/不存
  在的帳戶都擋,指向自己這張卡放行);update 的 merge 後狀態一致性校驗
  (只切 settlement_type 不帶 settlement_days 要擋,切回 period_end 時
  settlement_days 要被清掉);mobile push merge 契約;跨卡 `cap_shared_
  key` 共用上限(`fetch_cap_group_rules`);新端點 `GET /ledgers/{id}/
  card-reward-rules`(跨卡列表)+ `GET .../card-reward-rules/{id}/
  transactions`(交易明細,含 `remaining_reward_room`)。
- 新增 `tests/test_card_reward_payout.py`(11 例,直接呼叫
  `services.card_reward_payout.materialize_due_card_reward_payouts`,不
  经过 HTTP background loop):逐筆結算(`immediate_after_tx`)到期入帳 +
  重跑去重;`after_posting_date` 現況鎖定(跟 `immediate_after_tx` 算法
  相同);逐筆結算 + `cap_amount` clamp + 額度用完後續交易零金額也記去重;
  `reward_account_id` 指回卡片自己正確沖抵 `open_cycle_spend`;`period_
  end` 整批結算 + 去重 + 發通知;沒設帳單日的規則跳過不記去重;跨卡共用
  上限群組的 `period_end` 結算;`manual`/`enabled=False` 零副作用;規則
  事後過期不追回已賺回饋;internal task 端點回傳計數。
- 全量回歸同上一輪,唯一失敗的還是同一個既有已知、跟本次改動無關的 flaky
  用例。
- **過程中發現並修復一個既有 bug**:`snapshot_builder.py` 重建帳戶快照
  (web write 引擎每次 mutate 前的基線)時,`cardRewardRules` 的 SELECT
  語句沒有包含新的三個結算欄位,導致連續兩次 PATCH 之間第二次讀到的基線
  快照這三個欄位是空的,合併校驗誤判「reward_account_id 缺失」擋掉合法
  更新——被 pytest 直接抓到(`test_update_card_reward_rule_settlement_
  fields_merged_state_validated`),已修復;跟 CLAUDE.md 之前記錄過的
  `auto_pay_enabled`/`auto_pay_from_account_id` 漏 SELECT 是同一類 bug。

### 2. Alembic 迁移

`0032_tx_reward_rule_ids`:`read_tx_projection` 新增 nullable
`reward_rule_sync_ids_json` 欄位(跟 `tag_sync_ids_json` 同一模式),向下
兼容,不影響既有數據。

`0033_card_reward_settlement`:`read_card_reward_rule_projection` 新增
`settlement_type`(NOT NULL DEFAULT 'manual')/`settlement_days`/
`reward_account_id` 三欄,新表 `card_reward_payouts`(自動入帳去重台帳,
唯一索引 `(user_id, rule_sync_id, dedup_key)`)。向下兼容,既有規則升級
後預設 `settlement_type='manual'`,行為不變(零自動化)。

### 3. Web UI — 构建 + 单元测试

```
pnpm -C apps/web build       # tsc -b && vite build,全绿
pnpm -C apps/web test:unit   # vitest run src,73 例全过(含 i18n key 一致性检查)
```

**这次会话(2026-08-07)同样没有走完整浏览器手测**——只跑了 pytest 全量
回归 + `pnpm build`/`pnpm test:unit`,下面这份清单需要你自己在浏览器里
走一遍才能真正验证,尤其是**跨卡共用上限群组挑选**这块纯前端互动逻辑
(sequential PATCH 编排、群组成员计算)pytest 完全测不到,以及**自动入帐**
的四种 settlement_type 端到端流程(需要手动触发 `POST /internal/tasks/
materialize-recurring` 或等 15 分钟 loop)。

---

## 二、需要你自己在浏览器里验证的清单

前置:确认本机 `make dev-api`(或 `uvicorn server:app --port 8080`)和
`make dev-web` 都在跑,且已经跑过 `alembic upgrade head`(新增列
`reward_rule_sync_ids_json` 存在)。建议先建一张 `account_type=credit_card`
的测试帳戶,帳單日/還款日随便填(比如今天往后数 5 天),方便验证
billing_cycle。

### 規則管理(帳戶詳情彈窗)

- [ ] **入口可见**:打开帳戶详情弹窗(信用卡类型帳戶),「信用卡帳單」卡片
      下面应该新出现一个「紅利回饋」折叠区块(图标是礼物 🎁),初始收合只显示
      标题 + (如果本期已有回饋)右上角回饋合计金额。
- [ ] **空状态**:新信用卡没设任何规则时,展开后应该显示「尚未設定回饋規則」
      + 「+ 新增規則」按钮。
- [ ] **新增规则(百分比)**:点「+ 新增規則」,填「網購 2%」、回饋方式选
      「百分比」、回饋百分比填 `2`,**表单不应该再出现「限定分類」选择器**
      (2026-08-06 改版拿掉了,分類不再影响计算),其它留空,保存后应该出现
      在规则列表里。
- [ ] **新增规则(固定金额)**:再建一条「滿百送15」,回饋方式选「固定金額」、
      填 `15`、單筆最低金額填 `100`,保存成功。
- [ ] **编辑规则**:点某条规则的铅笔图标,修改回饋百分比/取整方式/备注,
      保存后确认列表里数字/预览跟着更新。
- [ ] **停用规则**:编辑时取消勾选「啟用此規則」,保存后规则应该显示
      「已停用」徽章;同时**去記交易頁確認该规则在「勾選回饋規則」下拉里
      仍然出现**(只是带「已停用」标记),因为已经勾选过这条规则的旧交易
      需要还能看到/取消勾选它。
- [ ] **删除规则**:点垃圾桶图标,确认弹出浏览器原生确认框,确认后规则从
      列表消失,再刷新页面确认真的删掉了(不是只是前端本地隐藏)。

### 記交易時手動勾選規則(2026-08-06 改版重點)

- [ ] **入口可见**:新建一笔支出交易,帳戶选刚才那张信用卡,表单应该出现
      「紅利回饋規則」的多选 chip 区块,列出该卡的所有规则(启用+已停用都
      列出)。
- [ ] **非信用卡帳戶不显示**:帳戶换成现金/一般帳戶,或 tx_type 换成
      收入/转账,「紅利回饋規則」区块应该消失。
- [ ] **複选**:同一笔交易同时勾选「網購 2%」+「滿百送15」两条规则(chip
      按钮变成高亮态),保存后打开帳戶详情的紅利回饋区块,两条规则应该
      **各自独立**显示这笔消费算进去的回饋(不是只算一条)。
- [ ] **不勾选 = 不计入**:另建一笔金额/分类都符合规则条件的交易,但**不
      勾选任何规则**,保存后确认这笔交易完全不出现在任何规则的回饋计算里
      (哪怕手动去看 qualifying_spend 也不该含这笔)。
- [ ] **金額門檻仍由系統判斷**:勾选了「滿百送15」(min_tx_amount=100)但
      交易金额只有 50,保存后应该看到这条规则的回饋没有算进这笔交易(门槛
      不满足是系统判断,不是使用者要自己心算)。
- [ ] **编辑交易改勾选**:编辑一笔已保存的交易,取消勾选原本选中的规则、
      改选另一条,保存后确认帳戶详情的回饋计算跟着更新(旧规则不再计入
      这笔,新规则计入)。
- [ ] **全局编辑弹窗一致**:从任意页面(比如日曆/總覽)点交易打开全局编辑
      弹窗(不是 /app/transactions 页面自己的表单),确认「紅利回饋規則」
      多选区块行为跟主表单一致(这是历史上容易漏改的第二份表单维护点)。

### 計算驗證

- [ ] **回饋上限**:新建一条 `本期回饋上限` 设 5 的高倍规则(比如 10%),记
      一笔 1000 的消費并勾选这条规则(理论回饋 100),预览应该显示回饋被
      截断成 `5.00`,不是 `100.00`。
- [ ] **共用上限群组**:建两条都填了相同「共用上限群組」字串(比如
      `网购群组`)且各自都设了 `cap_amount`(比如都填 150)的规则,分别记两笔
      大额消費各自勾选一条规则,让两条规则理论回饋加总远超 150,刷新后确认
      两条规则的回饋加起来正好是 150(不是各自截断到 150,合计 300)。
- [ ] **billing_cycle 与 calendar_month**:分别建一条 `billing_cycle` 和
      一条 `calendar_month` 的规则,记同一笔消費同时勾选两条,确认两条规则
      各自独立计算(calendar_month 用自然月份边界,billing_cycle 用帳戶的
      帳單日边界,两者理论上可能覆盖不同的日期区间导致回饋不同)。
- [ ] **没设帳單日的卡**:如果这张信用卡完全没配置帳單日/還款日,新建一条
      `billing_cycle` 规则,预览应该显示「此帳戶尚未設定帳單日/繳款日」提示,
      不应该报错崩溃;`calendar_month` 规则在同一张卡上应该正常工作(不受
      帳單日缺失影响)。
- [ ] **掛靠群组的子卡**:如果你有「主帳戶(合併帳單)」群组 + 掛靠它的子卡,
      在子卡上建一条 `billing_cycle` 规则,子卡自己没有 `billing_day` 但
      应该借用它掛靠的群组的帳單日,正确算出周期(不应该显示「未設定帳單
      日」)。

### 其它

- [ ] **多语言 + 深浅色主题**:切换繁中/简中/英文确认所有文案(含新增的
      「紅利回饋規則」表单标签)都有对应翻译(不应该看到原始英文 key 或空
      字串);切换深色/浅色主题确认版式正常、色彩对齐既有信用卡卡片风格。
- [ ] **权限**:如果这个帳本是共享帳本,用非 owner 身份(editor/viewer)
      登录后,「+ 新增規則」按钮点击应该被拒绝(403,toast 报错);但**记
      交易时勾选已有规则不受这个限制**(勾选走一般交易写权限,不是
      owner-only),确认这个区分行为正确。

### 活動期間 / 隱藏過期 / 複製(2026-08-07 新增)

- [ ] **設定活動期間**:編輯一條規則,填「活動開始日」「活動結束日」,
      「活動結束日」填成昨天,保存後回到規則清單,這條規則預設應該**消失**
      (不再顯示),但點「顯示已過期規則」後應該重新出現,帶「已過期」灰底
      徽章。
- [ ] **過期規則不出現在記交易勾選 chip**:上面那條已過期的規則,去記交易
      頁確認**不再出現**在「紅利回饋規則」多選 chip 裡;但如果某筆舊交易
      已經勾選過它,編輯那筆舊交易時,這條規則應該**仍然出現**(可以取消
      勾選)。
- [ ] **複製規則**:點某條規則的複製圖示(Copy icon),應該彈出新增規則
      表單,所有欄位(含活動期間日期)都預先帶入來源規則的值,`label` 一樣
      需要使用者確認/修改後才能儲存(不會自動改名),儲存後清單裡應該多
      一條獨立的新規則,原規則不受影響。

### 帳單週期同步(2026-08-07 修復)

- [ ] **回饋卡片跟著週期選擇器走**:信用卡帳單卡片上方有「◀ 2026/06/05 -
      2026/07/05 ▶」這種週期選擇器,點左右箭頭切換到歷史週期,下面的紅利
      回饋卡片數字應該**跟著變化**(不再固定顯示「目前這期」的數字)。
- [ ] **子卡不受影響**:如果是掛靠群組的子卡(沒有自己的週期選擇器 UI),
      紅利回饋卡片應該固定顯示「目前還在累積」那期的數字,行為跟改動前
      一致。

### 交易明細彈窗(2026-08-07 新增)

- [ ] **點規則看明細**:展開紅利回饋卡片,點某條規則(不是鉛筆/垃圾桶/
      複製圖示,是規則本身),應該彈出一個明細彈窗,列出這期命中的交易
      (日期、備註/分類、金額、各自的回饋金額),頂部顯示「本期回饋」跟
      「還可再賺 X」或「已達回饋上限」。
- [ ] **明細跟著週期走**:切換上面的帳單週期選擇器後再點同一條規則,明細
      彈窗裡的交易清單/金額應該反映對應那一期,不是永遠顯示目前這期。
- [ ] **未勾選的交易不出現**:確認明細清單裡只有真的勾選過這條規則的交易,
      沒勾選的(即使金額/日期都符合)不應該出現。

### 跨卡共用上限群組(2026-08-07 改版)

- [ ] **建兩張卡各一條規則**:建立兩張信用卡帳戶,各建一條回饋規則。編輯
      其中一條規則,在「共用上限群組」區塊應該看到另一張卡的規則列在可
      勾選清單裡(格式「卡片名稱 - 規則名稱」),勾選它、兩條規則都填上
      `本期回饋上限`,儲存。
- [ ] **跨卡消費各自命中對應規則**:在兩張卡上各記一筆大額消費並分別勾選
      對應規則,讓兩條規則理論回饋合計超過共用上限,確認**兩張卡各自的
      紅利回饋卡片**顯示的 `capped_reward` 合計等於共用上限(不是各自顯示
      完整的上限,也不是完全不受影響)。
- [ ] **取消勾選離開群組**:編輯其中一條規則,取消勾選另一條規則(離開共用
      群組),儲存後確認兩條規則變回各自獨立的上限(不再互相影響)。
- [ ] **衝突提示**:如果不小心在同一次編輯裡勾選了「分屬兩個不同既有共用
      群組」的規則(需要先手動建立出這種情境),儲存時應該跳出錯誤提示,
      不會靜默把兩個群組錯誤合併。

### 自動入帳(2026-08-07 新增,四種 settlement_type)

- [ ] **手動(manual)**:預設值,規則設定裡不需要填「回饋帳戶」,確認即使
      有符合條件的交易,也**不會**自動產生任何入帳交易(這是純顯示,對照
      組)。
- [ ] **消費後幾天(immediate_after_tx)**:設定這個結算方式,天數填 `0`
      (方便測試立即到期)、回饋帳戶選這張卡自己,記一筆消費並勾選這條
      規則,手動觸發 `POST /internal/tasks/materialize-recurring`(需要
      admin 權限,或等 15 分鐘背景 loop),確認交易列表裡多了一筆
      `tx_type=income`、金額等於這筆消費的回饋金額、備註帶規則名稱的交易,
      且信用卡帳單卡片的應繳金額對應減少。再觸發一次確認**不會重複入帳**。
- [ ] **入帳後幾天(after_posting_date)**:目前行為應該跟上面「消費後幾天」
      完全一樣(已知的誠實限制,§2.10 延後入帳落地前不會有差異)。
- [ ] **週期結束後一次結算(period_end)**:設定這個結算方式,回饋帳戶選
      一個別的錢包帳戶(不是這張卡自己),記幾筆消費並勾選這條規則但落在
      「還沒結束的當期」,先觸發一次確認**還不會入帳**;把交易日期改到已
      結束的週期內(或等實際時間推進到下個帳單日之後),再觸發一次,確認
      整期回饋金額一次性存入指定的錢包帳戶,且通知中心出現一則「信用卡
      回饋入帳」通知。再觸發一次確認不重複入帳、不重複通知。
- [ ] **回饋上限 + 逐筆結算**:設定 `immediate_after_tx` + `cap_amount`,
      記兩筆消費都勾選、都到期,確認第一筆拿到完整回饋、第二筆被夾到剩餘
      額度(甚至可能是 0),額度用完後的交易不會再產生入帳交易,但也不會
      被重複檢查。

### 手動入帳(2026-08-03 新增,manual settlement 的真正入口)

- [ ] **手動入帳按鈕**:規則設定「回饋入帳時機」= 手動指定,記一筆消費並
      勾選這條規則,點進交易明細彈窗,確認頂部出現「手動入帳」按鈕(其它
      三種 settlement_type 不會顯示這個按鈕)。
- [ ] **金額預填 + 目的帳戶**:點開按鈕後確認金額欄位預填了這期的
      `capped_reward`(可自行修改),目的帳戶下拉選單能選到信用卡、戶頭等
      所有非 `account_group` 帳戶(含這張卡自己)。
- [ ] **送出後**:確認交易列表多了一筆對應金額的 `income` 交易,備註帶規則
      名稱;彈窗關閉後回饋卡片/帳單卡片正確反映(選這張卡自己當目的帳戶
      時,應繳金額對應減少)。
- [ ] **重複點擊**:manual 沒有防重複機制,確認可以多次觸發、每次都各自
      產生一筆交易(這是設計上的行為,不是 bug——使用者自己控制)。

### 交易明細彈窗錯誤訊息(2026-08-03 修復)

- [ ] 正常情境下點規則看明細,確認能正確顯示交易清單(不應該再出現跟
      §2.9.5.4 相同的「帳戶尚未設定帳單日/繳款日」誤導訊息,除非帳戶真的
      沒設定)。
- [ ] 如果剛好遇到網路錯誤/伺服器錯誤,確認顯示的是中性的「載入失敗,
      請稍後再試」+「重試」按鈕,而不是誤導成某個具體業務狀態。

---

## 三、已知限制(暂不做,超出这轮范围)

- `calc_basis`(消費日 vs 入帳日归属)UI 暂不暴露选择——§2.10 延後入帳
  (`deferred_posting_at`)还没实作,两个值目前行为完全相同,暴露出来只会
  让使用者困惑,等 §2.10 落地后再补 UI。
- 帳單折抵(回饋金折抵帳單)按 MOZE_FEATURE_GAP_SD.md §2.9 既有决定不做。
- mobile 端仍待排期(server 已经把 `card_reward_rule` + 交易的
  `rewardRuleIds` 塞进 sync payload,mobile 拉到会被忽略,不会崩但看不到
  规则管理 / 勾选 UI)。
- 拆帳交易(§2.4 has_splits=true)的 `reward_rule_ids` 挂在父交易行上(跟
  `debt_id`/`refund_of_id` 同一层级),不会展开到 split 明细各自计算 ——
  如果一笔拆帳交易只有其中一个分类明细该算某条回饋规则,目前只能整笔交易
  一起勾选/不勾选,这是本轮范围外的已知缺口。
- 舊資料(2026-08-06 改版前建立的交易)一律不回填 `reward_rule_ids`,不计
  入任何規則回饋——如果使用者需要补算历史回饋,只能手动重新编辑那些交易
  逐一勾选,这轮没有做批次回填工具。
- (2026-08-07 新增)逐筆結算(`immediate_after_tx`/`after_posting_date`)
  不套用 `min_spend_threshold`(本期累積消費門檻)——逐筆入帳沒辦法等到
  「這期結束」才知道有沒有達標,已跟使用者確認接受這個限制;`period_end`
  類型仍然完整套用這個門檻。
- (2026-08-07 新增)共用上限群組如果同時包含逐筆結算跟區間結束兩種規則
  混用,逐筆結算當下沒辦法即時知道另一條區間結算規則吃了多少共用額度,
  極端情況下總額可能略微超出共用上限(最多超出一筆區間結算的量),v1
  已知限制,沒有在寫入層擋這種組合。
- (2026-08-07 新增)交易明細彈窗不做「逐筆命中上限分界線」標記(Moze 參考
  畫面裡消費列表中間出現的那種分界線)——因為 `apply_caps` 是整期比例分攤
  不是先到先得,沒有明確的「這一筆壓線」,只在頂部顯示整組彙總的剩餘額度。
