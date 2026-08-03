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

**§2.9 信用卡管理整組功能(Phase 4,server + web UI,2026-08-01)已落地**:
主帳戶(合併帳單)/信用卡繳款/免息期推薦三項落地新代碼,帳單分期(複用既有
`installment_plans`,`account_id` 指向信用卡即可)/自動扣繳(複用既有
`recurring_rules`,`RecurringRulesPanel.tsx` 本來就支援
`advanced_mode=monthly_day` 對齊 `payment_due_day`)兩項確認**不需要任何
新代碼**。主帳戶合併帳單:`user_account_projection` 加自我參照
`parent_account_id`(migration `0027_account_parent_id`),`snapshot_
mutator.py::_assert_valid_account_parent` 擋自我參照/未知帳戶/循環;新讀
端點 `GET /ledgers/{id}/accounts/{account_id}/billing-summary` 把
`parent_account_id == account_id` 的子卡跟主卡一起,用主卡自己的
`billing_day`/`payment_due_day` 合併算「最近一次已結束的帳單週期」應繳/
已繳/剩餘應繳,金額不落庫即時從 `read_tx_projection` 加總(同 §2.5 借還款
`remaining_amount` 的取捨)。信用卡繳款:`POST .../accounts/{account_id}/
card-payment` 語意化端點,本質建一筆 `tx_type=transfer`,走一般交易寫權限
(不是 owner-only);沒帶 `note` 時自動帶入帳單週期區間當備註,是「衝抵當期
應繳金額」標記的唯一落點,不需要額外欄位。免息期推薦:`GET .../accounts/
{account_id}/interest-free-suggestion` 純計算端點,`src/services/
credit_card.py` 無 DB 依賴,**月底夾斷是這塊最容易踩的坑**——往前/後平移
月份必須用原始 `billing_day`/`payment_due_day` 重新夾斷,不能拿已經夾斷
過的 `date.day` 反推(比如 `billing_day=31` 在二月夾斷成 28,28 往前推一
個月會錯算成 1 月 28 號而不是 31 號),寫單元測試時當場踩到並修掉,測試
鎖了這個邊界。web:`AccountsPanel.tsx` 信用卡欄位區加「主帳戶(合併帳單)」
下拉(**這裡也踩了一個坑**:新建帳戶時 `form.editingId` 是 `null`,過濾條件
如果寫成 `r.parent_account_id !== form.editingId` 會在新建模式下把所有
"本來就沒掛靠"的信用卡帳戶全部誤濾掉,只有編輯模式才需要排除"自己的子卡"
避免一步形成循環,這個 bug 是瀏覽器端到端測試抓到的,純 pytest 測不出來
因為後端測試直接打 API、不經過這段前端下拉過濾邏輯);`AccountDetailDialog.
tsx` 新增「信用卡帳單」卡片(合併摘要 + 繳款按鈕 + 免息期建議文案)。測試見
`tests/test_credit_card.py`(20 例),手動測試清單 + 瀏覽器端到端驗證記錄見
`docs/PH4_CREDIT_CARD_WEB_UI_MANUAL_TEST_PLAN.md`。mobile 端仍待排期。

**§2.9 信用卡管理改版為「群組」模型(2026-08-02,server + web UI)**:上面
2026-08-01 那版把主帳戶做成一張普通的 `credit_card` 帳戶(可以自己被記
交易/被單獨繳款),使用者反馈这不對——現實情境是「同一家銀行辦了好幾張
卡,銀行本身變成主帳戶」,主帳戶該是純管理容器,不該是能自己刷卡/收款的
獨立帳戶,而且這個「群組」概念以後也會用在銀行帳戶(不限信用卡)。改動:
①新增 `account_type = "account_group"`,`credit_limit`/`billing_day`/
`payment_due_day` 搬到群組自己身上(子帳戶不再各自帶這些欄位);
②`parent_account_id` 只能指向 `account_group`(`snapshot_mutator.py::
_assert_valid_account_parent` 校驗,group 不能巢狀掛靠、不能自我掛靠、
不能循環);③新 helper `_assert_account_not_group`(`routers/write/
_shared.py`)擋 account_group 被一般交易/週期性收支/分期付款/範本套用
拿來當帳戶用,在 `transactions.py`/`recurring_rules.py`/
`installment_plans.py`/`tx_templates.py` 四個寫入路徑都掛了這個檢查;
④group 還有子帳戶掛靠時不能刪除(同一個「不要 orphan」原則,`snapshot_
mutator.py::delete_account`)。**billing-summary 的 `remaining_due` 順便
改成「終身跑動餘額」計算**(子帳戶終身消費減終身已繳,不分期別窗口)——
原本按「結帳日之後」窗口查 paid_amount 的寫法,溢繳只在當下那次查詢有效,
下一期結帳日一過、查詢窗口往前移,溢繳就從計算裡消失了,這是 2026-08-01
版本埋的一個潛在 bug,這次一起修掉;順便加 `available_credit`(=
credit_limit - remaining_due,純顯示計算,不落庫,溢繳時自然超過原始
額度,不用另外調整 credit_limit 欄位)。**信用卡繳款改成群組分攤**:
使用者在群組頁面繳一筆總額,後端依「每個子帳戶自己的應繳金額」分攤 ——
足額或溢繳時每個子帳戶都拿到剛好付清的金額,剩下的溢繳打一筆 transfer
記在群組自己身上(這是 `account_group` 唯一可以當 transfer 目標的例外,
只在這個內部流程走,一般交易寫入路徑仍然擋它);金額不夠付清全部時按
應繳金額比例分攤,不會讓群組出現假的「溢繳」。**新增到期提醒**
(`src/services/credit_card_reminders.py`,仿 §2.5 `debt_reminders.py`
的模式,掛 `main.py` 同一個 15 分鐘 loop):結帳日當天/到期前 7 天/到期
當天三種時機各發一次通知(`category="card_due"`,§2.1 通知中心設計時就
預留了這個分類,一直沒接上,這次補齊),去重 key 是 `accountId + cycleEnd
+ kind`(每期都要各自提醒一次,不是「這張卡提醒過沒有」)。**共用計算邏輯
抽到 `src/services/credit_card_billing.py`**:billing-summary 讀端點/
card-payment 分攤端點/到期提醒都要算「這個群組現在該繳多少」,三處各自算
一份容易漏改,抽成 `compute_group_billing()` 集中維護。測試見
`tests/test_credit_card.py`(rewrite 後涵蓋 group 模型校驗 + 終身餘額
carry-forward + 分攤三種情境)+ 新增
`tests/test_credit_card_reminders.py`。web 側額外在信用卡帳單卡片加了
「設定自動扣繳」/「新增帳單分期」捷徑按鈕(帶 `?account=<id>` 預填目標
頁面表單,群組只有一張子卡時才能明確預填)。

