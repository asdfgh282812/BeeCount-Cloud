# 客製化功能使用手冊 SD（Phase 16）

本文件是「幫 BeeCount-Cloud 這些客製化改動補一份使用手冊，並讓 Web 端可以一
鍵導過去」的設計文件（SD）。目的是先把資訊來源盤點清楚、確認技術路徑可行，
**本文件本身不含任何程式改動**，待你回覆 §6 待決問題後再分階段實作（比照
`docs/PH13_PROJECT_SD.md` 的既有慣例：先寫 SD、之後分段做）。

---

## 0. 背景與動機

BeeCount-Cloud 這個服務端上已經加了不少 Moze 對標 / 自訂功能（週期性收支、
分期付款、拆帳退款、欠還款、信用卡帳單試算與自動扣款、卡片紅利回饋規則、
對帳與延後入帳、專案、交易範本……見本 repo `CLAUDE.md` 的功能模組地圖），
但這些功能目前**沒有面向使用者的說明文件**，你想要一個「手冊網站」，並且
希望 BeeCount Web 上有頁面可以一鍵導過去。

圖片（截圖）你不打算存在本地或 git repo 裡，而是要上傳到你自架在 Synology
上的圖床服務 `https://photo.pnsgzomf.synology.me`，所以第一步先實測這個圖
床的 API 能不能通、怎麼通。

---

## 1. 圖床串接驗證結果（已完成，事實紀錄）

服務身分：探測後確認 `https://photo.pnsgzomf.synology.me` 跑的是
**Lsky Pro（蘭空圖床）**，架在你的 Synology 上（從 `lsky_pro_session` cookie
與 `/api/v1/upload` 端點行為判斷），不是 Synology Photos 原生 API。

用你提供的 API Token（Lsky Pro 後台「個人中心 → Token」產生）實測，全部通過：

| 步驟 | 端點 | 結果 |
|---|---|---|
| Token 驗證 | `GET /api/v1/profile` | 200，回傳帳號 `andy91011000@gmail.com`（超級管理員） |
| 上傳圖片 | `POST /api/v1/upload`（multipart `file` 欄位） | 200，回傳 `url`/`markdown`/`html`/`bbcode` 多種現成格式 |
| 公開存取 | `GET` 回傳的 `url` | 200，`content-type: image/png`，**免登入**即可直接存取 |
| 刪除圖片 | `DELETE /api/v1/images/{key}` | 200，刪除後原網址立即變 404（已用測試圖驗證過一輪並清乾淨） |
| 相簿列表 | `GET /api/v1/albums` | 200，可列出（目前是空的） |
| 相簿建立 | `POST /api/v1/albums` | **405，這個版本 API 不支援用 API 建相簿**，只能在網頁後台手動建 |

結論：**「截圖 → 上傳 Lsky Pro → 拿到公開 URL → 直接貼進 Markdown」這條路
徑技術上完全通**，不需要額外的反向代理、CORS 設定或簽名機制——手冊網站本
身只要在 Markdown 裡寫 `![說明](https://photo.pnsgzomf.synology.me/i/...)`
即可，跟本地圖片在渲染上完全等價，只是圖片檔案不進 git repo。

限制：相簿只能手動建，若要用相簿整理截圖，需要先到 Lsky Pro 網頁後台建好
相簿拿到 `album_id`，上傳時帶這個參數；或乾脆不分相簿，靠 Lsky Pro 預設的
「按日期路徑」（`2026/08/10/xxx.png`）加上檔名描述來管理。

---

## 2. 內容落地位置——沿用既有 BeeCount-Website，不另起爐灶

**這是本次規劃過程中最重要的發現**：BeeCount 其實已經有一個**上線中的官方
文件站**，不需要新建。

- Repo：`TNT-Likely/BeeCount-Website`（本機原本沒有 clone，規劃期間已
  `git clone` 一份到 `/Users/andy/BeeCount-Website` 方便盤點，**尚未做任何
  修改**）
- 技術棧：Docusaurus 3，雙語（`zh-Hans` 簡中 / `en` 英文），內建本地全文搜
  尋（`@easyops-cn/docusaurus-search-local`），部署在 Cloudflare Pages
