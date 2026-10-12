# 彈性結帳日 + 繳款期限(alembic 0070)

日期:2026-10-12
參考:https://doc.moze.app/prepare/account/settings#flexible-closing-date
對應 App:`docs/changes/2026-10-12-flexible-closing-date.md`(App 倉庫,v71)

## 範圍

信用卡(以及合併帳單主帳戶 `account_group`)的帳單週期不再只靠兩個整數:

| 功能 | 欄位(`user_account_projection`) | wire(camelCase,三端字串契約) |
|---|---|---|
| 結帳日遇非工作日延後/提前 | `billing_holiday_rule`(`postpone`/`advance`)| `billingHolidayRule` |
| 假日地區 | `billing_holiday_region`(TW/CN/HK/JP/KR/US)| `billingHolidayRegion` |
| 繳款期限方式 | `payment_due_mode`(`fixed_day`,NULL 同義 / `days_after_closing`)| `paymentDueMode` |
| 單期結帳日覆寫 | `billing_cycle_overrides_json`(`{"yyyy-MM": "yyyy-MM-dd"}`)| `billingCycleOverrides`(物件)|

- `payment_due_mode = days_after_closing` 時,**既有的 `payment_due_day` 語意變成「結帳日後第 N 天」**
  (N 沿用 1..31 驗證範圍)。任何讀 `payment_due_day` 的地方都要看模式。
- 覆寫的 key 是**該期原定結帳日所在年月**(不是調整後的日期),結帳日被調到隔月也不會換 key。
- 全部 nullable,NULL = 沒有任何調整,升級後行為跟升級前完全一樣。

## 算法(`src/services/billing_schedule.py`,跟 App `lib/utils/billing_schedule.dart` 必須一致)

- 某月實際結帳日:有覆寫用覆寫;否則原定日(夾到月底),設了規則且是非工作日就延後/提前到最近的
  工作日。非工作日 = 週末 + 該地區放假日(節日資料集的 `public` 與補假 `day_off`,不含 `observance`;
  沒有資料的年份只剩週末)。**補班日不處理**(節日資料沒有這類資訊)。
- 本期 = 實際結帳日 >= 今天的最早月份(不能只比「今天幾號 vs 結帳日」,因為結帳日會被移動);
  `credit_card.py` 的函式維持原簽名,`billing_day` 參數可傳整數(舊行為)或 `BillingSchedule`。
- 繳款期限:`fixed_day` = 結帳日之後(嚴格晚於)的第一個該日,數字等於結帳日就落到下個月(2026-10-12 起跟 App 一致,原本是 `>=`);`days_after_closing` =
  實際結帳日 + N 天。MOZE 文件沒有「繳款期限遇假日順延」,不做。
- **行為變更(2026-10-12)**:繳款日 == 結帳日時 Cloud 原本算當天,現在對齊 App 算下個月;已設成同一天的卡,提醒/自動繳款日會因此從結帳當天變成下個月。

## 讀寫路徑

- sync:`sync_applier._USER_MERGE_SPECS["account"]` 缺鍵保留;`projection.upsert_user_account` 空字串 → NULL、
  覆寫經 `normalize_overrides` 過濾壞資料;`snapshot_builder` 有值才帶 key。
- Web/API 寫入(`snapshot_mutator._apply_flexible_billing_fields`):值域校驗(規則/地區/模式白名單、覆寫形狀);
  **結帳日、規則或地區實際改變時重設既有覆寫**(只比值有沒有變——編輯表單整包帶 `billing_day` 但值沒變
  不重設);清空覆寫要寫 `{}`,不能 pop key(diff-emit 缺鍵會被 sync merge 補回舊值)。
- 讀端:`ReadAccountOut`、`ReadAccountBillingSummaryOut` 帶四個欄位;billing-summary 另帶
  `period_cycle_month_key` / `period_default_cycle_end` / `period_cycle_adjusted` / `period_next_cycle_end`
  給「調整本期結帳日」使用。
- 消費者:`credit_card_billing.compute_group_billing` / `compute_cycle_period_billing`、
  `credit_card_reminders`、`credit_card_autopay`、`card_rewards`(帳單週期紅利)、interest-free-suggestion 全部吃
  `BillingSchedule`。

## Web

- 帳戶編輯(`AccountsPanel`):繳款期限方式、「結帳日遇非工作日」開關 + 方向 + 地區。
- 帳戶詳情彈窗:繳款日顯示「結帳日後第 N 天」;倒數天數改用 server 算出的實際日期;帳單區塊新增
  「調整本期結帳日」(`BillingCycleAdjustDialog`,payload 組裝在 web-features `lib/billingAdjust.ts`)。
- **Web 版不處理「一併移動延後入帳記錄」**(App 有);需要的話在對帳頁逐筆調整。

## 部署

先部署 Cloud(跑 `alembic upgrade head` 到 0070)+ Web,再發 App v71。App 先上而 Cloud 還沒升級時,App 推上來的
新欄位 Cloud 不認得,結帳日調整不會跨裝置同步。

## 測試

`tests/test_billing_schedule.py`(含「無調整時逐日對拍舊算法」)、`tests/test_account_flexible_billing.py`
(sync merge 契約/清空/快照/讀端/Web 寫入/billing-summary)、`tests/test_credit_card_reminders.py`
(`days_after_closing`)、Web `apps/web/src/billingAdjust.test.ts`。