**§2.9 群組模型 web UI 手測踩出的四個問題修復(2026-08-03)**:上面
2026-08-02 兩輪都只驗證了 pytest + build,沒有走完整瀏覽器手測,使用者
自己測完回報四個問題,逐一修復:①**account_group 不該在交易表單被選到**
——`TransactionsPanel.tsx`(以及 `RecurringRulesPanel.tsx`/
`InstallmentPlansPanel.tsx`/`TxTemplatesPanel.tsx`/`DebtsPanel.tsx`)的帳戶
下拉選單過濾掉 `account_type === 'account_group'`;順帶挖到一個更深的既有
bug——`GlobalEditDialogs.tsx`(全局編輯彈窗,跟 `TransactionsPage.tsx` 是
維護慣例上的兩份獨立提交邏輯之一)的提交 payload 一直只送 `account_name`/
`from_account_name`/`to_account_name`,沒送對應的 `*_id`,導致
`_assert_account_not_group` 這種靠 `account_id` 判斷的後端校驗在這個入口
完全被繞過(create 因為走另一個表單有送 id 所以會擋、update 走這個彈窗
就悄悄放行,且不只是 account_group 這個 case——任何透過這個彈窗改帳戶
名字的操作,底層 accountId 外鍵其實都沒有真的跟著改,只是顯示字串變了,
是本次意外挖到的既有 bug,不限 account_group);修法是幫這三個 id 欄位
補上跟 `TransactionsPage.tsx` 一致的 name→id 解析。②**account_group 卡片
在帳戶列表/詳情頁一直顯示 0**——`read/workspace.py::list_workspace_accounts`
按 `account_sync_id` 聚合統計時,group 自己永遠没有交易(`_assert_account_
not_group` 擋住了),所以 income/expense/balance 恆為 0;改成群組行的這四
個欄位由 `parent_account_id` 指向它的子帳戶們加總回填,子帳戶自己的統計
不受影響。③**沒有掛靠任何群組的獨立信用卡,也該有群組的全部功能**(繳費/
分期/免息期建議)——`credit_card_billing.py` 新增 `is_billing_root`/
`resolve_billing_children`:一張帳戶要嘛是 `account_group`,要嘛是沒有
`parent_account_id` 的獨立 `credit_card`(這時自己既是「群組」也是唯一
「成員」),才能被 billing-summary/card-payment/interest-free-suggestion
直接查;已經掛靠群組的子卡不能被直接查(要透過群組)。這個放寬順便暴露
兩個帳务計算 bug 一起修了:`compute_group_billing` 原本無條件另外查一次
「轉入 group.sync_id 的錢」當溢繳結轉,獨立信用卡場景下 group 和唯一子
帳戶是同一個 sync_id,會跟子帳戶自己的已還款金額重複計算兩次(改成
`group.sync_id in member_ids` 時跳過這次額外查詢);`card_payment_ep` 的
分攤算法在「足額付清」分支對 `allocations[account_id]`(溢繳結轉)用的是
覆蓋賦值,獨立信用卡場景下這個 key 會跟同一輪迴圈裡「該子帳戶自己的應繳
金額」的 key 衝突,覆蓋會把應繳金額直接冲掉,改成累加。web 側:
`AccountsPanel.tsx` 額度/帳單日/還款日欄位改成 `account_group` 或「沒有
掛靠的 credit_card」都會顯示,選了掛靠群組後這組欄位清空(改用群組共用
設定);`AccountDetailDialog.tsx` 的統計卡片 + 信用卡合併帳單卡片同樣放寬
到這兩種情形。④**自動扣繳到底會不會真的到期扣款**——原本
`recurring_materializer` 到期是「無條件」生成轉帳交易,完全不看來源帳戶
餘額夠不夠,使用者確認想要「不夠就跳過+通知」。落地時發現一個架構衝突:
一般週期性收支規則在建立當下就把未來最多 12 個月(`DEFAULT_WINDOW_
MONTHS`)或 200 筆(`MAX_OCCURRENCES_PER_GENERATION`)全部批次生成好(Phase
1.5 特意這樣設計,為了減少排程掃描),但檢查餘額只有在交易「實際到期日」
當下才有意義——提前好幾個月生成時,餘額根本無從得知。跟使用者確認後,
把這個矛盾侷限在 `tx_type == "transfer"` 的規則(也就是自動扣繳本身)上:
`write/recurring_rules.py::create_recurring_rule_ep` 和
`write/transactions.py` 的 inline `recurring` 分支,transfer 規則不再呼叫
`plan_initial_generation` 批次生成(`generated_until_at` 直接設成起點,不
生成任何未來 occurrence;inline 分支裡"這筆交易本身"照常立刻建立,因為
那是使用者當下的真實操作,不需要查餘額),`refill_recurring_windows` 的
查詢也排除 `tx_type == "transfer"`。新函式 `recurring_materializer.
materialize_due_transfer_rules`:到期當下才逐筆生成,生成前用
`compute_account_balance`(公式跟 `list_workspace_accounts` 算單一帳戶
餘額完全一致;2026-08-04 從 `_compute_account_balance` 改成公開名字,
因為 `credit_card_autopay.py` 也要共用同一份)查 `from_account_id` 當下
餘額,不夠就跳過(不推進
`generated_until_at`,下次重試同一筆)+ 發通知(`category="reminder"`,
`payload.kind="insufficient_funds"`,去重 key 是 `recurringRuleId +
occurrenceAt`,同一期不會重複通知)。這個函式挂在 `main.py` 的
15 分鐘 debt/card reminder loop 上(時效性要求跟提醒類似,不是
`recurring_materializer` 自己那個 24 小時長尾續窗 loop),手動觸發走既有
`POST /internal/tasks/materialize-recurring`(回傳體新增
`transfer_rules_materialized`/`transfer_rules_skipped_insufficient`)。
一般收支類規則(expense/income)完全不受影響——"餘額"這個概念對它們本來
就不適用。測試見 `tests/test_credit_card.py`(update 路徑的 account_group
校驗、workspace 帳戶加總、獨立信用卡繳款+溢繳)+
`tests/test_recurring_rules.py`(transfer 規則不預生成、到期生成、餘額
不足跳過+去重、補足餘額後重試成功、inline recurring transfer 只生成起點)。
這輪同樣是先 pytest + build 過,尚未走完整瀏覽器手測。