- 網址：`https://count.beejz.com`
- 現有 `docs/` 已有 13 個分類：`account` / `ai` / `category` / `cloud-sync`
  / `features` / `getting-started` / `maintenance` / `mcp` / `personalize`
  / `record` / `security` / `statistics`，加上根目錄的 `intro` / `faq` /
  `changelog` / `contributing` / `shared-ledger`
- **RAG 索引**：這個 repo 的 CI（`.github/workflows/build-rag-index.yml`）
  會在 docs 改動時自動重建向量索引，供 BeeCount-Platform 的 App/Web ⌘K AI
  文件問答使用——代表新增的文件**會自動被 AI 問答功能收錄**，不需要額外整
  合工作
- App 端「使用說明」WebView 本來就是內嵌打開這個站（`docusaurus.config.ts`
  裡有一段 `beeEmbedPlugin`，靠 `?embed=1&theme=dark|light&primary=RRGGBB`
  讓文件站套用 App 當下的主題色），也就是說「App 手冊」跟「Web 手冊」理論
  上可以共用同一個站

現有 `docs/cloud-sync/beecount-cloud.md` 只涵蓋「Docker 部署 / 拿管理員帳
號 / 共享帳本」，**完全沒有涵蓋 Cloud Web 這邊的進階記帳功能**（週期性收
支、分期、拆帳退款、欠還款、信用卡、專案……），這正好對應你說的「這個系
統被我改了很多東西」——這些客製化功能目前確實沒有任何使用者文件。

### 規劃中的新增分類

在既有 sidebar（`sidebars.ts`）新增一組分類，草案命名「**Cloud 進階功
能**」，每個功能一頁：

1. 週期性收支（RecurringRule）
2. 分期付款（InstallmentPlan）
3. 拆帳與退款（Split / Refund）
4. 欠還款（Debt）
5. 信用卡帳單試算與自動扣款（credit_card_billing / autopay）
6. 信用卡紅利回饋規則（CardRewardRule）
7. 對帳與延後入帳（deferred_posting）
8. 專案（Project）
9. 交易範本（TxTemplate）

每頁 frontmatter 沿用既有慣例（`sidebar_position` / `description` /
`keywords`），內容主體用繁體中文（`zh-TW`）以跟現有站內容一致，英文版視
你意願決定要不要同步翻譯（Docusaurus 允許某語系缺頁時 fallback 回預設語
系，可以先只做中文）。

> 這 9 個功能是「自建 Cloud 服務端獨有」，App 端不一定有對應功能（或行為
> 不完全一樣）。現有站內容是「App 官網 + 文件」一體，需要決定要不要在這批
> 新文件裡明確標「僅 Cloud 版可用」，見 §6 待決問題。

---

## 3. 截圖作業流程

寫一個一次性小工具（放進 `BeeCount-Website/scripts/`，跟現有
`scripts/build_docs_index.py` 同一層），本機執行：截圖存本機 → 跑腳本 →
自動 `POST` 到 Lsky Pro `/api/v1/upload` → 直接印出 Markdown 語法貼進文
件，流程一次搞定不用手動組 curl 指令。

- Token 存放：寫進本機 `.env`（不進 git，沿用 repo 既有 `.gitignore`
  pattern），例如 `LSKY_PRO_TOKEN` / `LSKY_PRO_BASE_URL` 兩個環境變數，避
  免 token 出現在 shell history 或指令列參數裡
- 因為相簿建立 API 不支援，不做相簿分類，靠檔名（如
  `recurring-rule-form.png`）+ 上傳時自動的日期路徑做管理即可

---

## 4. BeeCount-Cloud Web 進入點

**現況**：`frontend/apps/web/src/components/AboutDialog.tsx` 的「文檔站」
卡片（`REPO_DOCS = 'BeeCount-Website'`，第 38、194-199 行）目前連的是
`https://github.com/TNT-Likely/BeeCount-Website`（GitHub repo 首頁），**不
是**真正的文件站網址。這是目前唯一一個「文件」相關的入口，位置是頭像下拉
選單 →「關於」彈窗裡的三張 repo 卡片之一。

**規劃改動**：

1. 把該卡片網址改成 `https://count.beejz.com`（真正的文件站首頁，或等新
   分類定稿後直接連到新分類首頁 slug，例如
   `https://count.beejz.com/docs/cloud-advanced/overview`）
2. 可選：比照 App 的 embed 機制帶上 `?embed=1&theme=dark|light&primary=RRGGBB`
   query string（讀取 Web 端當下的主題設定組出來），讓打開文件站時視覺跟
   當前 Web 主題一致；不帶的話就是最單純的外部連結，兩者都能動，差別只在
   體驗細節，可以先做簡單版
