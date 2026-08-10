# 部署指南 (Deployment Guide)

## 1) 預設配置：SQLite 單一容器 (Default: SQLite single container)

```bash
docker compose up -d --build
```

- **資料卷 (Data volume)：** `beecount_data` 掛載於 `/data`
- **預設資料庫 URL：** `sqlite:////data/beecount.db`
- **備份產物目錄：** `/data/backups` (`BACKUP_STORAGE_DIR`)
- **App 協作讀寫權限範圍：** `ALLOW_APP_RW_SCOPES` 預設為 `true`（僅在明確希望限制 App 讀寫權限時才設為 `false`）

## 2) 健康檢查 (Health checks)

- **存活檢查 (Liveness)：** `GET /healthz`
- **就緒檢查 (Readiness)：** `GET /ready`
- **指標數據 (Metrics)：** `GET /metrics`
- Compose 設定檔已包含容器健康檢查（就緒探針 ready probe）

## 3) 備份 (Backup)

SQLite 備份命令：

```bash
./scripts/backup_sqlite.sh /data/beecount.db ./backups/sqlite
```

此腳本採用 `sqlite3 .backup`（SQLite 線上備份 API），在**伺服器運行期間執行是絕對安全**的，且適用於任何日誌模式（journal mode）。輸出的結果永遠是單一且乾淨的資料庫檔案，不會產生 `-wal` 或 `-shm` 等附屬檔案。

> ⚠️ **請勿直接複製 (cp) 原始資料庫檔案：**
> 伺服器是在 WAL 模式下運行，單純使用 `cp` 指令會遺漏掉仍留在 `beecount.db-wal` 中尚未寫入的資料。請務必使用本腳本（或直接執行 `sqlite3 .backup`）。

若需要完整的資料卷快照（包含資料庫、附件、JWT 金鑰及先前所有的備份），可在停止容器後將 `beecount_data` 資料卷打包為 tar 檔；或是透過應用程式內的備份執行器（管理員 UI → "Backup"）使用 `VACUUM INTO` 功能，該功能已整合 rclone。

### 還原 (Restore)

請參閱 [ROLLBACK_SOP.md](./ROLLBACK_SOP.md) — 請特別注意在 WAL 模式下，**覆蓋資料庫前必須先刪除 `-wal` 與 `-shm` 檔案**的步驟。

## 4) 安全基準 (Security baseline)

- 首次啟動時，系統會自動產生 32 位元組的 `JWT_SECRET` 並存入 `/data/.jwt_secret`；若希望自行管理密鑰，可透過環境變數進行覆寫。
- 請將 API 部署於自建的反向代理伺服器（如 Caddy / Nginx / Traefik / Cloudflare）並啟用 TLS 加密。
- 請確保 `/data` 存放於持久化儲存設備中 — 資料庫、附件、備份檔以及 JWT 密鑰皆存放於此。

## 5) App 權限範圍疑難排解 (App scope troubleshooting)

- **異常症狀：** App 顯示協作角色為未就緒，或是裝置頁面回報 `Insufficient scope`（權限不足）。
- **檢查環境變數：** 請確認 `ALLOW_APP_RW_SCOPES` 未被設定為 `false`。
- **套用變更：** 重啟服務或容器，並於 App 中重新登出再登入，以刷新 Token 與 Session 上下文。
- **裝置 API 預設值：** `GET /api/v1/devices` 目前預設回傳 `view=deduped` 與 `active_within_days=30`。
  - 查看完整 Session：`GET /api/v1/devices?view=sessions&active_within_days=0`
  - 去重後的裝置列表會保留 `session_count` 欄位，以利閱讀。

## 6) 自建託管之成員管理 (Self-host member management)

- Web 協作頁面支援直接透過 Email 管理成員（新增/更新/移除：`add/update/remove`），無需經過邀請碼流程。
- 自建託管的推薦操作流程：請於 Web / 管理員介面中管理共享帳本成員，並將 App 作為協作讀取端。

## 7) 最小化標準作業程序 (Minimal SOP - 自建託管)

- 若 App 角色顯示「權限未就緒」(Permission not ready)，請從 App 帳本協作頁面複製診斷資訊，並確認以下項目：
  - `role_resolve_status`
  - `scope_hint`
  - `deviceId`
- 確認 `ALLOW_APP_RW_SCOPES` 已啟用（設為 `true`），重啟後端，並於 App 中重新登出再登入。
- 若裝置列表項目過多，請先維持預設的去重檢視（deduped view），僅在需要撤銷權限時才切換至完整 Session 檢視。
- 若使用者本地帶有預設帳本 `id=1`，且同時存在遠端共享帳本（例如 `ledger_1.json`），最新版本的 App 會在啟動時自動進行身份調和（auto-reconciles）：
  - 個人帳本會重新映射至帶有命名空間的本地同步 ID（namespaced local sync id）。
  - `sync_queue` 與 `sync_state` 的引用關係會自動進行遷移。
  - 當目標路徑為空時，系統會盡力將舊的快照路徑複製至新路徑。

## 8) 實驗性協作策略 (Experimental collaboration policy)

- 目前的協作功能在自建託管部署中被視為**實驗性功能 (experimental)**。
- 請保持後端 API 的相容性穩定；在 App / UI 持續迭代期間，避免進行破壞性的 API 刪除。
- 建議之面向使用者的策略：
  - App 保留協作入口可見，並附帶 Beta 測試警告。
  - 共享成員的操作維護，仍以 Web / 管理端介面優先。