**§2.9 五項後續修復(2026-08-04)**:使用者實測建了一筆已逾期的信用卡帳單,
問「是否有出現提醒」——查 `notifications` 表確認**沒有**,根因是
`credit_card_reminders.send_due_card_reminders` 原本只精確比對三個時機
(`today == cycle_end`/`due_date`/`due_date-7天`),一旦錯過那個精確的一天
(比如帳單日/還款日是後補設定的,設定當下就已經逾期)就永遠不會再提醒——
補了第四種時機 `overdue`(`today > due_date` 且 `remaining_due > 0`,同一期
只發一次),補完後這個真實的逾期帳單立刻收到了通知(dev server 有
autoreload,不用重啟)。另外一次處理四個問題:①**主帳戶詳情彈窗打開是空
的**——`account_group` 自己從不擁有交易,`read/workspace.py::
list_workspace_transactions` 原本只精確比對 `account_sync_id`,現在偵測到
目標是 `account_group` 時展開成 `credit_card_billing.
resolve_billing_children` 解析出的子帳戶一起查(前端
`GlobalEntityDialogs.tsx` 也從傳 `accountName`(模糊 ilike)改傳
`accountSyncId`(精確,才能吃到這個展開邏輯));②**主帳戶卡片一律顯示
餘額/收入/支出,即使是信用卡群組**——因為 `account_group` 未來也會用在
銀行帳戶群組,不能只看 `account_type` 判斷樣式,`AccountsPanel.tsx` 的
`BankCardTile` 改成看群組自己身上有没有設 `credit_limit`/`billing_day`/
`payment_due_day`(有 = 信用卡樣式的額度/已用/可用,沒有 = 一般餘額樣式,
面向未來的銀行群組);③**帳戶詳情裡的交易點了沒反應**——`TransactionList`
組件本來就有 `onSelect` prop,`AccountDetailDialog.tsx` 只是沒接,補上
`dispatchOpenDetailTx`(跟 `TransactionsPage.tsx` 同款接法);④**自動扣繳
整個重新設計**——使用者反饋不該是借用週期性收支規則(2026-08-02 那版),
應該是掛在信用卡/主帳戶自己身上的一個開關 + 一個來源帳戶。新增
`UserAccountProjection.auto_pay_enabled`/`auto_pay_from_account_id`
(migration `0028_account_auto_pay`,自我參照同 `parent_account_id`
模式),新服務 `src/services/credit_card_autopay.py::
materialize_due_card_autopay`:到了繳款截止日(`today >= due_date`)才開始
嘗試,查來源帳戶當下餘額(`recurring_materializer.compute_account_balance`)
不夠就跳過 + 通知(去重 key `accountId+cycleEnd+kind`,同一期只通知一次,
之後靜默持續重試),夠的話用 `credit_card_billing.
compute_card_payment_allocations`(這個分攤函式是從 `card_payment_ep` 抽出來
的,兩處共用同一份,不重複維護)照實際應繳金額整筆繳清;去重也擋掉「截止
日後又有新消費導致 remaining_due 回正」被誤判成要重繳一次的情況。掛在同一
個 15 分鐘 loop(`main.py::_run_debt_reminders_once`)+ 手動觸發端點
(`POST /internal/tasks/materialize-recurring` 回傳體新增
`card_autopay_executed`/`card_autopay_skipped_insufficient`)。舊的
transfer 週期性收支規則(§2.2 通用機制,前一版被借用來做自動扣繳)完全
不變,兩者是獨立功能不是取代關係——一般排程轉帳(比如固定房租轉帳)還是
用回原本的週期性收支規則。web 側:帳戶編輯表單(`AccountsPanel.tsx`)在
額度/帳單日/還款日那個區塊下面加了開關 + 來源帳戶下拉(只在 billing-root
帳戶上顯示);`AccountDetailDialog.tsx` 原本「設定自動扣繳」按鈕(導去
週期性收支頁面預填)拿掉,改顯示目前開關狀態 + 指去帳戶列表頁編輯。測試見
`tests/test_credit_card_reminders.py::test_overdue_reminder_fires_when_due_
date_already_passed`、`tests/test_credit_card.py::
test_workspace_transactions_account_sync_id_expands_group_children`、新增
`tests/test_credit_card_autopay.py`(5 例:到期前不觸發、餘額足夠整筆繳清+
同期不重繳、餘額不足跳過+去重+補足重試成功、來源帳戶不能是 account_group、
不能是自己)。這輪同樣只跑了 pytest + build,沒有走完整瀏覽器手測。

