# Web 年度回顧:限動式改版 + 首頁提醒卡(對齊 App)

日期:2026-10-09

App 端在 2026-10-08 / 10-09 把年度帳單改成限動式年度回顧、加了「每年都不一樣」的驚喜設計與首頁提醒卡
(App repo `docs/changes/2026-10-08-annual-report-story.md`、`2026-10-09-annual-review-reminder.md`)。
這次把同一套做到 Web。**純前端改動,後端 / API / sync 契約都沒動。**

## 入口

- **首頁(總覽)最上方「{year} 年度回顧出爐了」提醒卡**:新年 1/1 起出現,點整張卡直接打開去年的回顧(跳過選年份)。
- 頭像選單 → 年度報告 / 命令面板(Cmd+K)→ 年度報告:跟以前一樣先選年份。

## 改了什麼

### 首頁提醒卡(`apps/web/src/components/dashboard/AnnualReviewReminderCard.tsx`、`apps/web/src/lib/annualReviewReminder.ts`)

規則跟 App 一樣:

| | 規則 |
| --- | --- |
| 何時出現 | 1/1 起提醒「去年」,**沒有期限** |
| 何時消失 | 打開那一年的回顧(從卡片、或從選單選到那一年)或按 ✕(不再提醒) |
| 今年 | 年中先看今年的**不算**看過(還沒過完,隔年 1 月照樣提醒完整版),見 `markAnnualReviewSeen` |
| 不出現 | 目前帳本去年筆數不到 `MIN_RECORDS_FOR_REPORT`(30)——不然點進去只會看到「資料太少」 |

- 卡片外觀跟回顧封面同一套:封面漸層 + 那一年的生肖(🐍 2025 / 🐴 2026),色相依年度旋轉。
- 看過的年份存在 **這個瀏覽器的 localStorage**(`beecount.annualReview.seenYears`),**不跟帳號同步**,
  跟 App 一樣只存本機:在 App 看過,Web 還會再提醒一次。要同步的話得把它塞進 profile appearance(像 App 的
  `tour_seen`),這次沒做。
- 卡片 → `OPEN_ANNUAL_REPORT_EVENT`(window CustomEvent)→ `AppHeader` 打開 `AnnualReportLauncher` 並帶
  `initialYear`;launcher 載入成功後呼叫 `markAnnualReviewSeen`,卡片收到 `ANNUAL_REVIEW_SEEN_EVENT` 自己收起來。
  跨分頁靠 `storage` 事件。
- 「去年夠不夠出報告」用 `GET /read/workspace/analytics?scope=year&period=<year>` 的 `transaction_count`,
  依(帳本, 年份)在模組層快取,切回首頁不重查;查詢失敗就先不顯示、不快取。

### 限動式互動(`features/annual-report/AnnualReportPage.tsx`、`widgets/ProgressDots.tsx`)

- **點畫面右側 70% 下一章、左側 30% 上一章**;觸控左右滑;鍵盤 ← → / 空白鍵;滾輪;進度條點擊直跳。
  點擊落在按鈕 / 連結上(選項、幣別 chip、分享)交給元素自己,不翻頁。滑動後那一下 click 會被吃掉,不會多翻一頁。
- 頂部改成分段進度條:看過的滿格、目前這章從 0 填滿(不自動翻頁,財務數字需要時間看)。
- 桌機保留左右浮動箭頭;手機(< sm)隱藏,點畫面就好。
- 章節順序調成跟 App 一致的節奏:封面 → 記了幾筆 → 跟去年比 → 錢去哪了 → 每月起伏 → 帳戶 → 時段 → 平日/週末 →
  難忘時刻 → 記帳畫像 → 冷知識 → 標籤 → 股票 → 成就 → **年度稱號(壓軸,含分享 / 再看一遍 / 關閉)**。
  原本的 `PageOutro` 併進年度稱號頁後刪除。

### 視覺(`widgets/storyKit.tsx`,取代 `HoneyBg`)

- 每章一組設計好的深色漸層(`PALETTES`,色碼跟 App `annual_story_kit.dart` 同一份),重點色透過 CSS 變數
  `--story-accent` 往下傳,頁面用 `text-[color:var(--story-accent)]`;原本各頁寫死的蜂蜜橙全部換掉。
  刻意不走主題色 / 深淺色 token:全螢幕沉浸畫面,背景永遠深、字永遠白。
- **背景是獨立一層交叉淡入**:內容用 `AnimatePresence mode="wait"`(先淡出再淡入),背景如果放在內容裡
  會在換章空檔閃黑;現在新背景蓋在舊背景上淡入,`bgSeq` 決定疊放順序。
- 收支色跟隨「外觀設定 → 收支顏色」(`<html data-income-color>`,`incomeTone()`);以前總覽頁寫死綠收紅支。

