# Phase 3 借還款追蹤（§2.5）/ 交易範本（§2.7）— 测试报告 + 手动测试清单

- 测试日期：2026-08-01
- 测试范围：[MOZE_FEATURE_GAP_SD.md](./MOZE_FEATURE_GAP_SD.md) §2.5 借還款
  追蹤、§2.7 交易範本（server + web UI，两者彼此独立，按文档 Phase 3 顺序
  一起实作）
- 本轮环境：`make dev-api` / `make dev-web` 都跑起来了，**server 端契约
  用 pytest 自动化验证 + 对着真实跑起来的 dev server 用 curl 走过一遍完整
  流程**；但会话中途 Safari 浏览器自动化工具断线，**web UI 的实际点击
  流程（弹窗/表单校验/按钮状态）需要你自己在浏览器里过一遍**，清单见
  下方「二」。

---

## 一、已自动化验证的部分（这次会话跑过，全部通过）

### 1. Backend — pytest

```
. .venv/bin/activate && pytest tests/ -q
```

- 新增 `tests/test_debts.py`（9 个用例）：
  - 建欠款 + 列表返回 `remaining_amount == principal_amount`、
    `status == "open"`
  - 建一笔带 `debt_id` 的交易 = 一次还款/收款：`remaining_amount` 相应
    减少，`status` 变 `partial`；还清后变 `settled`
  - `debt_id` 指向不存在的欠款 → 400 `DEBT_NOT_FOUND`
  - PATCH 只改 `note`/`due_at`，`principal_amount`/`direction` 不受影响
  - DELETE：已有还款记录的欠款删除被拒（400 `DEBT_HAS_REPAYMENTS`）；
    没有还款记录的可以正常删除
  - 多账本隔离：两个账本各自的欠款互不可见
  - mobile `/sync/push` 的 `debt` merge 契约（partial update 保留旧值）+
    `transaction` entity 的 `debtId` 反查字段同款保留语义
  - `services.debt_reminders.send_due_debt_reminders`：临期欠款发一条
    `reminder` 通知，第二次跑不重复；已结清的欠款不提醒
- 新增 `tests/test_tx_templates.py`（9 个用例）：
  - CRUD + 读接口把 `category_id`/`account_id` 解析成对应的
    `category_name`/`account_name`
  - `POST .../apply`：套用範本内容建一笔新交易；`amount`/`note` 可在
    套用时覆盖，其它栏位固定沿用範本
  - 套用不存在的範本 → 404
  - mobile `/sync/push` 的 `tx_template` merge 契约
  - 多账本隔离
- 全量回归：`pytest tests/ -q` 除一个**跟本次改动无关的既有 flaky
  用例**外全过 —— `test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`
  会按当前日期算出 3 或 4 个月频 occurrence，属于该测试自身对"现在"敏感
  （用 `git stash` 验证过：不带本次改动的 `main` 分支跑这条测试同样失败，
  确认不是这次改动引入的）
- `ruff check` / `mypy src`：新增/改动的文件里除既有基线噪音（本仓库
  `ruff check src tests alembic` 目前有 ~2000 条历史遗留 warning，主要是
  `from ._shared import *` 星号导入模式下 F405 提示，属于既定架构选择，
  不是本次改动引入）之外没有新问题；`snapshot_builder.py` 一处变量名
  遮蔽（mypy `no-redef`）和 `debt_reminders.py` 一处 `datetime | None`
  窄化问题已修掉
- Alembic 迁移 `0025_debts_and_tx_templates` 在真实 SQLite 文件上跑过
  `upgrade head` / `downgrade -1`，双向都成功；也在你本机真实的
  `beecount.db`（不是测试库）上跑了 `upgrade head`（升级前已备份成
  `beecount.db.bak-before-0025`，如需回滚可以 `alembic downgrade -1`
  或直接拿备份文件替换）

### 2. Backend — 对真实跑起来的 dev server 做端到端验证

