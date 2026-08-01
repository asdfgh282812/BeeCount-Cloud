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
(Phase 0)已落地,見下方 `src/routers/notifications.py`。§2.2/§2.3/§2.6
週期性收支/分期付款/退款(Phase 1,server 端)也已落地,見下方
`src/services/recurring_materializer.py` 一節。**Phase 0/Phase 1 的 web UI
(2026-07-30)已落地**:通知中心(header 🔔 铃铛,`apps/web/src/components/
NotificationBell.tsx`,轮询 `GET /notifications`,不接 WS)、週期性收支
(`/app/recurring-rules`,`RecurringRulesPage`/`RecurringRulesPanel`)、分期
付款(`/app/installment-plans`,`InstallmentPlansPage`/`InstallmentPlansPanel`)
——两者入口都在头像下拉「工具」组,仅账本 owner 可写(对齐 server
`_OWNER_ONLY_ROLES`)。退款入口(2026-07-30 起改版,取代旧版「新建交易表单
下拉选退款对象」)在交易详情弹窗(`TransactionDetailDialog.tsx`)的「退款」
按钮,点击后开建交易表单并预填原交易金额/备注/账户;income/expense 都能
被退款(仅 transfer 不行),退款交易类型自动取原交易的反向类型。2026-07-31
补了三项:①一笔交易只能被退一次,已退过款的交易按钮变灰并提示(server 端
`_assert_refund_target_not_already_refunded` 兜底,命中回 400
`TX_ALREADY_REFUNDED`);②原交易与退款交易双向勾稽且可点击跳转查看
(`GlobalEntityDialogs.tsx::handleJumpToTx`);③统计口径 netting
(`read/_shared.py::_projection_totals`、`read/workspace.py::
workspace_analytics`)对称处理两个退款方向。详见
`docs/MOZE_FEATURE_GAP_SD.md` §2.6/§2.12.3。mobile 端 UI 仍待排期。
**§2.4 拆帳(Phase 2,server + web UI,2026-07-31)已落地**:
`WriteTransactionCreateRequest/UpdateRequest.splits`(至少 2 笔、
tx_type 只能 expense/income、金额加总须等于交易 amount,校验见
`write/_shared.py::_validate_tx_splits`);新表
`read_tx_split_projection`(不是独立 sync entity,权威值走父交易
payload 的 `splits` 字段,`projection.upsert_tx` 每次整批
delete-then-insert 重建);`has_splits=True` 时父行
`category_sync_id`/`category_name` 清空,`workspace_analytics`
按 split 明细展开分别累加分类排行,分类预算用量同样把 split 明细计入
对应分类。**拆帳交易可以整笔退款(2026-07-31 起,行为跟非拆帳退款一致
——退款按原交易总金额整笔退,不会拆回各 split 明细各自的金额)**,但
退款交易本身不能同时拆帳(splits + refund_of_id 不能共存于同一笔交易),
拆帳跟週期性收支/分期付款组合暂不支持(write 层 + 前端 UI 都挡)。web
入口:`TransactionsPanel.tsx` 分类字段旁「拆分到多个分类」开关;
`TransactionDetailDialog.tsx`「退款」按钮不再对拆帳交易灰掉。详见
`docs/MOZE_FEATURE_GAP_SD.md` §2.4/§2.6,测试见 `tests/test_tx_splits.py`
+ `tests/test_refund_stats.py`。mobile 端(本地 SQLite 子表)仍待排期。
**§2.5 借還款追蹤 / §2.7 範本(Phase 3,server + web UI,2026-08-01)已
落地**:借還款新表 `read_debt_projection`(`direction`/`counterparty_
name`/`principal_amount`/`due_at`/`note`)+ `read_tx_projection.
debt_sync_id` 反查字段(同 `refund_of_sync_id`/`installment_plan_sync_
id` 模式)——**`remaining_amount`/`status` 不落库**,读路径
(`read/ledgers.py::list_debts`)从反查交易即时加总算出,跟
`read_installment_plan_projection.paid_periods` 是同一取舍(避免两条
独立写路径都要挂欠款余额联动重算逻辑);还款/收款走一般交易新增的
`debt_id` 字段(`WriteTransactionCreateRequest/UpdateRequest.debt_id`,
容许多笔部分还款,`write/_shared.py::_assert_debt_exists` 校验存在性);
DELETE 仅在尚未收到任何还款交易时允许(`_assert_debt_has_no_
repayments`,命中回 400 `DEBT_HAS_REPAYMENTS`);到期提醒
(`src/services/debt_reminders.py`)复用 §2.2/§2.3 的低频 asyncio loop,
去重靠查 `notifications` 表历史(不额外加 `reminder_sent_at` 落库)。
範本新表 `read_tx_template_projection` + `src/routers/write/
tx_templates.py`(POST/PATCH/DELETE + `POST .../apply` 把范本内容套成
一笔新交易,`amount`/`note` 可在套用时临时覆盖)。web 入口都在头像下拉
「工具」组:`/app/debts`(`DebtsPage`/`DebtsPanel`)、`/app/tx-templates`
(`TxTemplatesPage`/`TxTemplatesPanel`)。新建/编辑/删除仅账本 owner 可写,
还款/套用走一般交易写权限。详见 `docs/MOZE_FEATURE_GAP_SD.md` §2.5/§2.7,
测试见 `tests/test_debts.py` + `tests/test_tx_templates.py`,手动测试
清单见 `docs/PH3_DEBTS_TEMPLATES_WEB_UI_MANUAL_TEST_PLAN.md`。mobile 端
仍待排期。**借還款體驗補強(2026-08-01 第二輪)**:①主交易表單
(`TransactionsPanel.tsx`)也能直接選欠款(`TxForm.debt_id`,
expense/income 显示,transfer 不适用),`GlobalEditDialogs.tsx`(全局编辑
弹窗)同步补上;②交易 ↔ 借還款雙向勾稽:`ReadTransactionOut` 补
`debt_counterparty_name`/`debt_direction`(读端点原本漏填 `debt_id` 本身
是个既有 bug,这次一起修了,`read/ledgers.py::list_transactions` +
`read/workspace.py` 都改),`TransactionDetailDialog.tsx` 显示可点击的
「關聯欠款」行跳去 `/app/debts?highlight=<id>`
(`GlobalEntityDialogs.tsx::handleJumpToDebt`);`DebtsPanel.tsx` 的還款
記錄改成可點擊,呼叫全域 `dispatchOpenDetailTx` 開交易詳情;③新增
`closed_at` 欄位(`read_debt_projection.closed_at`,migration
`0026_debt_closed_at`)—— 不一定要還清全額才算結束,PATCH 帶
`closed_at` 走既有的 owner-only debt update endpoint(不是新 endpoint),
`list_debts` 的 status 判斷優先看 `closed_at`(蓋過 remaining_amount 算
出來的 open/partial/settled),`debt_reminders.py` 結案的欠款跳過不提醒;
④到期日只存日期不存時分,`src/snapshot_mutator.py::_date_only_iso8601`
在 create/update_debt 寫入時 truncate 到當天 UTC 零點(欄位型別仍是
DateTime,沒有改 schema,伺服器端兜底不完全依賴前端),前端
`DebtsPanel.tsx` 對應改用 `<input type="date">` + **UTC** getter
(`isoToDateInput`/`formatDateOnlyUTC`)取值/顯示,避免本地時區換算出現
「少一天」的 bug。**借還款體驗補強(2026-08-01 第三輪,純前端)**:第二
輪的「關聯欠款」下拉只能選既有欠款當還款用,這輪加「+ 建立新欠款」
——記交易時順便新建一筆欠款,`direction` 由 `tx_type` 自動推算(收入=
`payable` 我欠對方,支出=`receivable` 對方欠我,跟還款方向的映射對稱)。
**這筆交易不會把自己的 `debt_id` 指向新建的欠款**(它是起點不是還
款,指了會被 `list_debts` 的 `repaid_by_debt` 累加邏輯誤判成立刻結
清),新欠款走獨立一次 `POST .../debts` 呼叫,`principal_amount` 帶交
易金額;owner-only(前端按 `role === 'owner'` 決定顯不顯示這個選項),
失敗只提示不回滾已保存的交易。改動:`TxForm` 加
`new_debt_counterparty_name`/`new_debt_due_at`(`forms.ts`),
`TransactionsPanel.tsx` 的欠款 Select 加 `__new__` sentinel + 內嵌欄
位,`TransactionsPage.tsx`/`GlobalEditDialogs.tsx` 兩處提交邏輯都補上
(對齊既有的雙表單維護慣例)。詳見
`docs/PH3_DEBTS_TEMPLATES_WEB_UI_MANUAL_TEST_PLAN.md` §五。**到期提醒沒發
出的 bug 修復(2026-08-01,server 端)**:`debt_reminders.send_due_debt_
reminders` 原本掛在 `recurring_materializer` 共用的 loop 上——那個 loop
在「ph1.5 fix」時特意從 15 分鐘改成 24 小時(因為週期性收支規則建立當下
就已經批次生成過視窗,daily 續窗只是長尾兜底,降頻沒問題),但債務到期
提醒是後來 Phase 3 才接上去的,沒人重新評估這個降頻對它合不合適——加上
那個 loop 是先 `sleep` 再跑,冷啟動的服務器要等滿 24 小時才會發第一次
提醒,體感就是「設了過期時間但完全不通知」。修法:`src/main.py` 拆成獨立
的 `_start_debt_reminder_loop`,啟動時立即跑一次、之後每 15 分鐘一次,
不再跟 recurring materializer 共用 24 小時 loop;`recurring_materializer`
自己的 loop/interval 不變。手動立即觸發仍是
`POST /internal/tasks/materialize-recurring`(admin scope,回傳體裡
`debt_reminders` 計數)。

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
  helper,见下)、`recurring_materializer.py`(週期性收支/分期付款到期物化,
  见下)。