**§2.9.5 信用卡紅利回饋(Phase 4.5,server + web UI,2026-08-05)已落地**:
新表 `read_card_reward_rule_projection`——**user-global**,PK=`(user_id,
sync_id)`,不像 debt/recurring_rule 那樣掛 `ledger_id`,因為綁定的
`account_sync_id`(信用卡帳戶)本身就是 user-global 的
`UserAccountProjection`,規則跟著走同一個 scope;登記方式跟
account/category/tag 同款,`sync_applier.py` 走 `_USER_MERGE_SPECS`/
`_USER_UPSERT_DISPATCH`/`_USER_DELETE_DISPATCH`(而不是 debt 用的
`_LEDGER_*` 三張表),`routers/write/_shared.py` 也要在
`_USER_PROJECTION_UPSERTERS`/`_USER_PROJECTION_DELETERS`(不是
`_LEDGER_PROJECTION_*`)登記一次——這是這個 entity 跟本文件其它「新增
entity」案例最大的路徑差異,之後如果再加「綁定 user-global 帳戶」的新
entity,照這個抄比照 debt 抄更準。CRUD 端點
`POST/PATCH/DELETE /ledgers/{id}/accounts/{account_id}/card-reward-rules`
(`src/routers/write/card_reward_rules.py`,owner-only,`_assert_account_
is_credit_card` 擋 `account_group`/不存在的帳戶——回饋規則綁定的是實體
卡片,不是純管理容器,跟 §2.9 `_assert_account_not_group` 同一個道理但這裡
更嚴格,連「未掛 group 也未必是 credit_card」的帳戶類型也一併擋掉)。
回饋金額**不落庫**,跟 §2.5 借還款 `remaining_amount`/§2.3 分期
`paid_periods` 同一個「不落表、讀路徑即時加總」取捨:新服務
`src/services/card_rewards.py::compute_account_card_rewards` +
`apply_caps` 兩段——前者算每條規則的 `raw_reward`(依 `interval` 切period、
`category_ids`/`min_tx_amount` 過濾、`min_spend_threshold` 門檻判斷、
`rate_type`/`rate_value`/`rounding` 逐筆取整加總),後者處理
`cap_amount`/`cap_shared_key`(相同 `cap_shared_key` 的規則先加總
`raw_reward` 再一起套上限,超過時依佔比分攤,最後一條用減法拿餘數,對齊
`credit_card_billing.compute_card_payment_allocations` 同款做法避免四捨
五入對不上)。`interval == "billing_cycle"` 時的結帳日解析
(`resolve_billing_schedule`)優先用帳戶自己的 `billing_day`,沒有的話
(掛靠群組的子卡,§2.9 群組模型下 `billing_day` 只設在群組身上)回頭查
它的 `parent_account_id`,這樣子卡也能設定「帳單週期」回饋而不必是獨立卡
或群組本身。`calc_basis == "settlement_date"` 目前行為等同
`"transaction_date"`(§2.10 延後入帳的 `deferred_posting_at` 還沒實作,
`_attribution_date` 是唯一呼叫點,之後落地只需要改這一個函式)。讀端點
`GET /ledgers/{id}/accounts/{account_id}/card-rewards`(`period_offset`
語意同 billing-summary 的 `cycle_offset`,`0` 是還在累積的當期)+
`GET .../card-reward-rules`(規則列表)都在 `src/routers/read/ledgers.py`,
`account_id` 必須是 `account_type == "credit_card"`。web:
`AccountDetailDialog.tsx` 掛了新元件 `CardRewardRulesSection.tsx`(獨立
檔案,不是塞進本來就很長的 `CreditCardBillingSection`),渲染條件是
`account_type === "credit_card"`(不管有沒有掛靠群組,獨立卡跟子卡都能設
規則),折疊面板顯示規則清單(標籤/回饋方式/上限)+ 當期回饋預覽(符合條件
消費/是否達門檻/實拿回饋金額),CRUD 表單支援分類多選(chip 按鈕,不是
下拉,因為可以複選)、`cap_shared_key` 共用上限群組;`calc_basis` 目前不在
UI 暴露(固定送 `transaction_date`,理由同上)。測試見
`tests/test_card_rewards.py`(11 例:CRUD、owner-only、mobile push merge
契約、percentage/fixed_amount 兩種 rate_type、category_ids 過濾、
min_tx_amount/min_spend_threshold 門檻、cap_amount 單規則上限、
cap_shared_key 跨規則共享上限、billing_cycle 借用群組 billing_day、
calendar_month、拒絕 account_group 目標)。手動測試清單見
`docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md`——這輪 backend
pytest 全量回歸(除既有已知 flaky 用例外全過)+ frontend `pnpm build`/
`pnpm test:unit` 都過,但**沒有走完整瀏覽器手測**:本機 Vite dev server
這次會話持續復現「整頁空白 + `useContext` 讀到 null」的渲染錯誤,用
`git stash` 驗證過在**未改動的 `main` 分支**上同樣復現(說明是本機既有
的 dev tooling 環境問題,不是這次程式碼改動引入的),但也代表這輪沒有
機會像前幾個 Phase 那樣在瀏覽器裡抓純前端邏輯 bug,務必自己手動走一遍
清單。