会话里 `make dev-api`/`make dev-web` 已经在后台跑着（`--reload` 模式，
改代码自动生效），用一个临时注册的测试账号 + 临时账本，对着真实的
`beecount.db` 走了一遍完整 HTTP 流程（用完已经 `DELETE` 掉那个临时账本，
测试账号残留在 `users` 表里，如果介意可以在 `/app/admin/users` 手动删掉，
邮箱形如 `ph3smoketest_*@example.com`）：

- `POST /write/ledgers/{id}/debts` 建欠款 → `GET /read/ledgers/{id}/debts`
  返回 `remaining_amount=1000, status=open`
- `POST /write/ledgers/{id}/transactions` 带 `debt_id` 还 400 →
  再查 `remaining_amount=600, status=partial`，`repayments` 里有这笔交易
- 用不存在的 `debt_id` 建交易 → `400 {"error":{"code":"DEBT_NOT_FOUND"}}`
- 已有还款记录时 `DELETE .../debts/{id}` →
  `400 {"error":{"code":"DEBT_HAS_REPAYMENTS"}}`
- 还清剩下的 600 → `remaining_amount=0, status=settled`
- `POST /write/ledgers/{id}/tx-templates` 建範本 →
  `GET /read/ledgers/{id}/tx-templates` 返回正确内容
- `POST .../tx-templates/{id}/apply`（覆盖 amount）→ 新交易的金额/备注
  跟覆盖值一致，其它字段沿用範本
- `DELETE .../tx-templates/{id}` → 再查列表为空

### 3. Frontend

```
cd frontend && pnpm -C apps/web build   # tsc -b && vite build，无报错
cd frontend && pnpm -C apps/web test    # vitest run，10 个文件 73 个用例全过
```

- `pnpm -C apps/web build` 干净通过（无 TS 类型错误），确认新增的
  `DebtsPage`/`TxTemplatesPage`/`DebtsPanel`/`TxTemplatesPanel` 以及
  `@beecount/api-client` 里新增的类型/函数跟既有代码接得上
- `router.test.ts` 补了 `/app/debts`/`/app/tx-templates` 的路由解析 +
  反解回路径的用例
- **没有做的**：会话中途浏览器自动化(Safari MCP)工具断线，没能实际点
  开页面截图验证弹窗/按钮渲染是否符合预期。下面「二」是需要你实机走一遍
  的清单。

---

## 二、需要你在浏览器里手动过一遍的清单

先确认 `make dev-api`（另开一个终端）+ `make dev-web` 都在跑（如果延续
本次会话，这两个进程应该已经在后台跑着，`http://localhost:5173` 直接能
打开），登录任意账号，选一个账本。

### 2.1 入口

1. 点右上角头像，悬浮出下拉菜单，「工具」分组里应该能看到「预算」「週期性
   收支」「分期付款」之后多了「借還款」「交易範本」两个新入口（图标分别是
   一个"钱币手"icon 和"文件叠"icon）
2. 依次点开，URL 应该分别跳到 `/app/debts` 和 `/app/tx-templates`

### 2.2 借還款 —— 新建 + 列表

1. 进「借還款」页，应该看到空状态提示「该账本还没有欠款记录」+「新增
   欠款」按钮
2. 点「新增欠款」，弹窗里选方向「我欠款」，对方填「老王」，本金填
   `1000`，到期日留空，备注填「借车费」，提交
3. 列表应该出现一张卡片：老王 / 我欠款 / 未还（灰色徽章）/ 剩余
   ¥1000.00 / 本金 ¥1000.00，进度条是空的（0%）

### 2.3 借還款 —— 记还款 + derived 状态

1. 点卡片上的「记一笔还款」按钮，弹窗预填金额 = 剩余金额(1000)，改成
   `400`，选一个账户（如果账本下有账户的话），日期用默认今天，提交
2. 卡片应该刷新：剩余变 ¥600.00，状态徽章变「部分已还」（橙色），进度条
   走了 40%，「还款记录」区块出现一行 400 元的记录
3. 再点「记一笔还款」，这次预填金额应该是 600（剩余全额），直接提交
4. 卡片状态徽章变「已结清」（绿色），进度条满格，「记一笔还款」按钮应该
   变灰不可点

### 2.4 借還款 —— 编辑 / 删除