- `src/routers/notifications.py` + `src/models.py:Notification` —— 通知
  中心(MOZE_FEATURE_GAP_SD.md §2.1)。**user-global,不进
  `sync_changes`/projection**,是普通 REST 资源,跟本节其它"sync entity"
  的模式不一样。各功能(budget 超支、recurring 到期等)要发通知时,调
  `services.notifications.create_notification(db, user_id=..., category=...,
  title=..., body=..., payload=...)` 落一行,不 commit,由调用方业务事务
  一起提交;不要为了发通知单独开事务。
- `src/services/recurring_materializer.py` + `read_recurring_rule_projection`
  / `read_installment_plan_projection`(MOZE_FEATURE_GAP_SD.md §2.2/§2.3)。
  跟其它 sync entity 一样走 `_MERGE_SPECS`/`_UPSERT_DISPATCH`/
  `_DELETE_DISPATCH` + `src/routers/write/recurring_rules.py` /
  `installment_plans.py`;区别是这两种 entity **到期后还要自动生成
  transaction**——`materialize_all_due()` 由 `main.py` 的周期性 asyncio
  loop(每 15 分钟)调用,也可以手动 `POST /internal/tasks/
  materialize-recurring`(admin scope)立即触发。新增"到期自动生成实体"
  这类功能时可以复用这个 loop 模式,不需要重新引入 APScheduler。
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
