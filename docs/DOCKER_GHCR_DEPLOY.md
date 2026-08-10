# Docker 部署教學(GHCR 自動建置版)

> 本文件說明「這個 fork」(`asdfgh282812/BeeCount-Cloud`)專屬的部署方式:
> **建置完全交給 GitHub Actions,你只負責 `docker compose pull` + `up -d`**,
> 跟 [`SwipeSmart`](https://github.com/asdfgh282812/SwipeSmart) 的「NAS 直接部署」是同一套模式。
> 通用的環境變數 / 備份 / 疑難排解細節見 [`DEPLOYMENT.md`](./DEPLOYMENT.md),本文件不重複贅述,只補「怎麼從自己的 GitHub 建置的映像跑起來」這段。

---

## 1. 運作流程

```
本機改完 code
   │  git push origin main
   ▼
GitHub Actions(.github/workflows/release.yml)
   │  build + push 映像
   ▼
ghcr.io/asdfgh282812/beecount-cloud:latest
   │  docker compose pull
   ▼
你的伺服器 / NAS(容器啟動,不需要在本機/伺服器上跑任何 build)
```

- **push 到 `main`**:自動 build 並推 `:latest`(+ `:sha-xxxxxxx`),**不跑測試**,求快,幾分鐘內映像就緒。
- **打版本 tag**(如 `1.6.3`,格式 `x.y.z`,不用加 `v` 前綴,跟現有 tag 慣例一致):額外先跑一次後端 `pytest` + 前端 `tsc` 檢查,通過才 build,同時推 `:1.6.3` 和 `:latest`,並自動建立 GitHub Release。想要「正式版」有測試把關時才需要打 tag,平常改完直接 push main 就能拿到新映像。
- 進度可在 GitHub repo 的 **Actions** 分頁看;完成後映像會出現在 repo 的 **Packages** 分頁。

## 2. 一次性前置設定

### 2.1 確認 Actions 已啟用(Fork 預設會被關閉)

GitHub 對「fork 來的 repo」預設會把 Actions 整個關掉,跟 workflow 檔案本身寫得對不對無關 —— 即使 `.github/workflows/*.yml` 都在、也已經 push 過好幾次到 `main`,**Actions 分頁還是會完全沒有任何一次執行記錄**。這是每個新 fork 第一次要手動處理的一次性設定,只有 repo owner(有 admin 權限的帳號)能做:

1. 到 `https://github.com/<你的帳號>/BeeCount-Cloud/actions`,如果最上面有一條提示「Workflows aren't being run on this forked repository」+「I understand my workflows, go ahead and enable them」按鈕 → 直接點下去。
2. 保險起見再去 **Settings → Actions → General → Actions permissions**,確認不是選到「Disable actions」,建議選「Allow all actions and reusable workflows」。
3. 兩個都確認過之後,回到 **Actions** 分頁 → 左側選 `release` → 右側 **Run workflow** 手動觸發一次(不用等下一次 push 才知道有沒有修好),確認能跑完看到綠勾;之後日常 push 到 `main` 就會自動觸發,不用每次手動按。

> 這一步只影響「你自己的 fork」,跟 upstream(`TNT-Likely/BeeCount-Cloud`)的 Actions 狀態完全無關、互不影響。

### 2.2 把 Package 設成 Public(建議)

第一次 push 到 `main` 觸發建置完成後:

1. 到 GitHub → 你的 repo(`asdfgh282812/BeeCount-Cloud`)→ 右側 **Packages** → 點進 `beecount-cloud`
2. **Package settings** → **Change visibility** → 設成 **Public**

設成 Public 之後,伺服器端 `docker compose pull` **不需要登入**就能拉。映像裡不含任何密鑰(密鑰都是 runtime 環境變數),設 Public 是安全的。

> 不想公開的話也可以維持 Private,差別只是伺服器端每次都要先 `docker login ghcr.io`(見下方 3.4)。

### 2.3 確認 workflow 權限

repo → **Settings → Actions → General → Workflow permissions**,確認是 **Read and write permissions**(預設通常已經是,但如果之前手動改過 CI 設定,務必檢查一下,否則 build 完 push 映像那一步會因為沒有 `packages: write` 權限失敗)。

## 3. 伺服器端部署

只需要把下面這一個檔案放到伺服器上,**不需要整個 git repo**。

### 3.1 準備 `docker-compose.yml`

```yaml
services:
  beecount-cloud:
    image: ghcr.io/asdfgh282812/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8869:8080"
    volumes:
      - ./data:/data
    environment:
      # ===== 選填:啟用 ⌘K「AI 文檔問答」=====
      # EMBEDDING_BASE_URL: https://api.siliconflow.cn/v1
      # EMBEDDING_MODEL: BAAI/bge-m3
      # EMBEDDING_API_KEY: ""

      # ===== 選填:自訂初始管理員帳密(不填則首次啟動自動產生隨機密碼)=====
      # BOOTSTRAP_ADMIN_EMAIL: me@example.com
      # BOOTSTRAP_ADMIN_PASSWORD: <強密碼>

      # ===== 選填:SwipeSmart 刷卡建議整合 =====
      # SWIPESMART_BASE_URL: http://<swipesmart-host>:2801

      # ===== 正式環境必填:SSO(OIDC)登入,見 3.2 =====
      # OIDC_AUTHORITY: ""
      # OIDC_CLIENT_ID: ""
      # OIDC_CLIENT_SECRET: ""

      # 更完整的環境變數清單見 .env.example / docs/DEPLOYMENT.md
```

倉庫根目錄已有現成的 [`docker-compose.yml`](../docker-compose.yml),直接把它複製到伺服器上用也可以,image 欄位已經指到這個 fork 自己的 GHCR 映像。

> ⚠️ 上面這份範例**沒有設定 `APP_ENV`**,代表容器會用預設值 `development` 跑 —— 帳密登入(`/auth/login`)是開著的,3.5「啟動」那段看到的「自動產生管理員密碼」就是這個模式。這樣部署起來**不會**要求 SSO,能用是能用,但正式對外服務建議照著 3.2 把 `APP_ENV` 和 OIDC 三個必填變數一起設好。

### 3.2 正式環境:設定 SSO(OIDC)登入(必填)

`src/main.py` 啟動時有一段鐵律檢查:只要 `APP_ENV` 不是 `development`,就會強制要求 `OIDC_AUTHORITY` / `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` 三個欄位都要有值,任一個缺漏就直接 `RuntimeError`,容器啟動失敗退出——因為非 development 環境下帳密登入 `/auth/login`、`/auth/register` 會回 403,SSO 是唯一登入路徑,寧可開機就炸掉,也不要讓運維上線後才發現整個系統沒人能登入。

**必填三項**(在 IdP 後台建立一個 confidential client 拿到):

```yaml
    environment:
      APP_ENV: production
      OIDC_AUTHORITY: https://idp.example.com/realms/beecount   # IdP 的 issuer base URL
      OIDC_CLIENT_ID: beecount-cloud
      OIDC_CLIENT_SECRET: <IdP 給的 client secret>
      # 選填,預設 "openid profile email" 通常夠用
      # OIDC_SCOPE: openid profile email
      # 選填,留空會自動推導成 {你的網域}/api/v1/auth/sso/callback
      # OIDC_REDIRECT_URI: https://beecount.example.com/api/v1/auth/sso/callback
```

`OIDC_AUTHORITY` 是 issuer 的 base URL,server 會自動拼上 `/.well-known/openid-configuration` 做標準 OIDC discovery,常見 IdP 範例:

- Keycloak:`https://idp.example.com/realms/<realm>`
- Authentik:`https://idp.example.com/application/o/<slug>`
- Synology SSO:`https://<nas>/sso/webman/sso`

在 IdP 後台建 client 時,**redirect URI 要設成** `{你的網域}/api/v1/auth/sso/callback`(跟 `OIDC_REDIRECT_URI` 留空時 server 自動推導的值一致;兩邊沒對上,OIDC 登入回調會失敗)。

**誰會拿到 admin 權限?** 容器第一次啟動、資料庫還沒有任何 user 時,會照 3.1 的 `BOOTSTRAP_ADMIN_EMAIL`(沒填則預設 `owner@example.com`)自動建一個 admin 帳號 —— 但在 SSO 模式下密碼登入本身被 403 擋掉,這組密碼實際上用不到。**真正生效的方式是 email 對應**:之後你用同一個 email 透過 IdP 完成 SSO 登入時,後端會按 email 找到這個既有帳號並把它跟 SSO 身分綁定(`sso_subject`),而不是新建一個預設 `is_admin=false` 的新帳號。換句話說 ——

- 想讓自己的 SSO 帳號拿到 admin:把 `BOOTSTRAP_ADMIN_EMAIL` 設成你 SSO 登入會用的那個 email,再啟動容器。
- 如果沒設 `BOOTSTRAP_ADMIN_EMAIL` 就先啟動過,自動產生的 admin 掛在 `owner@example.com`(用不到的隨機密碼),之後任何人用真實 email SSO 登入都只會拿到普通(非 admin)帳號 —— 這時要嘛去 admin 後台手動把該帳號設成 admin,要嘛清空資料庫重新走一次 bootstrap。

### 3.3(選用)換成 PostgreSQL 而非 SQLite

不改 `DATABASE_URL` 的話,預設用 SQLite,單一檔案存在 `./data/beecount.db`(volume 內)。多進程/多副本部署,或想要比 SQLite 更好的並發寫入能力時,可以換成 PostgreSQL —— repo 裡已經有現成的 overlay 檔 [`docker-compose.postgres.yml`](../docker-compose.postgres.yml)(`make dev-db` 本地驗證用的就是它),把它的內容併進伺服器上的 `docker-compose.yml` 即可,不需要另外裝任何東西:

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: beecount
      POSTGRES_USER: beecount
      POSTGRES_PASSWORD: <換成自己的強密碼>
    volumes:
      - ./data/pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U beecount -d beecount"]
      interval: 10s
      timeout: 5s
      retries: 5

  beecount-cloud:
    image: ghcr.io/asdfgh282812/beecount-cloud:latest
    restart: unless-stopped
    ports:
      - "8869:8080"
    volumes:
      - ./data:/data
    environment:
      DATABASE_URL: postgresql+psycopg://beecount:<跟上面 POSTGRES_PASSWORD 一致>@db:5432/beecount
      # ...(其它變數同 3.1 / 3.2)
    depends_on:
      db:
        condition: service_healthy
```

Alembic migration 一樣在容器啟動時自動跑,不需要額外處理。**備份方式不同**:SQLite 用 `scripts/backup_sqlite.sh`(見 [`DEPLOYMENT.md`](./DEPLOYMENT.md)),Postgres 要改用 `pg_dump` / `pg_restore`(可以直接 `docker compose exec db pg_dump -U beecount beecount > backup.sql`);內建的多遠端加密備份(admin UI → Backup)兩種資料庫都支援,不受影響。

### 3.4(僅 Private package 需要)登入 GHCR

```bash
docker login ghcr.io -u asdfgh282812
# Password 貼 GitHub Personal Access Token(classic),勾選 read:packages
```

PAT 建立位置:GitHub → 右上頭像 → Settings → Developer settings → Personal access tokens。設成 Public package(2.2)的話這步可以跳過。

### 3.5 啟動

```bash
docker compose pull
docker compose up -d

# 看首次啟動自動產生的管理員帳密:
docker compose logs beecount-cloud | grep -A 10 "初次启动"
```

看到類似:

```
 BeeCount Cloud — 初次启动,已自动创建管理员账号:

   邮箱:    owner@example.com
   密码:    FIDodUnwprkw1zUi
```

瀏覽器打開 `http://<伺服器 IP>:8869` 用這組帳密登入 Web 管理端;App 端「選擇伺服器」填同一個網址即可。

> 上面這組「自動產生帳密」流程只在 `APP_ENV=development`(預設值,沒設 3.2 的話就是這個模式)才會發生。如果照 3.2 設定了 `APP_ENV` + `OIDC_*`,帳密登入是關閉的,`docker compose logs` 也不會印這段——改成直接開瀏覽器打 `http://<伺服器 IP>:8869`,頁面會導去 IdP 登入頁,登入完成後照 3.2 說明的 email 對應規則決定是否拿到 admin 權限。

## 4. 更新版本

日常改完 code push 到 `main`,GitHub Actions build 完(Actions 分頁確認綠勾)後,回到伺服器:

```bash
docker compose pull
docker compose up -d
```

Alembic migration 會在容器啟動時自動跑,不需要手動介入。想固定用某個正式版本號(而不是永遠追 `:latest`),把 `image` 改成 `ghcr.io/asdfgh282812/beecount-cloud:1.6.3` 這種明確 tag 即可。

可以用 Synology「工作排程器」/ cron 排一個腳本定期跑上面兩行做到自動更新;或手動執行。

## 5. 常用指令

```bash
docker compose logs -f beecount-cloud   # 即時日誌
docker compose restart beecount-cloud   # 重啟
docker compose down                     # 停止(./data 資料還在)
```

## 6. 備份 / 公網部署 / 疑難排解

這些跟映像來源無關,通用內容請看 [`DEPLOYMENT.md`](./DEPLOYMENT.md):

- 備份:`./data/` 目錄打包,或用內建多遠端加密備份(admin UI → Backup)
- 健康檢查端點:`GET /healthz`、`GET /ready`
- 公網:前面套 nginx / caddy / Traefik 做 HTTPS,容器本身只對內提供 HTTP
- Migration 失敗:容器會直接退出、資料庫留在升級前版本,修好問題後 `docker compose pull && up -d` 重試即可

### 常見問題

- **`docker compose pull` 出現 `denied` / `unauthorized`**:package 還沒設成 Public(見 2.2),或忘記 `docker login ghcr.io`(見 3.4)。
- **Actions build 失敗在「Log in to GitHub Container Registry」**:檢查 2.3 的 workflow 權限設定。
- **push 了 main 但伺服器 pull 下來沒變化**:先到 GitHub Actions 分頁確認該次 build 真的跑完成功(綠勾),`docker compose pull` 只會拉已經建置完成的映像,build 還在跑的話 pull 到的還是上一版。
- **push 了好幾次 main,Actions 分頁卻連一次執行記錄都沒有**(不是失敗、是完全沒有 run):十之八九是 2.1 那個 fork 預設關閉 Actions 的一次性設定還沒做。判斷方式:去 `https://github.com/<你的帳號>/BeeCount-Cloud/actions`,如果篩選任何條件都顯示「There are no workflow runs yet.」,就是這個情況,照 2.1 處理。
- **容器啟動就直接退出,log 印 `RuntimeError: OIDC_AUTHORITY / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET must be configured`**:代表 `APP_ENV` 被設成非 `development`(例如 `production`),但 3.2 的 OIDC 三個必填變數沒填全,照 3.2 補上即可;不想現在啟用 SSO 的話,把 `APP_ENV` 拿掉或設回 `development` 也能繞過(但代價是帳密登入會是開著的,見 3.1 的提醒)。