1. 点「编辑」，应该只能改对方名称/到期日/备注，本金金额跟方向的输入框
   应该是禁用状态（灰色不可编辑）
2. 改个备注保存，卡片应该同步更新
3. 点「删除」—— 因为这笔欠款已经有还款记录，应该看到操作被挡下的错误
   提示（"这笔欠款已经有还款记录，无法删除"），删除按钮本身在有还款记录
   时应该是禁用的（灰色，不会真的弹删除确认框）
4. 新建一笔没有任何还款的欠款（比如"小华"），确认这张卡片的删除按钮是
   可点的，点了之后弹二次确认框，确认后卡片消失

### 2.5 借還款 —— 权限（如果账本不是 owner 角色可以跳过）

1. 用非 owner 身份（editor/viewer）打开这个账本的「借還款」页 ——
   「新增欠款」按钮应该是禁用的，卡片上的「编辑」「删除」也应该禁用
2. 但「记一笔还款」按钮（因为走一般交易写权限）在 editor 角色下应该仍然
   可点（viewer 角色则不行，取决于账本的一般交易写权限规则）

### 2.6 交易範本 —— 新建 + 套用

1. 进「交易範本」页，空状态提示「该账本还没有交易範本」
2. 点「新增範本」，名称填「早餐」，类型「支出」，金额 `15`，选一个分类
   （如「餐饮」）+ 账户，备注「豆浆油条」，提交
3. 列表出现一张卡片：早餐 / 餐饮 · 账户名 / 豆浆油条 / ¥15.00，右下角
   有「套用」「编辑」「删除」三个按钮
4. 点「套用」，弹窗预填今天日期 + 金额 15 + 备注「豆浆油条」，把金额改成
   `18.5`，提交
5. 去「交易」页确认新增了一笔 18.5 元、备注「豆浆油条」、分类是「餐饮」的
   支出交易；範本本身应该没有被这次套用改变（金额还是显示 15）

### 2.7 交易範本 —— 转账類型

1. 新建範本，类型切到「转账」—— 分类选择器应该消失，换成「转出账户」
   「转入账户」两个下拉
2. 不选转出/转入账户直接提交 —— 应该被挡下并提示需要选择转账账户
3. 选好两个不同账户提交成功，卡片摘要行应该显示「账户A → 账户B」

### 2.8 交易範本 —— 编辑 / 删除

1. 编辑早餐範本，改金额成 `20`，改名成「豪华早餐」，保存，卡片应该同步
2. 删除範本，二次确认后卡片消失；再去「交易範本」页刷新确认列表不再有
   这条

### 2.9 到期提醒（可选，pytest 已覆盖核心逻辑，浏览器这边看通知中心即可）

1. 建一笔 `due_at` 是明天的欠款
2. 用 admin token 手动触发一次
   `POST /api/v1/internal/tasks/materialize-recurring`（或者等本机部署的
   每日排程自然触发,但本地开发环境一般不会等 24 小时）
3. 点右上角 🔔 通知中心图标，应该能看到一条"即将到期"的提醒，内容包含
   对方名称 + 剩余金额

---

## 三、已知限制 / 故意不做的部分

- ~~还款/收款不接入主交易表单~~：**2026-08-01 第二轮已解决**，见下方
  「四」。主交易表单现在也能直接选欠款了。
- **拆帳子项目个别还款/範本套用带拆帳**：範本目前只存单一
  分类/金额组合，不支持"套用一个已经拆好帳的範本"；欠款的还款交易也
  不支持带 `splits`（技术上没有互斥校验，但 UI 没提供这个组合入口）。
- **範本排序**：`sort_order` 字段在 API/DB 层已经就绪，但 web UI 没做
  拖拽排序,新建範本一律 `sort_order=0`,列表按 `sort_order, name` 排序
  (相同 sort_order 时按名称字母序)。