### 每年都不一樣

- **年度主題**(`data/yearTheme.ts`):生肖 12 年一輪,2026 馬年 = 0°(原始配色),每差一年色相轉 30°。
  封面放生肖標籤 + 右下角大圖騰 + 主題標語;海報、首頁卡片同一套配色。生肖以西元年近似。
- **冷知識章**(`data/funFacts.ts`,`pages/PageFunFacts.tsx`):規則與門檻跟 App `annual_fun_facts.dart`
  一致(最晚一筆、單日最多筆、最常光顧商家、重複最多次的金額、最小一筆、最常記帳的星期幾、換算珍奶),
  `pickFunFacts` 用「年份 × 帳本 id 雜湊」決定性洗牌取 3 則。為此 `TransactionLite` 多帶 `merchant`。
- **先猜再揭曉**(`data/quiz.ts` + `StoryQuiz`):「錢去哪了」「每月起伏」先出題,選完才揭曉,答案頁顯示
  猜對 / 差一點(附正解)。答案存在頁面 state,翻回去不會再問;點空白處 = 略過(直接換章)。
  答案頁是獨立元件(`MonthlyResult`),猜完才掛載,`useInView` 的進場動畫才會觸發。
- **稀有稱號**(`data/persona.ts`):新增傳說級「全勤記帳王」(年度到目前每天都有記,至少 60 天)、
  「超級存錢筒」(儲蓄率 ≥ 50%),既有「自律記帳王」(連續 ≥ 100 天)改為傳說級;投資者 / 儲蓄高手為稀有。
  優先序:全勤 > 自律 > 超級存錢筒 > 其他。壓軸頁顯示「傳說稱號 / 稀有稱號」,稀有以上灑彩帶
  (`StoryConfetti`,種子 = 年份,`prefers-reduced-motion` 時不畫)。
- **隱藏成就**(`data/achievements.ts`):「從頭記到尾」(1/1 與 12/31 都有記)、「午夜場常客」(凌晨 0–5 點
  ≥ 10 筆支出;App 叫「夜貓子」,Web 已經有同名年度稱號所以換名)。只有解鎖才出現,卡片虛線框 +「🔓 隱藏成就」。

### 分享海報(`widgets/PosterDialog.tsx`,重寫)

- 改成跟頁面同一套的長圖:深色底 + 每章一張漸層卡(封面含年度稱號與稀有度、記帳筆數、收支、前三大分類、
  12 個月、冷知識、股票、成就、QR)。預覽可捲動,下載拍整張。
- 長圖**不用任何動畫元件**(`html-to-image` 拍當下 DOM)。

### 一起修掉的舊問題

- **年度邊界用 UTC**:`fetch.ts` 的查詢區間原本是 `YYYY-01-01T00:00:00Z`,台灣 1/1 00:00~08:00 的交易被算進
  前一年、隔年 1/1 凌晨的反而算進今年;改成瀏覽器本地 1/1 00:00。`aggregate.ts` 的「哪一天」、`PageExtremes`
  的日期標籤、`extremesInsight` 原本 `slice(0, 10)` 切 ISO(UTC 日期),一律改 `localDayKey`。
- **每月起伏的長條圖高度永遠是 0**:欄位沒被撐高(父層 `items-end`),百分比高度無從計算;改成 `h-full` + `flex-1`。
- 手機寬度:總覽頁大數字撐破兩欄(`BigNumber` 改成 `min(size px, size×0.13 vw)`)、時段頁 4 欄太擠
  (手機改 2 欄)、成就頁內容太高被進度條蓋住(頁面容器改 `[justify-content:safe_center]` + 可捲動)。
- 繁體分類名(餐飲 / 購物 / 娛樂…)找不到 emoji,一律顯示 💰;補上繁體對照。

## 測試

`apps/web/src/annualReviewStory.test.ts`:生肖與色相、冷知識門檻與決定性抽選、全勤 / 超級存錢筒 / 隱藏成就、
台灣 1/1 凌晨算當年第一天、兩種猜謎的選項與「資料不夠就不出題」、提醒卡年份規則與「年中看今年不算」。

手動驗證(隔離的測試後端 + 一整年假資料):首頁卡片出現 → 點開直接進 2025 蛇年封面 → 兩題猜謎猜錯 / 猜對 →
冷知識、隱藏成就、傳說稱號彩帶 → 海報預覽與 PNG 產生 → 回首頁卡片消失;清掉紀錄後按 ✕ 只收卡片不開報告;
375px 寬逐章檢查沒有橫向溢出。

## 取捨 / 沒做的

- 不自動播放、不做 App 的年份 chip(Web 由 launcher 先選年份)。
- 看過的年份不跨裝置同步(見上)。
- 沒做「有多少 % 的人拿到這個稱號」:沒有跨使用者統計,不編造數字。
