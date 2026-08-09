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

### 2.1 把 Package 設成 Public(建議)

第一次 push 到 `main` 觸發建置完成後:

1. 到 GitHub → 你的 repo(`asdfgh282812/BeeCount-Cloud`)→ 右側 **Packages** → 點進 `beecount-cloud`
2. **Package settings** → **Change visibility** → 設成 **Public**

設成 Public 之後,伺服器端 `docker compose pull` **不需要登入**就能拉。映像裡不含任何密鑰(密鑰都是 runtime 環境變數),設 Public 是安全的。

> 不想公開的話也可以維持 Private,差別只是伺服器端每次都要先 `docker login ghcr.io`(見下方 4.2)。

### 2.2 確認 workflow 權限

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

      # 更完整的環境變數清單見 .env.example / docs/DEPLOYMENT.md
```

倉庫根目錄已有現成的 [`docker-compose.yml`](../docker-compose.yml),直接把它複製到伺服器上用也可以,image 欄位已經指到這個 fork 自己的 GHCR 映像。

### 3.2(僅 Private package 需要)登入 GHCR

```bash
docker login ghcr.io -u asdfgh282812
# Password 貼 GitHub Personal Access Token(classic),勾選 read:packages
```

PAT 建立位置:GitHub → 右上頭像 → Settings → Developer settings → Personal access tokens。設成 Public package(2.1)的話這步可以跳過。

### 3.3 啟動

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

- **`docker compose pull` 出現 `denied` / `unauthorized`**:package 還沒設成 Public(見 2.1),或忘記 `docker login ghcr.io`(見 3.2)。
- **Actions build 失敗在「Log in to GitHub Container Registry」**:檢查 2.2 的 workflow 權限設定。
- **push 了 main 但伺服器 pull 下來沒變化**:先到 GitHub Actions 分頁確認該次 build 真的跑完成功(綠勾),`docker compose pull` 只會拉已經建置完成的映像,build 還在跑的話 pull 到的還是上一版。
