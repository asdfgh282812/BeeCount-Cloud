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
