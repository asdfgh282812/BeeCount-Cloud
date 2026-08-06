# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# BeeCount Cloud —— AI 助手 / 開發者指南

本文件旨在提供 AI 編碼助手 (Claude Code / Copilot 等) 與新進開發者快速定位與開發規範指引，包含**常用指令**、**架構契約**與**避免 Bug 的標準作業流程 (SOP)**。

---

## 常用指令

### 後端 (FastAPI, Python 3.12+)

```bash
make setup-backend        # 建立 .venv + 安裝 requirements.txt + 複製 .env
make migrate              # alembic upgrade head
make dev-api              # uvicorn server:app --reload --host 0.0.0.0 --port 8080
make test                 # pytest -q（或 python -m pytest tests/）
make lint                 # ruff check src tests alembic
make typecheck            # mypy src
```

**單一測試執行：**
```bash
. .venv/bin/activate && pytest tests/test_budget_crud.py -q
. .venv/bin/activate && pytest tests/test_budget_crud.py::test_some_case -q
```

**常用維護腳本：**
```bash
make seed-demo                       # 灌入演示資料
make grant-admin EMAIL=user@x.com    # 將指定使用者提升為 Admin
make wipe-local                      # 清空本地 SQLite + data/ 運行時檔案（保留 docs-index）
python scripts/rebuild_all_projections.py   # 從 sync_changes 事件流重建所有 read_*_projection 表
```

> 本地預設資料庫為專案根目錄的 `beecount.db` (SQLite)；可透過 `make dev-db` 拉起 Docker Postgres 進行多處理器/生產環境儲存路徑驗證。

### 前端 (`frontend/`, pnpm workspace: `apps/web` + `packages/{api-client,ui,web-features}`)

```bash
make dev-web                         # pnpm install + pnpm -C apps/web dev
cd frontend && pnpm -C apps/web build       # tsc -b && vite build
cd frontend && pnpm -C apps/web test        # vitest run
cd frontend && pnpm -C apps/web test:unit   # vitest run src (僅單元測試)
```

---

## 核心架構與資料模型

同步層分為三種儲存形態，**切勿混用**：

1. **`sync_changes` (事件流)**: Append-only 數據表，`change_id` 單調遞增，為增量同步 (pull) 的權威源頭。**永遠只做 INSERT，絕不 UPDATE**。
2. **`read_*_projection` (讀取模型/投影表)**: 讀路徑 (Read APIs) 的唯一權威源。LWW 衝突決勝與 Rename Cascade 均在此落盤。
3. **`ledger_snapshot` (JSON 快照)**: `/sync/full` 採懶加載 (Lazy Build) 模式從 Projection 即時構建。**新程式碼請勿主動寫入 `ledger_snapshot`**。

### 實體作用域 (Scope)
* **Ledger-Scoped**: 包含 Transaction, Category, Budget, Debt, RecurringRule, InstallmentPlan, TxTemplate, DeferredPosting 等（PK 帶有 `ledger_id`）。
* **User-Global**: 包含 UserAccountProjection, UserTagProjection, UserCategoryProjection, CardRewardRule 等（PK 為 `(user_id, sync_id)`，跨帳本共享）。

---

## 開發規範與鐵律 (Hard Rules)

### 1. `main.py` 載入順序鐵律
* `ensure_jwt_secret()` **必須在任何 `from .routers ...` 導入前執行**。
* 原因：部分 Router 模組頂層呼叫了 `@lru_cache` 的 `get_settings()`，若先導入 Router，會導致預設占位 JWT_SECRET 被快取，造成生產環境 JWT 簽驗失敗。