**§2.9.5 回饋規則歸屬改版:自動比對 → 記交易時手動勾選(2026-08-06)**:
使用者反饋初版設計不對——現實情境是一筆消費通常只適用一種回饋方案,而
`category_ids` 自動比對容易讓同一筆錢被多條規則重複算(比如某分類同時
符合兩條規則的過濾條件),且使用者比系統更清楚這筆消費實際要走哪個回饋。
改動:①`read_tx_projection` 新增 `reward_rule_sync_ids_json`(nullable
JSON array,跟 `tag_sync_ids_json` 同一模式,migration
`0032_tx_reward_rule_ids`),交易的 `WriteTransactionCreateRequest/
UpdateRequest.reward_rule_ids`(可複選)、`snapshot_mutator.py` 的
`rewardRuleIds` 欄位映射、`sync_applier.py` merge spec、
`projection.upsert_tx` 都比照 `tagIds` 現有寫法;②`services/
card_rewards.py::compute_account_card_rewards` 改成只加總
`rule.sync_id in tx.reward_rule_sync_ids_json` 的交易,`category_
sync_ids_json` 過濾整段拿掉(欄位本身留著但不再參與計算,避免多一次
migration);`min_tx_amount`(單筆門檻)/`min_spend_threshold`(當期累積
門檻)這兩個「金額」判斷維持由系統計算——這是跟使用者確認過的界線:規則
篩選(分類)交給使用者手動判斷,金額門檻交給系統算;③新 helper
`write/_shared.py::_assert_reward_rules_valid`(仿 `_assert_debt_exists`
模式,但用 `user_id` 查,因為 `card_reward_rule` 是 user-global 實體不是
ledger-scoped):驗證每個 `reward_rule_id` 都存在且歸屬這筆交易的
`account_id`,在 `_commit_create_tx_fast`(create,查
`mutate_payload.get("account_id")`)跟 `_commit_write_fast_tx`(update,
merge 後在最終狀態的 `new_item.get("accountId")` 上驗證,對齊拆帳
`_validate_tx_splits` 同一個「改帳戶不改勾選也要重新校驗歸屬」的理由)
兩處都掛;④web:`TxForm` 新增 `reward_rule_ids: string[]`,
`TransactionsPanel.tsx` 記交易表單新增「紅利回饋規則」複選 chip 區塊
(只在 `tx_type === 'expense'` 且選中帳戶是 `credit_card` 時顯示,選項來自
`TransactionsPage.tsx`/`GlobalEditDialogs.tsx` 各自按選中帳戶單獨拉的
`fetchCardRewardRules`,不 filter `enabled`——已停用規則若曾被這筆交易
勾選,仍要能顯示讓使用者取消勾選);`CardRewardRulesSection.tsx` 的規則
表單拿掉「限定分類」chip 選擇器跟對應的 `fetchWorkspaceCategories` 依賴。
**舊交易(改版前建立、沒有 `reward_rule_ids`)一律不計入任何規則的回饋,
不做批次回填**——跟使用者確認過的取捨。測試見 `tests/test_card_rewards.py`
(14 例,含新增的「未勾選規則交易不計入」「一筆交易複選多條規則各自累加」
「交易 `rewardRuleIds` mobile push merge 契約」「write 校驗拒絕不存在/掛
錯帳戶的 rule_id」四類)。這輪同樣只跑了 pytest + frontend build/test,
沒有走完整瀏覽器手測,見上面清單。

**§2.9.5.4 活動期間 / 帳單週期同步 / 交易明細彈窗 / 自動入帳(2026-08-07)
已落地**:使用者實測後回報四個缺口,逐一修復。①**活動期間 UI**:
`starts_at`/`ends_at` 後端其實早就支援(`_rule_active_in_period` 已經在
判斷),純粹是前端表單沒有欄位——`CardRewardRuleFormDialog` 補上
`<input type="date">`(UTC getter/setter,照抄 `DebtsPanel.tsx` 的
`isoToDateInput`/`dateInputToIso` 慣例),清單新增「顯示已過期規則」切換
(預設隱藏 `ends_at` 已過的規則)+「複製」按鈕(`seed` prop,日期原樣複製,
使用者自己再改,跟預設值不同是使用者明確要求的)。記交易的回饋規則勾選
chip(`TransactionsPanel.tsx`)也補上「目前生效中」過濾(`enabled` 且在
`starts_at`/`ends_at` 區間內),但已經勾選在這筆交易上的規則即使失效仍
保留顯示,讓使用者能取消勾選——跟已停用規則的既有慣例一致。
②**帳單週期同步 bug**:`AccountDetailDialog.tsx` 的回饋卡片(`Card
RewardRulesSection`)原本完全不吃上面 `CreditCardBillingSection` 的
`cycleOffset`,永遠只顯示「目前還在累積的那期」。根因是兩者對「期數
offset=0」的定義剛好差一期(`cycleOffset=0` = 最近一次已結束的週期;
`card-rewards` 的 `period_offset=0` = 目前還在累積、尚未結束的那期),
換算關係固定是 `period_offset = cycleOffset - 1`。改法:`CreditCard
BillingSection` 新增 `onCycleOffsetChange` callback prop(仿既有
`onPeriodRangeChange` 同款「內部 state 不動,額外往上通知一次」模式,
沒有改動任何既有的自動跳期/日期運算邏輯,風險最小),`AccountDetailDialog`
接住後換算成 `periodOffset` 傳給 `CardRewardRulesSection`;後端
`get_account_card_rewards` 的 `period_offset` 下界配合放寬(`ge=-60` →
`ge=-120`,只是給換算後的範圍留餘裕,不影響既有語意)。③**交易明細彈窗**:
新端點 `GET .../card-reward-rules/{rule_id}/transactions`,點規則列可以看
命中哪些交易 + 各自回饋金額 + 剩餘額度;`services/card_rewards.py` 抽出
共用的 `_qualifying_transactions`(規則+可選期間→符合條件交易,`period_
start`/`period_end` 皆可傳 `None` 表示不限期間,§2.9.5.4 payout 引擎逐筆
結算也共用這個函式)跟 `compute_tx_reward_amount`,`compute_account_card_
rewards` 改呼叫這兩個函式(純重構,14 個既有測試原樣通過)。Moze 參考截圖
裡「消費列表中間出現已達上限分界線」這種逐筆命中點,因為 `apply_caps` 是
整期比例分攤不是先到先得,沒有明確的「這一筆壓線」,所以只在彈窗頂部顯示
整組彙總的「剩餘額度」文字,不做逐筆分界線(v1 簡化,已寫進去)。