- **到期提醒去重**：不是按"每天最多提醒一次"这种时间窗口去重,而是"这笔
  欠款只要曾经提醒过就不会再提醒第二次"(查 `notifications` 表历史记录,
  不额外在 `read_debt_projection` 上加 `reminder_sent_at` 列 —— 原因见
  `src/services/debt_reminders.py` 模块开头注释:避免绕开
  sync_changes 直接改 projection 破坏"projection 只能从事件回放重建"的
  一致性契约)。如果编辑了 `due_at` 想重新触发提醒,目前没有入口,需要
  产品面另外设计。
- **mobile 端**：跟以往几个 Phase 一样,这次只做了 server + web UI,
  mobile(Flutter)本地 SQLite 子表/UI 仍待排期。

---

## 四、體驗補強（2026-08-01 第二輪）

针对第一轮上线后的使用反馈,加了四项:①主交易表单也能直接掛欠款(不
用跳去借還款頁)；②交易頁 ↔ 借還款頁雙向勾稽；③欠款「結案」按鈕(不
一定要還清全額,可重新開啟)；④到期日只存日期,不存時分。详见
`CLAUDE.md`「借還款追蹤」段落。

### 4.1 自动化验证(已跑过,全部通过)

- Backend:`pytest tests/ -q`(除已知跟本次无关的既有 flaky 用例
  `test_recurring_rules.py::test_recurring_occurrence_update_overridden_skipped_by_update_from`
  外全过),`tests/test_debts.py` 新增 8 条用例(結案/重新開啟狀態覆蓋、
  到期日 truncate 到當天 UTC 零點、結案欠款不再提醒、交易讀取端點回傳
  `debt_id`/`debt_counterparty_name`/`debt_direction`)
- Frontend:`pnpm -C apps/web build`(`tsc -b && vite build`)、
  `pnpm -C apps/web test`(vitest,73 条全过)均无错误
- Alembic 迁移 `0026_debt_closed_at` 在本机 `beecount.db` 上跑过
  `upgrade head`,成功

### 4.2 需要你在浏览器里手动过一遍的清单

1. **主表單掛欠款**:先在「借還款」頁新建一筆欠款(例如"欠小明 500")。
   回到「交易」頁點「新增交易」,選支出/收入類型,應該能看到一個「關聯
   欠款」下拉(轉帳類型沒有這個欄位),選中剛才那筆欠款送出。去借還款
   頁確認 remaining_amount 有相應減少、還款記錄多了這筆。
2. **交易 → 借還款跳轉**:點開剛才那筆帶欠款的交易詳情,應該能看到
   「關聯欠款」那一行顯示對方名字 + 應付/應收,點一下應該跳去借還款
   頁,並且對應卡片有高亮外框(定位一下就消失也算正常,只要能看到明顯
   跳到了正確卡片)。
3. **借還款 → 交易跳轉**:在借還款頁,點某筆欠款下方的還款記錄(日期 +
   金額那一行),應該直接彈出那筆交易的詳情彈窗(不用整頁跳轉)。
4. **結案 / 重新開啟**:找一筆還沒還清的欠款(或先新建一筆不還款),點
   「結案」按鈕,應該跳一個確認彈窗提示剩餘金額,確認後狀態變「已結
   案」,「記還款」按鈕變灰。再點「重新開啟」,應該恢復原本的
   open/partial 狀態。到期提醒(如果有設到期日且快到期)結案後不應該
   再收到通知(這條 pytest 已覆蓋,瀏覽器這邊主要看 UI 狀態切換是否正
   常)。
5. **到期日只存日期**:新建/編輯一筆欠款,到期日欄位現在應該是純日期
   選擇器(沒有時分),選一天存檔後重新整理頁面,顯示的日期應該跟你選
   的一致(不會因為時區差一天)。
6. **編輯既有交易改欠款關聯**:編輯一筆已經有欠款關聯的交易,「關聯欠
   款」下拉應該正確回顯原本選的那筆;改選成「不掛欠款」存檔後,去借
   還款頁確認那筆還款記錄消失、remaining_amount 恢復。

---

## 五、體驗補強（2026-08-01 第三輪）—— 交易頁直接建立新欠款