### 2. 新增或修改 Sync Entity 檢查清單 (SOP)
當需要新增/修改同步實體時，必須確保同時更新以下 7 個位置：
1. **DB & Migration**: 新增 `read_*_projection` 資料表與 Alembic Migration。
2. **Projection**: 在 `src/projection.py` 新增 `upsert_*` / `delete_*` / `rename_cascade_*`。
3. **Sync Applier**: 在 `src/sync_applier.py` 的 `_MERGE_SPECS` / `_UPSERT_DISPATCH` / `_DELETE_DISPATCH` 登記。
4. **Write Routers**: 在 `src/routers/write/<entity>.py` 實作 POST / PATCH / DELETE 端點（並加入必要校驗）。
5. **Read Routers**: 在 `src/routers/read/ledgers.py` 或 `workspace.py` 新增讀取端點。
6. **Snapshot Builder**: ⚠️ **極易遺漏** ── 若該實體屬帳本快照的一部分，**必須同步更新 `src/snapshot_builder.py` 中的 SELECT 語句**（包含所有欄位），否則連續 Update 會讀到空基線而校驗失敗。
7. **測試 (pytest)**: 必須補上 `test_mobile_push_<entity>_partial_update_keeps_existing_fields` 風格的 Merge 契約測試，確保 Partial Update 不會將未帶欄位靜默衝成 null。

### 3. 特殊業務校驗規範
* **帳戶群組限制**: `account_type == "account_group"` 為純管理容器。任何交易（Transaction）、週期性收支（RecurringRule）、分期（InstallmentPlan）、範本套用（TxTemplate）寫入路徑，必須呼叫 `_assert_account_not_group` 擋下。
* **前端 UI 驗證要求**: 任何觸及 Web UI 的改動，**必須實際在瀏覽器中手動操作過一遍**，不能僅憑 `pytest` 或 `pnpm build` 通過就宣稱完成。

---

## 功能模組與文件地圖

更詳細的業務架構、歷史背景與測試計畫請參閱 `docs/` 文件：

| 模組 / 主題 | 核心職責與程式碼入口 | 對應說明文件 |
| :--- | :--- | :--- |
| **同步架構 (Sync)** | `routers/sync/`, `sync_applier.py`, `ws.py` (LWW, Change ID, 鎖粒度) | [`docs/SYNC_ARCHITECTURE.md`](./docs/SYNC_ARCHITECTURE.md) |
| **Moze 功能對標 (Phase 0~5)** | 週期性收支, 分期付款, 拆帳, 借還款, 信用卡, 範本 | [`docs/MOZE_FEATURE_GAP_SD.md`](./docs/MOZE_FEATURE_GAP_SD.md) |
| **信用卡與群組模型** | `services/credit_card_billing.py`, `credit_card_autopay.py` | [`docs/PH4_CREDIT_CARD_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH4_CREDIT_CARD_WEB_UI_MANUAL_TEST_PLAN.md) |
| **信用卡紅利回饋** | `routers/write/card_reward_rules.py`, `services/card_rewards.py` | [`docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md) |
| **對帳、延後入帳與報表** | `services/deferred_posting.py`, `read/workspace.py` | [`docs/PH5_RECONCILIATION_WEB_UI_MANUAL_TEST_PLAN.md`](./docs/PH5_RECONCILIATION_WEB_UI_MANUAL_TEST_PLAN.md) |
| **部署與備份** | rclone 多遠端加密備份, docker-compose, Alembic | [`docs/DEPLOYMENT.md`](./docs/DEPLOYMENT.md), [`docs/MIGRATION.md`](./docs/MIGRATION.md) |

---

## 前端與移動端結構

* **Web 前端** (`frontend/apps/web`): Vite + React + TypeScript + Tailwind + shadcn。
* **共用 Package**:
  * `frontend/packages/api-client`: 與 Server 互動的型別化 API Client。
  * `frontend/packages/ui`: 通用 UI 組件。
  * `frontend/packages/web-features`: 跨頁面業務邏輯。
* **Mobile 端 (Flutter)**: 原始碼位於 `../BeeCount/` 專案庫中。同步契約規範請參閱 Mobile 倉內之 `CLAUDE.md`。