3. i18n 文案：`about.repos.docs.desc`（目前描述的是「GitHub 原始碼」語
   氣）要改成「產品說明文件、進階功能教學」這類字眼，`zh-TW.ts` /
   `zh-CN.ts` / `en.ts` 三個語系檔都要同步改

**影響檔案**：`AboutDialog.tsx`、三個 i18n 檔案。不影響 `nav.ts`（頂部導
覽的 `NavItem` 型別目前綁死 `AppSection`，只認內部路由，不支援外部連結；
若之後想把手冊入口提升到頂部導覽而不是塞在「關於」彈窗裡，需要額外鬆綁這
個型別，本次先不做，見 §6）。

---

## 5. 部署與維運影響

- BeeCount-Website 走 Cloudflare Pages 自動部署（push to main 觸發，非本
  repo 內的 GitHub Actions workflow），新增 docs 頁面不需要額外部署設定
- 新文件會被既有 RAG 索引 CI 自動收錄（見 §2），沒有額外整合成本
- BeeCount-Cloud Web 這邊的改動只是換一個外部連結網址 + 文案，走這個 repo
  正常的 build/deploy 流程，沒有新的基礎設施需求
- 兩個 repo 都不需要新增伺服器、網域或反向代理設定

---

## 6. 待決問題（需要你決定後才進入實作）

1. **新分類命名與範圍**：草案「Cloud 進階功能」9 頁（週期性收支/分期/拆
   帳退款/欠還款/信用卡帳單/卡片回饋/對帳延後入帳/專案/範本），你想先做
   哪幾個、還是一次全做？（全做，寫文件而已）
2. **語言範圍**：先只寫繁體中文就好
3. **要不要標註「僅 Cloud 版可用」**：這批功能是自建服務端獨有，要不要在
   每頁明確提示，避免純 App 使用者混淆？（可以）
4. **AboutDialog 連結要不要帶 embed 主題參數**：帶的話體驗更一致但要多寫
   一段組 URL 邏輯；不帶的話最單純，之後隨時可以再加。（可以加）
5. **截圖上傳腳本現在就寫，還是等真的開始寫文件才寫**：這次已經手動驗證
   整條路徑可行，腳本可以現在先做起來，或等你開始截圖時再做也不遲。
6. **新分類要不要在文件站首頁 `DocCardList` 露出**：目前 `intro.md` 用
   `<DocCardList />` 自動列出各分類卡片，新分類預設會自動出現，除非你想
   特別藏起來或放到別的入口。（可以）

---

## 附註：術語對齊（定案）

**修正**：與最初草案相反，§6 定案後，新增的手冊內容（Cloud 進階功能 9 頁）
**一律用繁體中文撰寫**，不跟隨現站既有頁面的簡體中文（`zh-Hans`）風格——這
些新頁面放在同一個 `zh-Hans` locale 目錄下，只是內文文字改用繁體字，不建
新的 i18n locale（不影響既有頁面、不動 `docusaurus.config.ts` 的
`i18n.locales` 設定）。

---

## 7. 執行方式（依 §6 定案後續補）

依使用者指示，本階段改為**由 Claude Code 自動執行**，不再需要人工截圖：

1. **自動理解功能**：直接讀 `src/services/`、`src/routers/write/`、
   `frontend/packages/web-features/src/` 對應模組的原始碼，確保文件內容
   （欄位、限制、行為）跟實際程式邏輯一致，不是憑空杜撰
2. **自動截圖**：起本地 `make dev-api` + `make dev-web`（必要時
   `make seed-demo` 灌示範資料），用瀏覽器自動化工具實際操作到每個功能的
   畫面，截圖後透過 §3 的上傳流程送進 Lsky Pro，拿到公開 URL 直接嵌文件
3. **自動寫文件**：9 篇 Traditional Chinese 文件直接產出到
   `BeeCount-Website/docs/cloud-advanced/`，並更新 `sidebars.ts`
4. **不自動 push**：兩個 repo 的變更完成後**先留在本機工作目錄**，
   `BeeCount-Website` push 到 main 會觸發 Cloudflare Pages 正式環境部署，
   屬於高影響操作，待使用者檢視過內容後再另外確認是否 push
