# 授權金鑰 + App 最低可同步版本

> 2026-09-25 新增。App 端對應變更記錄：`BeeCount-main/docs/changes/2026-09-25-license-key-and-min-sync-version.md`。

## 1. 需求與設計決策

| 項目 | 決定 | 原因 |
|---|---|---|
| 營運模式 | 只有一台 server（本人架設），使用者只拿得到 Web 與 App | 金鑰存在 server DB；能碰到 DB/原始碼的人本來就能繞過，程式層面只能保證「一般使用者」繞不過 |
| 誰需要金鑰 | 所有非管理員帳號；**管理員免金鑰** | 金鑰由管理員在後台產生，管理員被擋住就沒人能產生新金鑰 |
| 綁定 | 綁帳號，一把金鑰只能啟用一次 | Web 與該帳號所有裝置共用同一份授權 |
| 效期 | 預設 365 天（產生時可改），**從啟用當下起算** | 已有有效授權時再輸入新金鑰，新天數接在目前到期日之後，不吃掉剩餘天數 |
| 過期後 | 必須換一把新的金鑰 | 同一把金鑰無法重複啟用 |
| App 離線 | App 每次跟 server 確認有效後，本地可離線使用 7 天（不超過金鑰到期日） | 7 天內至少連網一次，否則 App 整個擋住（App 端實作） |

## 2. 強制點（防繞過）

授權檢查**集中在鑑權入口**，不散落在各 router：

| 入口 | 檔案 | 沒授權時 |
|---|---|---|
| 所有一般 REST 端點 | `src/deps.py::get_current_user` → `services/license.py::enforce_request_gates` | HTTP 402 `LICENSE_REQUIRED` |
| PAT（MCP 用） | `src/deps.py::_resolve_pat`、`src/mcp/auth.py::_resolve_pat_sync` | 402 |
| WebSocket `/ws` | `src/routers/ws.py` | close code 4402 |

- 檢查在**每個請求**做，不是發 token 時做 —— 功能上線前發出去的 token、撤銷金鑰、金鑰到期都立即生效（不快取）。
- 管理員判斷讀 DB 的 `users.is_admin`，不讀 token 內容。
- 豁免路徑（`services/license.py::is_license_exempt_path`）：`/auth/*`（登入/登出/2FA）、`/license/*`（查狀態、輸入金鑰）、`/devices/{id}/report-version`。
- **`tests/test_license_route_audit.py`** 掃過 app 上每一條路由：需要登入的路由必須經過 `get_current_user` 或 `get_mcp_scopes`，其餘只能是白名單裡的公開端點。之後新增端點若只掛 `require_scopes`、忘了掛 `get_current_user`，這支測試會直接失敗。新增公開端點時要有意識地加進 `PUBLIC_ROUTES`，並確認不會吐出使用者資料。
- `LICENSE_ENFORCEMENT=false` 只在 `APP_ENV` 為 development/test 時有效（給既有測試與本地開發用，`tests/conftest.py` 預設關閉）；其它環境一律忽略，避免部署時誤設 env 就整台免授權。
- `/license/activate` 每帳號 15 分鐘最多 10 次；金鑰 20 碼 × 32 字元 = 100 bits，暴力猜測不可行。
- 啟用用條件式 UPDATE（`WHERE redeemed_by_user_id IS NULL AND revoked_at IS NULL` + 檢查 rowcount）原子搶佔，兩個帳號同時送同一把金鑰只會有一個成功。

## 3. 最低可同步版本

- 設定位置：Web 後台「App 版本更新提醒」頁的「最低可同步版本」欄位（`app_version_check_config.min_sync_version`，空白 = 不限制，格式錯誤會被 PUT 拒絕）。
- App 從這版開始在每個 HTTP 請求帶 `X-App-Version`（WebSocket 用 `?app_version=`）。JWT `client_type == "app"` 的請求若版本低於門檻、**或根本沒帶版本（舊版 App）**，回 HTTP 426 `APP_VERSION_TOO_OLD`（WS 為 4426）。Web client 不受影響。
- 版本檢查先於授權檢查：舊版 App 先看到「版本過舊」。豁免：`/auth/*`、`/devices/{id}/report-version`（讓後台看得到誰還在用舊版）。
- 公開 `GET /app-version/latest` 多回傳 `min_sync_version`，新版 App 啟動時比對，低於就整個擋住顯示強制更新頁。
- 舊版 App 的實際表現：同步全部失敗（「我的」頁顯示「狀態取得失敗」、帳本卡片紅色雲朵、BeeCount Cloud 同步頁健康檢查顯示 server 的中文訊息「請將 App 更新到 X 以上版本才能同步」）。舊版 App 沒有授權門，本機功能仍可使用 —— 這是舊版程式無法被遠端改變的限制。

## 4. API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/v1/license/status` | 目前帳號授權狀態 `{licensed, exempt, expires_at, server_time, offline_grace_days}` |
| POST | `/api/v1/license/activate` | `{key}`；錯誤碼 `LICENSE_KEY_INVALID`(400) / `LICENSE_KEY_NOT_FOUND`(404) / `LICENSE_KEY_ALREADY_REDEEMED`(409) / `LICENSE_KEY_REVOKED`(410) / `RATE_LIMITED`(429) |
| GET | `/api/v1/admin/licenses?status=&q=` | 管理員列表（unused/active/expired/revoked） |
| POST | `/api/v1/admin/licenses` | `{count, duration_days, note}` 批次產生 |
| POST | `/api/v1/admin/licenses/{id}/revoke` | 撤銷（立即失效） |
| DELETE | `/api/v1/admin/licenses/{id}` | 只能刪未使用的金鑰；已啟用的請用撤銷 |

Web 入口：管理員「授權金鑰」頁 `/app/admin/licenses`；一般使用者沒有授權時登入後直接看到輸入金鑰頁。

## 5. 上線順序（重要）

1. 部署 server（`alembic upgrade head` 套用 `0056_license_keys`）。**部署當下所有非管理員帳號立刻被擋**，先在後台產生好金鑰發給使用者。
2. 發布新版 App 後，到「App 版本更新提醒」頁把「最低可同步版本」設成這個新版本號 —— 舊版 App 就無法再同步。