**共用上限群組改版為跨卡挑選**:討論過程中發現使用者要的「共用上限群組」
本來就是**跨卡**的(同一家銀行的正副卡共用一個回饋額度),不是同一張卡上
的多條規則——但舊版 `get_account_card_rewards` 的規則查詢只 scope 在
單一 `account_id`,`cap_shared_key` 這個欄位雖然定義上不分帳戶,實際上
從來沒有真的跨帳戶算過。新增 `services/card_rewards.py::
fetch_cap_group_rules(db, *, user_id, base_rules)`:把 `base_rules` 裡任何
一條有 `cap_shared_key` 的,跨帳戶(不限 `account_sync_id`)查出同一個
user 底下所有共用同一個 key 的規則聯集回傳,`get_account_card_rewards`/
明細端點/§2.9.5.4 payout 的 `_materialize_period_end` 三處都先呼叫這個
函式擴成完整群組再丟給 `compute_account_card_rewards` + `apply_caps`。
**這個改動連帶暴露一個既有的正確性 bug**:`compute_account_card_rewards`
原本無條件用呼叫端傳入的單一 `account` 幫**所有**規則解析帳單週期
(`_resolve_period(db, account=account, ...)`),同帳戶場景下這是對的,
但跨卡場景下第二張卡的規則會被錯誤套用第一張卡的 `billing_day`——修法是
函式內部改成依每條規則自己的 `account_sync_id` 批次查對應帳戶(`foreign_
accounts` dict,只在真的出現跨帳戶規則時才多查一次),`account` 參數保留
但只當「預設/主要」帳戶用,不再假設所有規則都屬於它。前端
`CardRewardRuleFormDialog` 拿掉原本的 `cap_shared_key` 自由輸入框,改成
用新端點 `GET /ledgers/{id}/card-reward-rules`(這個使用者名下所有信用卡
的所有規則,不帶 `account_id` 前綴)拉出清單,渲染成「卡片名稱 - 規則
名稱」的可勾選 chip;送出時前端邏輯判斷有效 key(選中規則裡已有 key 就
沿用,都沒有但有勾選就 `crypto.randomUUID()` 生一個新的,選中規則橫跨
兩個以上不同既有 key 直接前端擋掉),再依序(`retryOnConflict` chaining)
PATCH 新加入/被移出群組的規則各自的 `cap_shared_key`——這是純前端編排,
沒有新增後端批次寫入端點,理由是這個操作互動頻率低,沿用既有單規則 CRUD
即可,不需要新的抽象。

**自動入帳(§2.9.5.4 主功能)**:規則新增 `settlement_type`(`immediate_
after_tx`/`after_posting_date`/`period_end`/`manual`,預設 `manual` 保證
既有規則升級後行為不變)、`settlement_days`(逐筆結算類型專用)、`reward_
account_id`(目的帳戶,非 manual 時必填),寫入驗證在
`snapshot_mutator.py::create_card_reward_rule`/`update_card_reward_rule`
(新增 `_assert_valid_reward_account`,比照既有 `_assert_valid_auto_pay_
source` 但**不擋**「不能是自己」——`reward_account_id == 這張卡自己`
正是最常見用例,如 U Bear/Cathay 直接折抵當期帳單;`update_card_reward_
rule` 的一致性檢查要在套用完所有 partial-update 分支、拿到合併後的最終
狀態才驗證,比照 §2.4 拆帳 `_validate_tx_splits` 同一個理由)。回饋入帳
交易一律用 `tx_type="income"`——已確認 `credit_card_billing.py` 的應繳/
餘額計算本來就把 `income` 當負的消費處理,`reward_account_id` 選同一張
卡時這筆 income 會正確沖抵應繳金額,選別的錢包帳戶時 `recurring_
materializer.compute_account_balance` 也會正確算進餘額;`income` 不強制
要求分類,不需要新增分類。新服務 `src/services/card_reward_payout.py::
materialize_due_card_reward_payouts`:`immediate_after_tx`/`after_posting_
date`(逐筆結算,`after_posting_date` 目前算法等同 `immediate_after_tx`
——沿用 `calc_basis`/`_attribution_date` 同款「§2.10 `deferred_posting_
at` 落地前兩者行為一致」的誠實文檔化限制,唯一呼叫點是新函式
`card_rewards.compute_settlement_date`,放在 `card_rewards.py` 而不是
payout 模組是為了讓明細彈窗端點也能共用,避免循環 import)每筆符合資格
的交易各自在 `happened_at + settlement_days` 天後入帳一次;`period_end`
只結算「最近一次已結束的那期」(比照 `credit_card_autopay`/`credit_card_
reminders` 既有的「只看最近一期,長時間離線=錯過一次自動化」限制)。
**去重機制刻意不沿用 Notification 表**(`credit_card_autopay`/`debt_
reminders`/`credit_card_reminders` 用的「查歷史 payload 比對」去重法):
逐筆結算量級可能累積到每個使用者上百筆,沿用會讓去重查詢隨時間無界成長、
也會把使用者的通知中心灌爆——新增專用表 `card_reward_payouts`
(`models.CardRewardPayout`,唯一索引 `(user_id, rule_sync_id, dedup_
key)`,不是 sync entity),`dedup_key` 逐筆類型是交易 `sync_id`,
`period_end` 類型是 `period_end.isoformat()`。決策(已跟使用者確認):
逐筆結算**不發通知**(使用者在交易列表就看得到這筆收入),`period_end`
整批結算才發一則通知(`category="card_reward"`,新增進
`services/notifications.py::NotificationCategory`,比照
`credit_card_autopay` 的通知慣例)。掛在 `main.py` 同一個 15 分鐘
debt/card reminder loop 上(`_run_debt_reminders_once`),手動觸發沿用
`POST /internal/tasks/materialize-recurring`(回傳體新增
`card_reward_tx_payouts`/`card_reward_period_payouts`)。**已知限制**:
①逐筆結算不套用 `min_spend_threshold`(本期累積門檻)——逐筆入帳沒辦法
等到「這期結束」才知道有沒有達標,已跟使用者確認接受;②共用上限群組
如果同時包含逐筆結算跟區間結束兩種規則混用,逐筆結算當下沒辦法即時知道
另一條區間結算規則吃了多少共用額度,極端情況下總額可能略微超出共用上限
(最多超出一筆區間結算的量),v1 已知限制,寫在這裡不是留待討論項。

