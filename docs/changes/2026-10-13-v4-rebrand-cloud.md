# 2026-10-13 4.0 改名：Cloud 後端改成帳好嗎 Cloud / TallyCheck Cloud

> 分支：`feat/v4-m1-rebrand`（4.0 開發線，不併 main）。
> 規格：App repo `docs/roadmap/4.0.0/01-rebrand.md` §2.4、§2.5、§3（刻意保留的部分）。
> 本文件只涵蓋**後端、Docker、CI、對外文件**；Web 面板（`frontend/`）的改名另外處理。

## 原則

使用者看得到的文字換成「帳好嗎 Cloud」（中文）／「TallyCheck Cloud」（英文）；程式碼識別字、wire 欄位、資料庫與部署名稱一律保留 BeeCount，讓舊 App、舊部署、舊監控都不用改就能繼續運作。簡中文案也用「帳好嗎」，因為品牌名不分簡繁。

## 改了什麼

### 後端顯示名稱
- `src/version.py` 的 `APP_NAME`、`src/config.py` 的 `app_name` 預設值、`.env.example` 都改成 `TallyCheck Cloud`。`/api/v1/version` 回傳的 `name` 會跟著變。App 與 Web 只拿這個值來顯示，沒有拿來比對，所以改了不會出問題。
- `src/main.py`：OpenAPI description 改成 `TallyCheck Cloud (帳好嗎 Cloud) v1 API — based on BeeCount Cloud`。OAuth protected-resource metadata 裡的 `resource_documentation` 改為連到 fork 的 `docs/MCP.md`。
- `src/bootstrap_admin.py`：首次啟動的管理員橫幅改成「TallyCheck Cloud（帳好嗎 Cloud）— 初次启动…」。「初次启动」這幾個字沒動，文件裡的 `grep "初次启动"` 照樣抓得到。
- `src/routers/auth.py`：SSO 登入時，如果前端沒有帶 `device_name`，裝置名稱的預設值從 `BeeCount Web` 改成 `TallyCheck Web`。這個值只用在裝置列表的顯示，沒有任何地方拿它來比對。
- `src/services/securities/providers/base.py`：報價抓取用的 User-Agent 改成 `Mozilla/5.0 (compatible; TallyCheck-Cloud/1.0; +https://github.com/asdfgh282812/BeeCount-Cloud)`。整個 repo 只有這一處設定 UA。Yahoo 等上游只要求請求帶 UA，不檢查 UA 的內容。

### MCP
- `src/mcp/server.py`：`FastMCP("BeeCount Cloud")` 改成 `"TallyCheck Cloud"`。這個名字只出現在 MCP initialize 回應的 `serverInfo.name`，純顯示。客戶端是用使用者在設定檔裡取的 key（文件範例是 `beecount`）來識別 server，tool 名稱也不含這個名字，所以既有的連線設定不受影響。tool docstring 裡寫給 LLM 看的「BeeCount user / BeeCount AI」也一併改了。
- `src/mcp/tools/write_tools.py`：沒有帳本時回傳的訊息改成 `Create one in TallyCheck first.`。

### 兩步驟驗證（TOTP issuer）
- `src/config.py` 的 `totp_issuer_name` 預設值、`src/services/totp.py` 的 fallback，都從 `BeeCount` 改成 `TallyCheck`。`docker-compose.yml` 註解裡的範例值也一起改了。
- **只影響之後新綁定的裝置**：issuer 是在綁定當下寫進 QR code 的。已經加進驗證器 App 的項目還是顯示「BeeCount」，驗證碼照常可用，使用者想統一名稱可以在驗證器裡自己改。如果自架的人想維持舊名稱，設定 `TOTP_ISSUER_NAME=BeeCount` 即可。
- 測試 `tests/test_two_factor_auth.py` 的斷言改成檢查 `TallyCheck`。

### AI 助手 prompt（`src/services/ai/prompts.py`）
- 文件問答（A1）的 system prompt 從「你是 BeeCount(蜜蜂記帳)的助手」改成「你是帳好嗎(TallyCheck)的助手」，英文版同步改。另外各加了一條規則：文件裡出現「蜜蜂記帳／BeeCount」時，指的是帳好嗎的舊名，回答一律稱帳好嗎。加這條的原因是 RAG 索引的內容（手冊站文件）可能還沒全部改名，不能讓 AI 在回答裡講出舊品牌。
- 截圖記帳（B2）與文字記帳（B3）的「你是 BeeCount 记账助手」改成「你是帳好嗎记账助手」，英文版同步改。抽取規則、JSON schema 都沒動。