第二輪的「關聯欠款」下拉只能選**既有**欠款(當還款用)。使用者反饋:
真正想要的是在記一筆交易時**順便建立一筆新欠款**——這筆交易本身就是
欠款的起點,不是還款。例如:記一筆「收入 500」代表跟朋友借了 500 元
(之後要還),或記一筆「支出 500」代表借給朋友 500 元(之後要收)。

### 5.1 方向推算與資料模型(重要,跟第二輪的「選既有欠款」語意不同)

- `direction` 由 `tx_type` 自動推算,不再讓使用者手動選:**收入 →
  `payable`(我欠對方)**,**支出 → `receivable`(對方欠我)**。跟既有
  「記還款」的反向映射(`receivable` 的還款走 `income`,`payable` 的
  還款走 `expense`)剛好對稱。
- **這筆交易本身不會把 `debt_id` 設成新建的這筆欠款**——它是欠款的
  起點,不是還款。如果設了,`read/ledgers.py::list_debts` 的
  `repaid_by_debt` 累加邏輯會把這筆交易當還款算,新欠款一建立就會被
  這筆交易的金額直接沖抵成 `settled`(金額對不上會出現詭異的部分結
  清狀態)。所以前端送出時 `debt_id` 對 `'__new__'` sentinel 值特殊處
  理,實際送給 server 的是 `null`;新欠款走獨立的
  `POST /ledgers/{id}/debts` 呼叫,`principal_amount` 帶這筆交易的金
  額,交易和欠款是兩個獨立實體,只是同一次操作的兩個副作用。
- 建立新欠款是 owner-only(跟「借還款」頁新建欠款同一個 endpoint /
  同一組權限),前端按目前寫入帳本的 `role === 'owner'` 決定要不要顯
  示「+ 建立新欠款」這個選項(共享帳本的 editor 角色看不到這個選項,
  但仍然看得到「選既有欠款」)。
- 交易保存成功後,新欠款用 `retryOnConflict` 單獨再打一次寫入;如果
  這一步失敗只彈提示（交易已保存但關聯欠款建立失敗）,不會回滾已經
  保存成功的交易(目前沒有跨實體的事務機制)。

### 5.2 自动化验证(已跑过,全部通过)

- Frontend:`pnpm -C apps/web build`(`tsc -b && vite build`)、
  `pnpm -C apps/web test`(vitest,73 条全过,含 i18n 三语 key 對齊)
  均无错误
- 純前端改動,沒有動到 backend/schema/migration,`pytest tests/` 不受
  影響(沒有重跑,邏輯上不涉及)

### 5.3 需要你在浏览器里手动过一遍的清单

1. **建立新欠款(收入 → 我欠對方)**:「新增交易」選收入類型,「關聯欠
   款」下拉裡應該多一個「+ 建立新欠款」選項(前提:目前帳本你是
   owner)。選中後應該展開「對方名字」(必填)+「到期日」(可選,純日
   期)兩個欄位,上方有提示文字說明會記成「我欠對方」。填對方名字後
   送出,去借還款頁確認多了一筆新欠款,方向是「我欠」,金額等於這筆
   交易金額,狀態是 open(未還)。
2. **建立新欠款(支出 → 對方欠我)**:同上,改選支出類型,提示文字應
   該說明會記成「對方欠我」,送出後借還款頁那筆新欠款方向應該是
   「欠我」。
3. **新欠款不會被立刻結清**:確認步驟 1/2 建立的欠款狀態是 open,
   remaining_amount 等於 principal_amount(不會因為這筆交易本身被算
   成還款而變成 settled)。
4. **對方名字必填**:選了「+ 建立新欠款」但不填對方名字直接送出,交
   易應該正常保存(對方名字是新欠款的必填項,不是交易的必填項),但
   不會多出新欠款(因為沒填名字這步直接跳過,不會拿空字串去建)。
5. **非 owner 看不到這個選項**:如果有共享帳本且你是 editor 角色,切
   到那個帳本記交易,「關聯欠款」下拉應該只有「不掛欠款」和既有欠款
   列表,沒有「+ 建立新欠款」。
6. **轉帳沒有這個欄位**:選轉帳類型時整個「關聯欠款」區塊(含「+ 建立
   新欠款」)應該不顯示,跟第二輪的既有行為一致。
