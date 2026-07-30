# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# BeeCount Cloud —— AI 助手/新人阅读指南

本文件给 AI 编码助手(Claude Code / Copilot 等)和第一次进本仓的人类开发者
一个快速定位,告诉你**改什么在哪里改**、**绕不过去的契约**、**哪类修改
最容易出 bug**。

## 常用命令

### Backend (FastAPI, Python 3.11+)

```bash
make setup-backend        # 建 .venv + pip install -r requirements.txt + 拷贝 .env
make migrate              # alembic upgrade head
make dev-api              # uvicorn server:app --reload --host 0.0.0.0 --port 8080
make test                 # pytest -q（等价 python -m pytest tests/）
make lint                 # ruff check src tests alembic
make typecheck            # mypy src
```

单个测试文件 / 单个用例:

```bash
. .venv/bin/activate && pytest tests/test_budget_crud.py -q
. .venv/bin/activate && pytest tests/test_budget_crud.py::test_some_case -q
```

其他常用脚本(均需先 `. .venv/bin/activate`,`PYTHONPATH=.`):

```bash
make seed-demo                       # 灌演示数据
make grant-admin EMAIL=user@x.com    # 把某用户提升为 admin
make wipe-local                      # 清空本地 sqlite + data/ 运行时文件（保留 docs-index）
python scripts/rebuild_all_projections.py   # 从 sync_changes 事件流重建 read_*_projection
```

本地默认数据库是仓根的 SQLite 文件 `beecount.db`,可以直接用 `sqlite3` CLI
查看;`make dev-db` 会拉起 docker-compose 里的 Postgres,用于验证多进程/
真实生产存储路径的行为。

### Frontend (`frontend/`,pnpm workspace: `apps/web` + `packages/{api-client,ui,web-features}`)

```bash
make dev-web                         # pnpm install + pnpm -C apps/web dev
cd frontend && pnpm -C apps/web build       # tsc -b && vite build
cd frontend && pnpm -C apps/web test        # vitest run
cd frontend && pnpm -C apps/web test:unit   # vitest run src（只跑单元测试目录）
```

## 改代码之前必读

**如果要改跟 mobile ↔ server 或 web ↔ server 同步相关的任何逻辑**,
先读:

### [docs/SYNC_ARCHITECTURE.md](./docs/SYNC_ARCHITECTURE.md)

里面有:
- 核心路由目录与职责(`routers/sync/` `routers/write/` `routers/read/` +
  `sync_applier.py` + `ws.py`)
- 4 条核心数据流(mobile→web / web→mobile / mobile 首次同步 / web 读)
- **契约部分**(最容易踩坑):
  - user-global vs ledger-scoped 实体的 `ledger_id` 通道区分
  - LWW 冲突决胜规则
  - rename cascade 在 push / write 两条路径上的实现
  - 增量 push 的 merge 字段语义
  - change_id 单调性
  - `lock_ledger_for_materialize` 锁粒度
- debug 清单 + 修改前自检清单

**这块代码历史上出过几次难复现的 bug**(2026-04 修过两次 ledger_id
误用 + budget import path 错误),根因都是"有隐式契约但没在契约点强制"。
动之前花 5 分钟读完 `SYNC_ARCHITECTURE.md` 省几小时 debug。

**如果要对标 Moze 补功能缺口**(週期性收支/分期/拆帳/借還款/信用卡/對帳等),
先看 [docs/MOZE_FEATURE_GAP_SD.md](./docs/MOZE_FEATURE_GAP_SD.md) —— 逐項列了
現況、修改內容、跨端依賴跟建議實作順序(Phase 0~7)。§2.1 通知中心
(Phase 0)已落地,見下方 `src/routers/notifications.py`。

## 架构总览(server 端)

FastAPI 应用,入口是 `src/main.py`,可执行文件是仓根 `server.py`(`make
dev-api` 实际跑的是 `uvicorn server:app`)。核心模块:

- `src/routers/` —— HTTP API,按 `<group>/` 包组织(见下方"路由组织")。
  子目录:`sync/`(推拉同步)、`write/`(按实体 CRUD)、`read/`(账本/工作区/
  汇总只读端点)、`ai/`(AI 记账解析、docs 问答)、`import_data/`(CSV 导入)。
- `src/sync_applier.py` —— 同步落盘的核心分发器,`_MERGE_SPECS` /
  `_UPSERT_DISPATCH` / `_DELETE_DISPATCH` 三张表决定每种 entity 怎么合并、
  怎么 upsert、怎么删除。
- `src/projection.py` —— `read_*_projection` 表的 upsert / delete /
  rename cascade 实现,是读路径的唯一权威源。
- `src/snapshot_builder.py` / `snapshot_cache.py` / `snapshot_mutator.py`
  —— `/sync/full` 按需从 projection 懒构建整本账本快照(不再主动写
  `ledger_snapshot`)。
- `src/websocket_manager.py` + `routers/ws.py` —— 多端实时推送。
- `src/mcp/` —— MCP server(`server.py` + `tools/read_tools.py` /
  `tools/write_tools.py`),给 Claude Desktop / Cursor 等 LLM 客户端暴露
  记账操作。
- `src/services/` —— 领域服务:`ai/`(LLM provider 适配 + 文档 RAG 问答)、
  `backup/`(rclone 多远端加密备份、调度、恢复)、`exchange_rate/`、
  `import_data/`、`data_cleanup/`、`notifications.py`(通知中心写入
  helper,见下)。