**過程中意外發現的既有 bug**:`snapshot_builder.py` 重建帳戶快照(web
write 引擎每次 mutate 前的基線)時,`cardRewardRules` 的 SELECT 語句沒有
包含 `settlement_type`/`settlement_days`/`reward_account_id` 三個新欄位
——導致連續兩次 PATCH(第一次成功設定這三個欄位,第二次改別的欄位)之間,
第二次讀到的基線快照裡這三個欄位是空的,合併校驗會誤判「reward_account_
id 缺失」直接擋掉合法的更新。這是本次 pytest 測試(`test_update_card_
reward_rule_settlement_fields_merged_state_validated`)直接抓到的,修法
是把三個欄位補進 `snapshot_builder.py` 的 SELECT——跟 CLAUDE.md 之前記錄
過的 `auto_pay_enabled`/`auto_pay_from_account_id` 漏 SELECT 是同一類
bug(§2.9 2026-08-02 補強一節),提醒之後每次幫既有 entity 加新欄位,除了
model/schema/write/projection 四處,**`snapshot_builder.py` 的 SELECT
也要記得同步加**,不然只有「連續兩次 write 之間」這種場景才會暴露,單次
create/update 測試測不出來。

測試見 `tests/test_card_rewards.py`(新增 12 例:結算欄位 round-trip、
寫入校驗缺天數/缺目的帳戶/目的帳戶是群組或不存在/自己當目的帳戶放行、
merge 後狀態一致性校驗、mobile push 契約、跨卡共用上限、跨卡規則列表
端點、交易明細端點)+ 新增 `tests/test_card_reward_payout.py`(11 例:
逐筆結算到期入帳+去重、after_posting_date 現況鎖定、cap_amount 逐筆
clamp+零金額去重、自我折抵帳單、區間結束整批結算+去重+通知、無帳單週期
跳過、跨卡共用上限的區間結算、manual/停用規則零副作用、規則事後過期不
追回已賺回饋、internal task 端點計數)。backend 全量 `pytest tests/ -q`
除一個既有已知、跟本次改動無關的 flaky 用例(`test_recurring_rules.py::
test_recurring_occurrence_update_overridden_skipped_by_update_from`,
本次完全沒碰 `recurring_rules` 相關代碼)外全過;frontend `pnpm build`/
`pnpm test:unit`(含 i18n 三語系 key 一致性檢查,73 例)都過。**這輪同樣
沒有走完整瀏覽器手測**,尤其是跨卡共用上限群組挑選這塊純前端互動邏輯
pytest 測不到,務必手動走一遍 `docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_
TEST_PLAN.md` 更新後的清單。

**§2.9.5.4 手動入帳補上真正的入口 + 兩個前端 bug 修復(2026-08-03 使用者
實測回報)**:使用者實測後回報四個問題。①**「手動指定」原本只是純展示**
——`settlement_type = "manual"` 從一開始就設計成「不進自動掃描範圍」,但
沒人接著問「那使用者自己想入帳的動作要從哪裡觸發」,結果整個 UI 完全沒有
任何按鈕能真正把回饋金額存進帳戶,對使用者來說等同「設定了卻沒有用」。
補上新端點 `POST .../accounts/{account_id}/card-reward-rules/{rule_id}/
manual-payout`(`src/routers/write/card_reward_rules.py`,語意同
`card_payment_ep` 的「語意化端點包一筆交易」模式,本質產生一筆
`tx_type="income"`,走一般交易寫權限不是 owner-only):`amount`/
`reward_account_id` 每次呼叫臨時指定,不要求跟規則上的 `reward_account_id`
一致(manual 規則的這個欄位原本就允許是 null,建立規則時前端
`needsRewardAccount` 邏輯也不收集它)。共用同一份 `CardRewardPayout` 台帳
(`dedup_key = f"manual:{tx_id}"`,交易 sync_id 天生唯一,不需要額外防重複
校驗——manual 沒有自動引擎的「同一期只能結算一次」天然邊界,使用者自己
決定要不要重複按)。web 入口在交易明細彈窗(`CardRewardRuleTransactionsDialog`
in `CardRewardRulesSection.tsx`)頂部,`settlement_type === 'manual'` 時
顯示「手動入帳」按鈕,展開成金額(預設帶入這期 `capped_reward`)+ 目的帳戶
兩個欄位;刻意放在 dialog 頂部、不依賴 `detail.status === 'ok'`,因為
manual 入帳是使用者自己認定「這筆錢該入帳」,不需要規則的期間計算成功。
②**交易明細彈窗把任何 fetch 失敗都顯示成「帳戶尚未設定帳單日/繳款日」**
——使用者提供的截圖裡帳戶明明已經設好帳單日(每月 5 號)/繳款日(每月
23 號),而且同一個規則的彙總卡片也正確算出回饋金額(狀態是 `"ok"`),點
進明細卻看到這則訊息,懷疑有 bug。逐行覆核 `services/card_rewards.py` 的
`resolve_billing_schedule`/`_resolve_period`/`list_rule_qualifying_
transactions`,並且直接用 FastAPI TestClient 重建使用者的確切場景(信用卡
帳戶 billing_day=5/payment_due_day=23、規則 5% 上限 200、一筆 600 元符合
條件的消費)三個端點(`card-rewards`/`.../transactions`/跨卡
`card-reward-rules` 列表)全部回傳 200 且結果正確,**沒有重現任何後端
缺陷**。回頭看前端 `CardRewardRuleTransactionsDialog` 的
`fetchCardRewardRuleTransactions(...).catch(() => setDetail(null))`——
`detail` 為 `null` 時無論真正原因是什麼(網路錯誤、伺服器 500、或任何非
「帳單週期解析失敗」的例外),UI 一律 fall through 顯示同一句「帳單日未
設定」文案,完全不反映實際發生了什麼。這是可以直接從程式碼證實的真defect
(不需要先重現後端問題),遂修成用獨立的 `fetchError` state 區分「fetch
真的失敗」(顯示中性的「載入失敗,請稍後再試」+ 重試按鈕)跟「fetch 成功
但這期 `status !== 'ok'`」(才顯示具體的 no_billing_schedule/expired 文案)。
使用者如果重試後仍然复现,下次至少能看到不會誤導方向的訊息,也能用瀏覽器
Network 分頁抓到真正的錯誤內容。③**共用上限群組顯示「目前名下沒有其他
回饋規則」**——覆核後這是正確行為不是 bug:`CardRewardRuleFormDialog` 的
`capGroupEmpty` 訊息只在 `otherRules.length === 0`(使用者名下**只有這一條**
回饋規則,沒有第二條可以勾選加入群組)時出現,§2.9.5.4 的 `fetch_cap_group_
rules`/`list_all_card_reward_rules` 兩處都沒有 ledger 過濾,不會漏掉其它
帳本/其它卡的規則。附帶把 `CardRewardRuleFormDialog` 原本 `Promise.all(
[fetchReadAccounts, fetchAllCardRewardRules])` 共用一個 `catch` 的寫法拆成
兩個独立 `.catch()`——原寫法下任一個 fetch 失敗會連帶把已經成功的
`accounts` 也清空,回饋帳戶下拉選單會看起來「什麼都選不到」,但其實只是
共用上限群組那份資料沒抓到而已,跟使用者原本懷疑的「信用卡/戶頭帳戶被
篩選掉」是兩回事(篩選條件本來就只排除 `account_group`,信用卡/戶頭帳戶
一直都在候選清單裡)。測試見 `tests/test_card_rewards.py` 新增 3 例
(`test_manual_payout_creates_income_tx_and_dedup_row`/
`test_manual_payout_self_credit_offsets_billing`/
`test_manual_payout_rejects_account_group_and_unknown_account`)。backend
`pytest tests/ -q` 除既有已知的 `test_recurring_rules.py` flaky 用例外全過;
frontend `pnpm build`/`pnpm test:unit`(73 例)都過。**這輪同樣沒有走完整
瀏覽器手測**——尤其②的根因分析只證明了前端錯誤處理確實有 defect,無法
100% 排除使用者當下環境還疊加了别的因素(例如本機 dev server 的既有 flaky
渲染問題,見上一節記錄),務必請使用者在瀏覽器裡重新走一次交易明細彈窗
確認訊息文案已經改善、且錯誤(如果還會發生)能被準確分類。