### 下載檔名
- 交易 CSV 匯出的檔名從 `beecount-<帳本>-<期間>.csv` 改成 `tallycheck-…`（`src/routers/read/workspace.py`）。
- 匯入範本的檔名從 `beecount-<entity>-template.<fmt>` 改成 `tallycheck-…`（`src/services/import_data/templates.py`）。
- 匯入流程判斷格式只看檔案內容（表頭嗅探）和副檔名，不看檔名前綴，所以舊檔名的檔案照樣能匯入。匯入格式代碼 `"beecount"` 沒有改。
- Web 端的 fallback 檔名（`api-client` 的 `beecount-export.csv` 等）在 `frontend/` 裡，由 Web 改名一起處理。

### Docker / RAG 索引（`Dockerfile`、`scripts/fetch_docs_index.py`）
- `DOCS_INDEX_REPO` 預設值從上游 `TNT-Likely/BeeCount-Website` 改成 fork 的 `asdfgh282812/BeeCount-Website`。本地開發用的 `fetch_docs_index.py --remote` 也改了。
- ⚠️ **目前 fork 的手冊站還沒有 `data/docs-index.*.sqlite`，也還沒有 build 索引的腳本與 CI**（2026-10-13 檢查 `origin/main`）。所以 Dockerfile 先 clone fork，`data/` 不存在或是空的就改 clone 上游 `DOCS_INDEX_FALLBACK_REPO`（`TNT-Likely/BeeCount-Website`）。上游文件是蜜蜂記帳品牌，但 AI prompt 已規定回答時一律稱帳好嗎，這樣 ⌘K「問 AI」的文件問答不會因為改名而沒有資料。手冊站 repo 補上索引（`build_docs_index.py`、提交 `data/`）後，會自動改用 fork 的索引，不用再改 Dockerfile。兩邊都拉不到時，`mkdir -p /website/data` 保證 `COPY` 不會讓 build 失敗，只是文件問答降級。本地的 `fetch_docs_index.py` 沒有退回機制，fork 沒索引時要手動加 `--remote` 指向上游。
- image 名稱、容器名稱、volume、`DATABASE_URL` 裡的 `beecount.db` 都沒動。

### CI（`.github/workflows/release.yml`，B6）
- `docker/metadata-action` 的 `images` 從一個改成兩個：原本的 `ghcr.io/<owner>/beecount-cloud`，加上新的 `ghcr.io/<owner>/tallycheck-cloud`。兩者共用同一組 tag（`latest`／semver／sha），build 一次、推到兩個名字，內容完全相同。原有的 tag 一個都沒少。
- 新 package 第一次推上去後，要到 GitHub Packages 頁面設成 Public，`docs/DOCKER_GHCR_DEPLOY.md` §2.2 已補上這一步。用 `GITHUB_TOKEN` 推新 package 時，GitHub 會自動把它連到這個 repo，不需要另外設定 secrets。
- OCI label 的 `title` 改成 `TallyCheck Cloud`，另外新增 `description`。Telegram 發版通知的標題也改成新名字。

### 文件
- `README.md`／`README.en.md`：產品名稱改成帳好嗎 Cloud／TallyCheck Cloud，開頭加了「以 BeeCount Cloud（© sunxiao / TNT-Likely）為基礎修改」的來源聲明，以及一節「4.0 改名須知」，內容包括顯示名稱、TOTP 只影響新綁定、`tallycheck-cloud` image 別名，以及刻意不改的識別碼。MCP 文件連結從上游官網 `count.beejz.com` 改成自己的手冊站 `beecount-manual.pages.dev/docs/more/mcp`。授權段落原本寫「BSL」是錯的，Cloud 的 `LICENSE` 其實是上游作者的自訂授權 v1.0，這次一併改正。商業授權仍然向原作者聯絡。「相關連結」加上 fork 的 repo 與 GHCR image，上游連結保留。Docker Hub 範例（`sunxiao0721/beecount-cloud`）是上游的 image，指令沒改，只在旁邊註明 fork 的 GHCR image。
- `docs/DEPLOYMENT.md`：開頭加上適用範圍說明，安全基準一節新增 `TOTP_ISSUER_NAME` 的說明（只影響新綁定）。
- `docs/DOCKER_GHCR_DEPLOY.md`：補上雙 image 名稱的說明、把 `tallycheck-cloud` 設成 Public 的步驟、新的首次啟動橫幅，以及固定版本 tag 的範例。
- `docs/MCP.md`／`docs/MCP.en.md`：說明文字改成新名字。設定範例的 key `beecount` 刻意不改，並註明它只是使用者自己取的名字，可以改成 `tallycheck`。
- `docs/PERSONAL_API_KEY_POSTMAN_GUIDE.md`：Key 名稱的範例改成「帳好嗎」。
- `CLAUDE.md`：標題和開頭改成新名字，並寫明哪些識別字刻意保留。