- `src/routers/notifications.py` + `src/models.py:Notification` —— 通知
  中心(MOZE_FEATURE_GAP_SD.md §2.1)。**user-global,不进
  `sync_changes`/projection**,是普通 REST 资源,跟本节其它"sync entity"
  的模式不一样。各功能(budget 超支、recurring 到期等)要发通知时,调
  `services.notifications.create_notification(db, user_id=..., category=...,
  title=..., body=..., payload=...)` 落一行,不 commit,由调用方业务事务
  一起提交;不要为了发通知单独开事务。
- `src/models.py` / `src/schemas.py` —— SQLAlchemy ORM 模型 / Pydantic
  schema。
- `src/database.py` —— SQLite(默认,WAL + busy_timeout,生产必需)和
  Postgres 双引擎支持,连接串取决于 `DATABASE_URL`。
- `src/config.py` —— `pydantic-settings`,`.env` 后 `.env.local` 覆盖
  （本地临时改配置不污染 `.env`,且 `.env.local` 已 gitignore)。

**`main.py` 顶部有一个必须保留的导入顺序**:`ensure_jwt_secret()` 必须
在任何 `from .routers ...` 之前执行,因为部分 router 模块顶层有
`settings = get_settings()`(`@lru_cache`),先导入 router 会让 settings
缓存住占位 JWT_SECRET,后续 env 变更不生效,生产环境校验直接 raise。改
`main.py` 顶部 import 顺序前務必读一遍那段注释。

### 路由组织

每个 HTTP API 组是 `src/routers/<group>/` 包形式,结构:

```
<group>/
  __init__.py     聚合 router,main.py 的 import 不变
  _shared.py      共享 imports / helpers / 常量 / router 实例
                  __all__ 显式列表(wildcard 默认不带下划线名字)
  <entity>.py     按资源拆分的 endpoint 文件,3 个 HTTP 方法(POST/PATCH/DELETE)
                  或按逻辑分组的 GET
```

修改某个 endpoint → 进对应 entity 文件,修改跨 endpoint 的共享逻辑 →
改 `_shared.py`。不要把业务加回到 `__init__.py`。

### 分 snapshot / projection / event log

同步层有三种存储形态,**不要混用**:

- `sync_changes`(事件流):append-only,`change_id` 自增,pull 增量同步
  的源头。永远只插入,从不 UPDATE。
- `read_*_projection`(5 张 denorm 表):读路径唯一权威源。LWW / rename
  cascade 落盘到这里。
- `ledger_snapshot`(JSON blob):方案 B 之后基本不写,`/sync/full` 按需
  从 projection 懒构建。**新代码不要再主动写 ledger_snapshot。**

### 新增 entity

如果要加一种新的 sync 实体(比如 "recurring_transaction"):

1. 新建 `read_*_projection` 表 + alembic migration
2. `src/projection.py` 加 upsert_* / delete_* / rename_cascade_* (如需)
3. `src/sync_applier.py` 登记 `_MERGE_SPECS` + `_UPSERT_DISPATCH` +
   `_DELETE_DISPATCH` 三张表
4. `src/routers/write/<entity>.py` 加 POST/PATCH/DELETE endpoints
5. `src/routers/read/ledgers.py` 或 `workspace.py` 加读端点
6. 补 pytest(`tests/test_projection_consistency.py` 已有
   mixed-entities 模板可参考)

### 测试

- `pytest tests/` 全过才能合代码
- 多账本场景至少有一个测试覆盖(一个 sync_id 在多个账本的 projection 里
  同时出现,dedup 行为)
- 添加新 entity 必须添加一条 `test_mobile_push_<entity>_partial_update_keeps_existing_fields`
  风格的 merge 契约测试 —— 防 2026-04 踩过的"漏 merge 某字段"类 bug

### 日志

- 同步决策点用 `logger.info("sync.push.accept entity=...")` 结构化日志
- 错误 path 用 `logger.exception` 带上 entity_type / action / sync_id /
  payload,方便 /sync/push 500 时定位到具体哪条 change 炸的
- 服务端有 admin 日志面板(web header 的 📜 按钮,admin 可见),默认筛
  ERROR 级别

## Frontend

Mobile 端(Flutter)和 Web 端(React)各自有仓,各自有 CLAUDE.md:

- Mobile: `../BeeCount/CLAUDE.md`
- Web: 前端源码在 `frontend/apps/web/`(Vite + React + TypeScript +
  Tailwind + shadcn 风格组件),pnpm workspace 下还有两个共享包:
  `frontend/packages/api-client`(与 server 交互的类型化客户端)、
  `frontend/packages/ui`(通用组件)、`frontend/packages/web-features`
  (跨页面业务逻辑)。改跨页面共享的东西先看这两个包里有没有现成的。

跟服务端同步相关的 mobile 契约(`ChangeTracker.recordUserGlobalChange` /
`recordLedgerChange`)在 mobile 仓 CLAUDE.md 里。Server 端的契约在上面
链的 `docs/SYNC_ARCHITECTURE.md` 里。

## 部署 / 运维

- 生产部署见 [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)
- 迁移相关见 [docs/MIGRATION.md](./docs/MIGRATION.md)
- 回滚 SOP 见 [docs/ROLLBACK_SOP.md](./docs/ROLLBACK_SOP.md)
- 可观测性(日志 / 指标)见 [docs/OBSERVABILITY.md](./docs/OBSERVABILITY.md)
