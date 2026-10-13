# 2026-10-13 Web 面板改名帳好嗎 + 4.0 設計 token + 標題圓體

> 分支 `feat/v4-m1-rebrand`(4.0 開發線,不可併 main)。規格在 App repo
> `docs/roadmap/4.0.0/01-rebrand.md` §2.1/§2.4/§3、`02-brand-identity.md` §4、
> `03-ui-motion-redesign.md` §2.2/§5。App 端對應實作:`lib/styles/tokens.dart`、`lib/theme.dart`、
> `lib/pages/settings/personalize_page.dart`、App 的 `docs/changes/2026-10-13-v4-brand-assets.md`。
> 這份只講 Web(`frontend/`);後端改名是另一份改動。

## 使用者在哪裡看得到

- **登入頁**:新 logo、「帳好嗎 Cloud 控制台」、標題用圓體、表單卡片下方的來源聲明。
- **授權閘門頁**(需要授權金鑰時):同上的 logo 與標題。
- **頁首**:左上 logo + 「帳好嗎 / TallyCheck」(圓體)、滑過版本號顯示「帳好嗎 Cloud vX」。
- **頭像選單 → 關於**:標題「關於帳好嗎」、倉庫連結改指本 fork、最下方來源聲明 + 「查看授權條款」。
- **設定 → 個人資料/外觀 → 主題色**:色票換成 App 的清單(新芽綠第一,蜂蜜黃 #F8C91C 與 Web 舊預設蜂蜜金 #F59E0B 都還在)。
- **分頁標題、PWA 安裝名稱、favicon、主畫面 icon**:帳好嗎。
- 全站:米白底、墨綠黑字、米灰邊框、暗色帶綠深底;卡片滑過有層次變化;金額變動時淡入。

## 各區改了什麼、為什麼

### 文案(`apps/web/src/i18n/{en,zh-TW,zh-CN}.ts`、`index.html`、`manifest.webmanifest`)
- en 用 TallyCheck / TallyCheck Cloud;zh-TW、zh-CN 都用「帳好嗎 / 帳好嗎 Cloud」(品牌名不做簡繁轉換)。
  改到的 key:`app.brand`、`login.title`、`license.gate.desc`、`shell.appName/docTitle/userDefault`、
  `about.title`、`profile.avatarManagedByApp`、`sharedLedger.share*`、`pwa.install.*`、
  `swipesmart.quickAdd.noteUnboundClause`、`investments.tradeType.openingHint`、`annualReport.entry.title`(🐝 → 🌱)。
- 「蜜蜂使用者」改成「帳好嗎使用者」(預設暱稱,不另外發明農場暱稱)。
- 新增 `about.upstreamNotice`、`about.viewLicense`(三語)。
- `index.html`:title、`apple-mobile-web-app-title`、新增 meta description、theme-color #2E9E66、
  SVG favicon + PNG 後備、apple-touch-icon 改用不透明的新檔。
- `manifest.webmanifest`:name「帳好嗎 TallyCheck」、short_name「帳好嗎」、theme #2E9E66、
  background 米白 #FFF8EC(原本是黑)、lang 改 zh-TW、捷徑名稱改繁中、maskable icon 改用獨立檔案。
- 寫死的字串:`packages/ui` 的 `WEB_APP_NAME`、年度報告封面/長圖的 “TallyCheck Year in Review”、
  長圖 🐝 → 🌱 與「Powered by {shell.appName}」、長圖/復原碼/匯出/匯入範本的**下載檔名後備值**
  `tallycheck-*`、登入時送出的 `device_name: 'TallyCheck Web'`(跟後端改名一致;檔名不是識別碼,
  匯入靠內容判斷)。`AnnualReportEntry` 去 emoji 的 regex 同時認 🌱 / 🐝。

### 來源聲明(`apps/web/src/components/AboutDialog.tsx`、`pages/LoginPage.tsx`)
- 上游授權要求保留版權與作者資訊(01 §1)。文字與 App 關於頁 `aboutUpstreamNotice` 相同,
  連到 `https://github.com/asdfgh282812/BeeCount-main/blob/main/LICENSE`(= App `WebsiteUrls.licenseUrl`)。
- 做成 `UpstreamNotice` 元件,放在「關於」對話框最下方與登入頁表單卡片下方(登入前也看得到)。不要刪。
- 「關於」的三個倉庫連結與 release 來源從 `TNT-Likely/*` 改成本 fork `asdfgh282812/BeeCount-main`、
  `asdfgh282812/BeeCount-Cloud`(repo 名稱沿用 BeeCount-*,改名不改 repo)。

### Logo / icon(`apps/web/public/branding/`)
從 App repo `assets/brand/*.svg` 用 cairosvg 一次性輸出(腳本沒進 repo,步驟見下):
| 檔案 | 內容 |
|---|---|
| `logo.svg` | = `logo-mark.svg`(透明底,亮暗底共用);頁首、登入頁、授權閘門頁、manifest SVG icon、SVG favicon |
| `favicon-216.png` | logo-mark 透明底 |
| `icon-192.png`、`icon-512.png` | `app-icon.svg` 裁圓角(purpose any,捷徑也用它) |
| `icon-maskable-192/512.png`(新) | `app-icon.svg` 滿版不透明;標誌已縮到 78%,主體在中央約 60%,落在 maskable 80% 安全圓內 |
| `apple-touch-icon.png`(新,180) | `app-icon.svg` 不透明(透明底在 iOS 會被填黑) |

檔名都沿用,引用處不用改。`public/icons/account/*` 是帳戶類型圖示,不是品牌圖,沒動。
重產方式:`python3 -m venv venv && venv/bin/pip install cairosvg pillow`,
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` 下用 cairosvg 把上述 SVG 輸出成對應尺寸即可。

### Service Worker(`apps/web/public/sw.js`)
- 靜態資源是 cache 優先、**不會重新驗證**,所以 `CACHE_VERSION` `beecount-web-v2` → `v3`,
  否則舊使用者會一直看到快取裡的蜜蜂 logo。activate 時會刪掉 v2。前綴 `beecount-` 刻意保留。
- 標題字型(約 1.2MB)不放進 PRECACHE,第一次用到時才由 cache 優先邏輯存起來。之後若換字型檔,
  要換檔名或再遞增版本號。

### 設計 token(`apps/web/src/styles.css`、`apps/web/tailwind.config.ts`)
`:root` / `.dark` 的 shadcn 變數整套換成 App `BeePalette` 色碼(HSL,不帶 hsl() 包裝):

| Web 變數 | App 來源 | 亮色 | 暗色 |
|---|---|---|---|
| `--background` | scaffoldBackground | rice #FFF8EC | night #121614 |
| `--foreground` / `*-foreground` | textPrimary | ink #1F2A24 | inkDark #EEF3EF |
| `--card` | surface | paper #FFFFFF | night1 #1B211E |
| `--popover` | surfaceElevated | paper #FFFFFF | night2 #232A26 |
| `--secondary` | surfaceChip | paper3 #EFE8DA | night3 #2A332E |
| `--muted` | surfaceSecondary / surfaceInput | paper2 #F7F2E8 | night2 #232A26 |
| `--muted-foreground` | textSecondary | ink2 #5B6B62 | ink2Dark #9DB0A5 |
| `--border` | line / divider | line #E8E1D3 | lineDark #2A332E(中性,不再帶主色) |
| `--border-strong`(新) | borderStrong | line #E8E1D3 | night4 #35403A |
| `--input` | line / borderStrong | line #E8E1D3 | night4 #35403A |
| `--primary` | primary(`BeeTheme.primaryFor`) | 使用者主色,預設 sprout #2E9E66 | 預設綠換成 #4CC788,自選色不變 |
| `--primary-foreground` | onPrimary | 依主色亮度挑白 / ink | 同左 |
| `--primary-soft`(新) | primarySoft | 預設 #E3F4EA,其他 = 主色 14% 疊白 | 主色 20% 疊 night1 |
| `--primary-strong`(新) | primaryStrong | 預設 #23895A,其他明度 −6% | 明度 +6% |
| `--ring` | — | = primary | = primary |
| `--destructive` | error(珊瑚紅) | coral #E5604D,字白 | coralDark #F07C6A,字 ink |
| `--harvest` / `-strong`(新) | accent / accentStrong(收成金) | #F5B041 / #D98E1E | #F7BE5C / #F5B041 |
| `--info` / `--success` / `--warning`(新) | info / success / warning | #5AA9E6 / #22C55E / #F59E0B | #7DBDF0 / #34D399 / #FBBF24 |
| `--income-rgb` / `--expense-rgb`、`--stock-*` | incomeColor / expenseColor | coral #E5604D / success #22C55E | #F07C6A / #34D399 |
| `--shadow` + `--elevation-1..3`(新) | BeeElevation | 暖棕 #5A4630,6% / 9% / 12% | 不用陰影(transparent) |
| `--radius-xs..xl`(新) | BeeRadius | 6 / 10 / 14 / 20 / 28px | 同 |

- shadcn 的 `accent` 在這個專案是全站 hover 底(`hover:bg-accent/40`,約 100 處),**沿用 3.x 調好的**
  「主色色相 + 80% 90%」淺色方案,沒有改成 App 的 primarySoft(暗色改深色調會讓 hover 幾乎看不見)。
  App 的收成金因此在 tailwind 叫 `harvest`(`bg-harvest`、`text-harvest-strong`)。
- tailwind 圓角:`sm` 10、`md` 14(按鈕/輸入框 = BeeRadius.md)、`lg` 14(shadcn 相容)、
  `xl`/`2xl` 20(`.card`、`.bc-panel` = BeeRadius.lg 卡片)、`3xl` 28。保持單調遞增。
- 新增 tailwind 色:`primary-soft`、`primary-strong`、`harvest*`、`info`、`success`、`warning`、`border-strong`;
  陰影 `shadow-elevation-1..3`。
- `.card` / `.bc-glass` / `.bc-panel` 靜止時用 elevation 1(暗色只有邊框)。

### 主題色(`packages/ui/src/theme/primary-color-script.ts`、`PrimaryColorPicker.tsx`)
- `DEFAULT_PRIMARY_COLOR` = `#2E9E66`,新增 `DEFAULT_PRIMARY_COLOR_DARK` = `#4CC788`、`primaryForMode()`、`onColorHex()`。
- `applyPrimaryColor` 改成一次算好亮、暗兩組變數(`--primary-light/-dark`、`--primary-foreground-*`、
  `--primary-soft-*`、`--primary-strong-*`)寫在 `<html style>`,`styles.css` 的 `:root` / `.dark` 各挑一組。
  所以「預設綠在暗色換亮綠」純靠 CSS,切換亮暗不用重算,ThemeProvider 不用改;節日主題色照舊走同一支。
- 色票 = App `personalize_page.dart` 清單(28 色,順序相同)+ Web 3.x 預設 #F59E0B。選中的勾勾依底色挑白/墨綠黑。
- **localStorage key `beecount.primary-color` 不變**;已存過顏色(含 App 同步下來的)的人維持原色,
  只有從沒存過的人會從蜂蜜金變成新芽綠。
- 測試:`apps/web/src/primaryColor.test.ts`。

### 動態(只做 03 §5 指定的兩件)
- **卡片 hover 層次**:`.card` / `.bc-glass` / `.bc-panel` 滑過時陰影升到 elevation 2、暗色邊框亮一階,200ms。
  刻意不做位移:`transform` 會讓卡片變成新的 stacking context,卡片內非 portal 的下拉會被後面的卡片蓋住。
  只在 `(hover: hover)` 裝置生效;選擇器包在 `:where()` 裡,頁面另外寫的邊框 utility 仍然優先。
  頁首也是 `.card`,加了 `bc-static` 排除。
- **金額淡入**:`packages/web-features/src/components/Amount.tsx`,顯示字串改變後才掛 `bc-amount-fade`
  (280ms opacity),首次渲染不動;已開 `animate` 的 odometer 金額維持原本滾動。
- 兩者都在 `prefers-reduced-motion: reduce` 時關掉。

### 字型
- 內文:body 改成系統字(`system-ui, -apple-system, 'Segoe UI', 'PingFang TC', 'Noto Sans TC', 'Microsoft JhengHei'`),
  拿掉 `'Sora'`(原本就沒載入 webfont,只是 stack 第一順位)。
- 標題:`apps/web/public/fonts/TallyCheckRounded-Regular.woff2`,以 jf open 粉圓 2.1 取子集
  (Big5 常用字 + App/Web 介面用字 + ASCII),OFL 保留字型名所以改名 **TallyCheck Rounded**(family 名與檔名都不能寫回原名)。
  `@font-face` 在 `styles.css`(`font-display: swap`),tailwind `font-display`。授權全文
  `public/fonts/OFL-TallyCheckRounded.txt`,並寫進 repo 根目錄 `THIRD-PARTY-NOTICES.md`。
- 只有 400 一種字重:套用處一律 `font-normal`,另外 `.font-display { font-synthesis: none }`,
  呼叫端就算帶了 `font-semibold` 也不會假粗體。
- 套用範圍(只有標題,金額維持系統字 + tabular-nums):`CardTitle`、`DialogTitle`、`SheetTitle`
  (`packages/ui`,涵蓋大部分頁面/區塊/對話框標題)、頁首品牌字、登入頁品牌/主標題/表單標題、
  授權閘門頁、行事曆月份標題、匯入頁標題、PAT/MCP 設定標題、裝置列表標題。年度報告故事頁的 `font-serif` 大標沒動。

## 刻意保留(改了會壞,見 01 §3)
- localStorage / sessionStorage key `beecount.*`、`beecount:web:*`,自訂事件名 `beecount:*`、`bee:*`。
- Service Worker 快取名前綴 `beecount-web-` / `beecount-share-target-v1`(只遞增版本號)。
- `window.__beecountPendingFiles`、`web+beecount` protocol handler、import 格式代碼 `'beecount'`、
  `@beecount/*` 套件名、`BeeCountCloudVersion` 等型別名、CSS class `bc-*` / `bee-rise-in`。
- 頁首皮膚「蜂巢 / Honeycomb」(App 也保留)、年度報告的 🍯(股利/穩健人格的內容 emoji)。
- 備份還原說明裡的 `beecount.db`、`beecount-cloud` container 名(部署識別碼)。
- 其他頁面寫死的 amber/emerald 指標色(待繳、收支卡片)是語意色,不是品牌色,沒改。

## 已知限制 / 未做
- 簡體中文介面裡,字型子集沒有的簡體字會逐字退回系統字。
- `asdfgh282812/BeeCount-main` 目前對外 404(私有或未公開),LICENSE 連結與「關於」的 App 倉庫連結在公開前點不開;
  `asdfgh282812/BeeCount-Cloud` 目前沒有 release,「關於」的版本比對會顯示「前往 GitHub」。
- 沒有做逐頁視覺改版(依 03 §5 Web 只換 token / logo / 兩種動態)。