## 刻意沒改的部分（改了會壞，或沒有好處）

| 項目 | 位置 | 原因 |
|---|---|---|
| SSO deep link `beecount://auth-callback` | `src/routers/auth.py`、`tests/test_sso_mobile_scope.py` | App 的 URL scheme。改了 SSO 就回不到 App |
| UUIDv5 seed `beecount:stock_dca:*` | `src/services/recurring_materializer.py` | 定期定額的 ID 會改變，產生重複交易 |
| 授權碼前綴 `BC-` | `src/services/license.py` | 已經發出的授權碼會全部失效 |
| PAT 前綴 `bcmcp_` | `src/security.py` | 既有的 MCP token 會失效 |
| Prometheus 指標 `beecount_*`、logger `beecount.*` | `observability.py`、`websocket_manager.py`、`sync/push.py`… | 監控儀表板和告警會斷掉 |
| 預設 DB `beecount.db`、docker volume `beecount_data`、compose service／image `beecount-cloud`、Postgres 帳號／庫名 | `config.py`、`Dockerfile`、compose、scripts | 改了等於部署搬家，資料看起來會不見 |
| 匯入格式代碼 `"beecount"`、`BeeCountParser` | `services/import_data/` | API 參數契約與類別名，使用者看不到 |
| `/version` 回應的欄位名、所有 API／wire 欄位名 | — | 欄位名是契約，只改了 `name` 的值 |
| 程式碼註解裡的「BeeCount 倉」「BeeCount-Website」等 repo 名稱 | 多處 | 它們是實際的 repo 名稱，repo 4.0 不改名（B6） |
| `docs/OBSERVABILITY.md`、`docs/ROLLBACK_SOP.md` | — | 內容全是指標名和檔案路徑，都屬於識別字 |
| 節日預設色 `DEFAULT_COLOR = "#F59E0B"` | `src/services/holidays/catalog.py` | 這是沒有對照到節慶的節日用的「節日色」，不是品牌主色。改了會讓節日資料集的內容變動，App 內建的備援 bundle 也要重新匯出 |
| `LICENSE`、`LICENSE_EN`、`CONTRIBUTING.md` 的授權條款 | — | 上游作者的授權義務，不可修改 |
| 既有的 alembic migration | `alembic/versions/` | 不修改歷史 migration |

## 主題色預設值（任務 4）

後端**沒有**替使用者注入預設主題色：`user_profiles.theme_primary_color` 是 nullable、沒有 server default（`0001_init.py` 也沒有），`routers/profile.py` 沒存值就回傳 `null`，由 App／Web 自己決定預設色。App 4.0 對「從沒選過顏色」的使用者給新芽綠 `#2E9E66`，後端不需要改，也不需要 migration。已經存下的使用者選擇不會被改寫。

## 部署注意事項

1. **部署順序：Cloud → Web → App**。App 上的「帳好嗎 Cloud」字樣要跟伺服器端一致，而且這次 Cloud 的 wire 契約沒有任何變動，舊 App 連新 Cloud、新 App 連舊 Cloud 都能正常運作。
2. **TOTP issuer 只影響新綁定**：已經綁定 2FA 的使用者不受影響，驗證器裡的舊項目照常可用。想統一名稱的使用者可以在驗證器裡自己改名，或解除綁定後重新綁定。
3. **image 別名**：既有部署繼續 `docker compose pull` `ghcr.io/asdfgh282812/beecount-cloud` 即可，不用改。想用新名字的，把 `image:` 換成 `ghcr.io/asdfgh282812/tallycheck-cloud` 即可，內容相同。第一次推送後記得把新 package 設成 Public。
4. **既有 `.env` 寫死了 `APP_NAME=BeeCount Cloud`**（本機開發用的 `.env` 就是這樣）：OpenAPI 標題會繼續顯示舊名，要自己改。`/version` 用的是 `version.APP_NAME`，不受 `.env` 影響。
5. **文件問答（⌘K 問 AI）**：fork 的手冊站還沒有索引，這次之後 build 出來的 image 會缺少 RAG 索引，見上方「Docker / RAG 索引」。