**§2.9.5 真實瀏覽器手測 + 4 個新 bug(2026-08-03 第二輪)**:之前每一輪都
只跑 `pytest`/`pnpm build`,沒有走完整瀏覽器手測——這輪用瀏覽器自動化
真的走過六大模組(規則 CRUD、記交易勾選、帳單週期與額度計算、交易明細
彈窗跨卡共用上限、四種 `settlement_type` 自動入帳、例外情況與權限),過程
中抓到 4 個之前沒發現的真實 bug,已修復並跑過 `pytest tests/`(630 passed,
1 個既有 date-sensitive flaky 用例無關)+ `pnpm build`/`pnpm test:unit`
(73 例)確認無回歸。①`CardRewardRulesSection.tsx`/`DebtsPanel.tsx` 的
`isoToDateInput` 對「沒帶時區位移的 naive datetime 字串」用 `new Date()`
解析會被當本地時間,UTC+8 使用者編輯規則活動期間/欠款到期日會少一天——
補位移標記強制當 UTC 解析。②`AccountDetailDialog.tsx` 的 `periodOffset =
billingCycleOffset - 1` 換算式假設 billing summary fetch 成功才會自動
校準,沒設定帳單日的卡 fetch 必定失敗、`billingCycleOffset` 卡在初始值,
且這種卡沒有週期選擇器 UI 讓使用者自己切——`calendar_month` 規則(文件
明訂「不受帳單日缺失影响」)因此永遠算錯期,回饋卡片一直顯示 0。③
`card_reward_payout.py::_materialize_period_end` 的 dedup key 是「這期
結束日」共用字串,零回饋也會永久記去重(跟 `immediate_after_tx` 用交易
自己 sync_id 當 dedup key 不同)——背景 loop 若在使用者補記/回溯一筆合格
交易進已結束週期**之前**先跑過一次,這期就永遠卡在 0,之後補記的交易
再也結算不到,沒有任何補救路徑;修成零回饋時不記去重,留給下次 tick
重算,直到這期自然過期。④(**這輪影響最大**)`_assert_reward_rules_valid`
(`src/routers/write/_shared.py`)校驗交易 `reward_rule_ids` 存在性時,
錯誤地用 `current_user.id`(當下操作者)查規則,而不是 `ledger.user_id`
(帳本真正擁有者,也是規則真正歸屬者)——導致共享帳本裡的 editor 完全
無法勾選 owner 建立的任何回饋規則,一律誤判 400 找不到規則,是這份文件
之前所有輪次都只用單一使用者(帳本 owner)測試、從未驗證多使用者共享
帳本場景才漏掉的 bug(對照組 `_assert_debt_exists` 是正確用 `ledger_id`
查,兩處寫法不一致)。這個是**真的註冊第二個帳號、加成 ledger_members
的 editor、直接拿它的 token 打交易寫入 API** 才抓到的,不是看程式碼推
出來的。改法:兩處呼叫都把 `user_id=current_user.id` 改成 `user_id=
ledger.user_id`。詳見 `docs/PH4_5_CARD_REWARDS_WEB_UI_MANUAL_TEST_PLAN.md`
「零、2026-08-03 第二輪」章節(含哪些項目沒走到,例如多語言文案巡查/
深淺色主題視覺檢查/editor 身份下「+ 新增規則」按鈕在真實瀏覽器 UI 裡
點下去看到的錯誤文案——後端已確認擋下,只是沒有另開一個真的 editor
登入分頁去看 UI 呈現)。**權限回傳的是 404「Ledger not found」而非字面
上的 403**(`ledger_access.get_accessible_ledger_by_external_id` 帶
`roles` 過濾不通過時故意回 None→404,理由是不洩漏帳本存在性,是這個
codebase 其它 owner-only 端點共用的既有慣例,不是 bug)。測試過程中在
本機 dev DB 建了一個 `editor-test@example.com` 測試帳號(role=editor)、
把 `cctest@example.com` 提升為 `is_admin=1` 以便呼叫 admin-only 的
materialize-recurring 端點,只影響本機 SQLite,如需清理見上述文件。

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
