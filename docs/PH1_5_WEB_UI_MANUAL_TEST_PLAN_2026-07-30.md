# Phase 1.5 週期性收支／分期付款／退款 设计修正 — 测试报告 + 手动测试清单

- 测试日期:2026-07-30(初版 pytest/API 脚本)+ 2026-07-30 补测(Safari
  浏览器自动化,真实点击 web UI)
- 测试范围:[MOZE_FEATURE_GAP_SD.md](./MOZE_FEATURE_GAP_SD.md) §2.12(commit
  `32e6163` "ph1.5 fix")—— §2.12.1 分期付款攤還算法/差异化编辑、§2.12.2
  週期性收支建立当下批次生成/差异化编辑、§2.12.3 退款发起入口搬到交易
  明细
- 本轮环境:这次会话接了 Safari 浏览器自动化工具(MCP),对着真实跑起来的
  `http://localhost:5173` web UI 实际点击走完了 §2.12.1/§2.12.2/§2.12.3
  的核心流程(用既有测试账号 `phase15-manual-test-2@example.com` /
  `test-password-123`,账本 `phase15-manual-test`),**发现了 5 个需要修的
  问题**,见下方「零、本轮浏览器实测发现的问题」。之前跑过的 pytest/
  vitest/build/API 脚本结果见「一」,仍然全部有效。
- **服务已经保持运行,不会关闭**:
  - 后端 API:`http://localhost:8080`(uvicorn --reload,日志在
    `/tmp/beecount_api.log`)
  - Web:`http://localhost:5173`(vite dev,日志在 `/tmp/beecount_web.log`)

---

## 修正记录(2026-07-30 第二轮:修了下面「零」的 5 个问题 + 你在本文件
里手写补充的 3 个问题)

**5 个原始问题的修法**:

- **0.1 期数金额显示错**:`src/routers/read/ledgers.py`
  (`list_installment_plans`)和 `src/snapshot_builder.py`(`/sync/full`)
  两处都改成跟 `paid_periods`/`next_period_at` 一样,从
  `read_installment_period_projection` 即时取"当前未到期那期"(或全部
  到期后取最后一期)的 `total_amount`,不再读那个只在建计画当下算过一次、
  之后不再更新的 `period_amount` 字段。
- **0.2 退款不带分類**:重新查证后**这不是 bug**——原交易是 expense
  分類、退款交易强制 income,两边分類树本来就不通,代码里已经有明确注释
  说明为什么故意留空让你自己选(`GlobalEntityDialogs.tsx` 的
  `handleRefundTx`)。帳戶因为不分 kind,原本就有带,不用改。
- **0.3 i18n key 打错**:`GlobalEditDialogs.tsx` 三处(不是文档原先说的
  2 处,还有一处 `editTxForm.editingId` 更新分支也打错了)`notice.
  transactionCreated`/`transactionUpdated` 全部改成正确的 `notice.
  txCreated`/`txUpdated`。
- **0.4/0.5 页面说明文案过时**:`recurringRules.desc`/`installmentPlans.
  desc`(en/zh-CN/zh-TW 三个语言档全改)改成描述 Phase 1.5 实际行为(建立
  当下批次生成 + 各差异化编辑端点),不再提 Phase 1 的 15 分钟排程/只生
  第一期。

**你在文件里手写补充的 3 个问题**(0.1 条目下面那三行),逐条处理如下:

1. **「餘額歸入首期/末期」的选项应该要有**——查证后**建交易表单內建的
   分期付款区块(`TransactionsPanel.tsx`)本来就有這個選項**,完整链路
   (UI → payload → schema → 攤還算法)是通的。但**独立的 `/app/
   installment-plans` 新增计画表单(`InstallmentPlansPanel.tsx`)漏了
   这个选项**(也漏了「金額取整」开关),导致从那个入口建的计画永远只能
   用預設的「歸入末期」+ 開啟取整,选不了「歸入首期」或關掉取整。已经
   补上,补完后我实机测了「總金額 1200 / 3 期 / 尾差歸入第一期」,建立
   成功且选项正确生效。
2. **刪除分期不聯動**——查证后确实是 bug,而且不只你说的「刪除單筆」
   这一种情况,一共有三处:
   - 整個計畫的刪除(`installment_plans.py` 的 `delete_installment_plan_
     ep`)原本只刪计画本身那一行记录,不会跟着刪各期的
     `read_installment_period_projection` 記錄和已生成的交易——
     「調整利率」「部分還本」「提前結清」「終止未來分期」这几个操作
     其实早就正确地在做级联删除了,只有「刪除整個計畫」这个按钮漏做。
     已经比照那几个操作的写法补上级联删除,並且把刪除確認彈窗的文案
     从「已產生的交易不受影響」改成如实的「也會一併刪除,此操作無法
     復原」(不然文案跟实际行为对不上,更容易誤導)。实机测了建 1200/
     3 期计画后直接刪整個計畫,確認 3 筆生成的交易也一起消失了。
   - 直接在「交易」页面刪除某一筆屬於分期計畫的交易:这条快速删除路径
     完全不知道分期这回事,刪了 tx 之後,對應那一期在
     `read_installment_period_projection` 裡的紀錄會變成孤兒(`tx_sync_
     id` 指向一筆已經不存在的交易)。这类刪除现在会直接被擋下,跳出
     「這筆交易屬於一個分期付款計畫,請改用計畫自身的操作(部分還本、
     提前結清、終止未來分期或刪除整個計畫)」的提示,而不是靜默留下
     壞掉的資料——實機測過會正確跳出翻譯後的中文提示。
   - 顺手还发现一个更隐蔽的关联 bug:在「交易」页面直接编辑(不是刪除)
     一筆分期期數交易的金額/備註時,这条快路径原本会把这筆交易的
     `installmentPlanId` 反查欄位靜默清空(跟旁邊 `refundOfId`/
     `recurringRuleId` 已经修过的同类问题一样,只是分期这个漏補),已經
     一并补上。
3. **週期收支的交易日期是創建日,不是未來發生日**——**实机重新測試沒能
   重現**。我用 Safari 直接建了一条無結束時間的月頻規則,「交易」頁面
   立刻顯示的 12+ 筆記錄日期是正確逐月遞增的未來日期(2026-08 一路到
   2027-07),不是創建當下的日期;代碼從產生時間點算法、寫入
   `happened_at`、projection 存值、到前端渲染逐層讀了一遍也沒找到會
   讀錯欄位的地方。如果你之後又遇到,麻煩告訴我具體是哪一頁/哪個時間點
   看到的(比如剛建立完 vs 隔天回來看、或者是不是看的日曆頁而不是交易
   頁),我再針對性排查。

以上所有代码改动跑过 `pytest`(486 个测试全过)和 `pnpm build`(tsc + vite
都过),并且逐条在浏览器里实机复现验证过。

---

## 修正记录(2026-07-31 第三轮:你测试时发现的 3 个新问题)

1. **交易「全選刪除」(批量刪除)沒有卡控分期付款交易,重演了跟单笔删除
   一样的孤儿化问题**。根因:批量删除是完全独立的一条 endpoint
   (`POST /write/ledgers/{id}/transactions/batch/delete` →
   `src/routers/write/transactions_batch_delete.py`),直接调用
   `snapshot_mutator.delete_transaction()` 逐笔 mutate,并**没有**走单笔
   删除那条路径上的守卫(`_shared.py` 的 `_commit_write_fast_tx`)——两条
   代码路径各自独立,之前只修了单笔那条。这次在 batch delete 的 mutate
   循环之前,先扫一遍 snapshot 里每个 tx_id 是否挂着 `installmentPlanId`,
   挂着的直接记成 `failed[]`(新增 reason `installment_linked`)跳过,不
   调用 mutator;其余正常交易照常删除,不受影响。前端 toast 也从只显示
   笼统的失败计数,改成能识别这个 reason 单独提示"N 条属于分期付款计划
   未删除,请到该计划操作"(新 i18n key
   `txBatch.deleteResult.installmentLinked`,三语言都加了)。**实机验证**:
   用账本里现有的 5 笔分期付款生成交易 + 1 笔新建的普通交易做混合全选
   删除,结果精确显示"已刪除 1 條;5 條屬於分期付款計畫,未刪除",5 笔
   分期交易原样保留,1 笔普通交易被正常删除——跟单笔删除的行为完全对齐。

2. **交易日期范围筛选:不是查错欄位(不是 created_at vs happened_at 的
   问题),是"只填一边日期"时的语义容易被误解**。实机测试证实后端
   `src/routers/read/workspace.py` 的 `date_from`/`date_to` 两端都是
   正确过滤 `happened_at`(用 `happened_at >= date_from` /
   `happened_at < date_to`),前端 `TransactionsPage.tsx` 的 ISO 转换逻辑
   也没问题——直接在筛选框里把「起始日期」「结束日期」都设成同一天
   (7/30),套用后精确只显示 1 笔,过滤完全正确。但只填「起始日期」、
   「结束日期」留空时,語義是"从这天起(无上限)",会把之后一路到 11 月
   的资料都算进去——这正是你截图里看到的现象,只是根因不是查错欄位,
   而是空的结束日期被当成"不限"而不是"同一天"。这次的修法是在筛选
   对话框里加了行内提示文字:只填起始日期时提示"未設定結束日期
   —— 會顯示此日期(含)之後的所有交易,包含未來日期",只填结束日期
   时同理提示,让这个语义不再是隐性的、容易被忽略的行为(没有改变筛选
   本身的语义,因为"只设起始日期查询所有之后的记录"本身也是一个合法
   常见用法,不能悄悄改成"必须同一天")。

3. **独立分期付款页面(`/app/installment-plans`)的「金額取整」开关
   显示异常**:根因是 `InstallmentPlansPanel.tsx` 建計畫表单外层用了
   写死的 `grid grid-cols-2 gap-3`(无条件两栏),但「金額取整」开关的
   包装容器只在 `md:` 断点以上才 `md:col-span-2` 跨两栏——两者断点不
   一致,导致窄屏(`< 768px`)下开关被挤压成两栏网格里的半栏,和旁边
   「尾差歸入」欄位互相干扰,看起来像是"坏掉"。参照
   `TransactionsPanel.tsx`(交易表單內嵌的分期表单,一直是好的)的写法,
   外层网格改成 `grid gap-3 md:grid-cols-2`(窄屏单栏、`md:` 以上才两栏),
   跟内部 `md:col-span-2` 的断点对齐。**实机验证**:390px(手机宽度)和
   700px(`md` 断点以下的中等宽度)下开关都完整占满一行、不再挤压;
   点击开关本身的 checked 状态切换也正常(`true`→点击→`false`),排除了
   还有额外的状态绑定问题。

以上 3 处改动同样跑过 `pytest`(486 个测试全过)和 `pnpm build`(tsc +
vite 都过),并且逐条在浏览器里实机复现验证过。

## 修正记录(2026-07-31 第四轮:第 3 项复测仍跑版,真正根因另有其人)

上面第 3 项(独立分期付款页面「金額取整」开关)改完 grid 断点后,你复测
截图显示开关的白色圆钮仍然溢出到卡片圆角边框外面——说明 grid 断点不一致
只是一个巧合存在的次要问题,不是这次跑版的真正根因(而且这个对话框
`max-w-md`≈448px,本来就一直小于 `md` 断点 768px,所以「grid 断点不一致」
这条根本不会在实际渲染宽度下触发,等于白修)。

**真正根因**:开关的白色圆钮(`<span>`)用的是 `position: absolute` +
`top-0.5`,但**没有显式设置 `left`**。CSS 规范里,`position: absolute`
元素若只设 `top` 不设 `left`,水平位置会退回浏览器按"假设该元素仍在正常
文档流中会出现的位置"去猜(hypothetical static position),这个值在这个
按钮的渲染环境下算出来是 `left: 22px` 而不是预期的 `0`——再叠加
`translate-x-5`(+20px)的位移,圆钮最终 `left` 变成 `42px`,而外层
`track` 只有 `44px` 宽,圆钮的 `20px` 宽度让它一路溢出到 `62px`,超出
track 右边界 `18px`,自然也顶穿了外层卡片的圆角边框。用浏览器
`getBoundingClientRect()` 实测量出来的数字跟这个推算完全吻合(track
`left:778 width:44` vs knob `left:820 width:20`,knob 右边到
`840`,超出 track 右边`822`足足 18px)。

真正奇怪的是全仓库只有 `InstallmentPlansPanel.tsx` 这一处开关用了这种
"absolute 定位但不设 left"的写法(`grep` 全 repo 只命中这一个文件);
交易表单内嵌的分期表单(`TransactionsPanel.tsx`,一直显示正常)用的是
完全不同、更稳的写法——track 用 `inline-flex items-center`,knob 用
`inline-block` + `transform`(不用 absolute 定位),水平位置完全交给
flex 布局本身决定,不依赖浏览器对"假设位置"的猜测,天然不会有这个坑。

**修法**:把 `InstallmentPlansPanel.tsx` 里这一处开关的 track/knob 换成
跟 `TransactionsPanel.tsx` 完全一样的写法(`relative inline-flex h-5 w-9
items-center` + `inline-block h-4 w-4 transform translate-x-[18px]`),
不再用 `absolute`/`top-0.5` 这条路。**实机验证**:用
`getBoundingClientRect()` 量过 knob 现在完全落在 track 内(track 右边
`822` vs knob 右边 `820`);390px 宽度下截图确认开关完整贴合卡片圆角,
不再溢出;点击开关的 `checked` 状态切换(`true`→`false`)正常。

`pnpm build`(tsc + vite)通过。这处改动只碰了一个按钮的 className,不
涉及后端/schema,没有重跑 pytest。

---

## 修正记录(2026-07-31 第五轮:「金額取整」開了,建立後金額還是有小數點)

**你的反馈**:分期計畫建立對話框裡把「金額取整」打開了,但建立之後,每期
的攤還明細(尤其是利息/合計)還是有小數點(如 7816.32、7772.27)。

**根因**:`round_amounts` 这个参数从一开始设计/实作时就只把每期金额取整
到「分」(2 位小数),不是取整到「元」(整数)——见
`src/services/installment_amortization.py` 的 `_round2`/`_apply_rounding`。
UI 上的字面意思「金額取整」在中文语境下明确是指「取到整数金额,不带小
数」,跟代码原本的语义(取到分)对不上,属于语义级的 bug,不是 UI 显示
问题。

**修法**:把取整粒度从「分」改成「元」——
`src/services/installment_amortization.py`:`_round2`(`round(x, 2)`)改名
`_round_unit`(`float(round(x))`),`_apply_rounding` 里本金/利息都取整到
整数,尾差(`diff`)也按整数计算再塞进 `remainder_position` 指定的那一期。
`round_amounts=False` 的路径完全不受影响(不取整,保留原始浮点精度)。

**测试**:更新了 `tests/test_installment_amortization.py` 里所有取整相关
的锁定值(从两位小数改成整数),`tests/test_installment_plans.py` 的
`test_installment_period_patch_marks_overridden_and_skipped_by_rebalance`
有一处断言(`interest_amount` 前后不相等)在改成整数取整粒度后,两个
raw 利息值凑巧四舍五入到同一个整数(小额场景下 6.63 vs 7.17 都会四舍五
入成 7),属于取整粒度变粗后必然出现的巧合碰撞,不是逻辑 bug——已改成
比较 `total_amount`(变动幅度大,不会碰撞)。`pytest`(486 个测试全过,
含 `ruff`/`mypy`)。这处改动只在后端(计算逻辑),前端不用改。

---

## 零、本轮浏览器实测(Safari MCP)发现的问题

以下是这次用浏览器自动化实际点过 web UI 后发现、需要修的问题,按严重
程度排列。**核心功能链路本身是正确的**(见后面「已验证正确的行为」),
这些都是周边的显示/文案/翻译缺陷。

### 0.1(中)分期计画「每期」摘要金額算错 —— 用的是天真平均值,不是真实攤還金額，且有小數

- 位置:`src/snapshot_mutator.py:1133`(`period_amount = round(total_amount
  / periods, 2)`)→ `src/routers/read/ledgers.py:765`(`period_amount=
  float(row.period_amount or 0)`,直接读这个从没更新过的字段)→ 前端
  `frontend/packages/web-features/src/features/InstallmentPlansPanel.tsx:499`
  渲染 `plan.period_amount`。
- 实测:建一笔 1200 元 / 6 期 / 等額本息 / 年利率 0.12 的分期计画,清单
  摘要显示「200.00 / 每期」(=1200/6 天真平均),但展开「查看期數」看到
  真实每期是 207.06(攤還后的等額本息金額)。rebalance-from 调整利率后
  这个摘要值完全不会跟着变(还是停在建计画当下算的 200.00),更加脱离
  实际。
- 同一个 read 端点里 `paid_periods`/`next_period_at` 已经改成从
  `read_installment_period_projection` 即时算(`read/ledgers.py:737-739`
  的注释也写了「不再信任 projection 里那两个不被排程更新的历史相容字段」),
  但 `period_amount` 漏改,没有套用同样的修法。
- 建议修法:`list_installment_plans` 里比照 `paid_periods` 的做法,从
  `due_dates_by_plan`/period 明细里取第一笔(或当前未还清那笔)的
  `total_amount` 现算,不要读 `row.period_amount`。
- 此系統應該會有一個選項是分期餘額納入，由於金額不可能整除，台幣沒有小數，所以不能整除的，使用者應該可以選擇放首期還是末期
- 分期付款建立的時候雖然在右邊的獨立分期付款頁面會連動，可是當我刪除其中一個（不管是獨立的分期付款頁面，還是在交易頁面的所有分期 付款資料），都不會聯動，這點也請測試一下
- 週期的記帳他的發生日期是在未來，但在「交易」這個頁籤下的日期似乎是交易創建日，請改變，這會影響所有有關未來記帳的事誼

### 0.2(中)退款快速建单不会带入原交易的「分類」

- 位置:`frontend/apps/web/src/components/GlobalEditDialogs.tsx` 里
  refund 预填那段(`refundOf` 分支),只塞了 `amount`/`note`/
  `category_name`/`account_name` 展示字段,但没有把 `category_id`/
  `account_id` 一起设进表单状态,导致分類下拉維持在「分類名稱」占位符,
  没有真正选中原交易的分類。
- 实测:對一筆分類「分期测试分类」的支出交易点「退款」,打开的建交易
  表單金額 `100`、備註「午餐100」正確帶入,但分類下拉還是空的占位符
  「分類名稱」,提交时若原账本这个交易类型(收入)下没有既有分類会直接
  跳「請選擇分類」错误挡住提交 —— 使用者要重新手動选一次分類,不符合
  MOZE_FEATURE_GAP_SD.md §2.12.3 「自動帶入原支出的金額/備註等資料」的
  设计(分類/帳戶应该也算「等資料」)。帳戶那次因为原交易本身没挂帳戶
  没能验证,建议实机测一筆有帳戶的支出交易的退款,确认帳戶是否也有同样
  漏带的问题(代码读起来是同一段逻辑,大概率也漏了)。

### 0.3(低,2 处)`GlobalEditDialogs.tsx` 用错 i18n key,退款/透過此彈窗建交易時 toast 显示原始 key 而非翻译文字

- 位置:`frontend/apps/web/src/components/GlobalEditDialogs.tsx:361` 和
  `:371`,分别在「透過此彈窗建立分期計畫」「透過此彈窗建立一般交易/退款
  交易」成功后调用 `notifySuccess(t('notice.transactionCreated'))`。
  但三个语言档(`en.ts`/`zh-CN.ts`/`zh-TW.ts`)里实际的 key 是
  `notice.txCreated`(`zh-TW.ts:1115` = "交易已建立"),`notice.
  transactionCreated` 根本不存在,`t()` 找不到 key 时原样吐回 key
  字串本身。
- 实测:两次退款提交后,成功 toast 显示的是字面文字
  `notice.transactionCreated`,不是「交易已建立」。同一页面透过「建立
  交易」按钮走的是另一个组件(`TransactionsPage`/`TransactionsPanel`),
  用的是正确的 `notice.txCreated`,所以只有走 `GlobalEditDialogs`(退款
  入口、以及可能其它跨页快速编辑入口共用这个弹窗)这条路径会看到这个
  问题。
- 建议修法:把这两行的 key 改成 `notice.txCreated`(create 分支)——
  `installmentPayload` 分支(:361)也读 `notice.txCreated` 即可,不需要
  额外新增 key。

### 0.4(低)`/app/recurring-rules` 页面说明文案是 Phase 1 的旧行为描述

- 位置:大概率在 `frontend/apps/web/src/pages/sections/
  RecurringRulesPage.tsx` 或对应 i18n 字串(文案是「本帳本的週期性收支
  規則。到期後每 15 分鐘自動產生一筆…」)。
- 实测:Phase 1.5 改成建立当下就批次生成未来一整个视窗的交易(本次也
  实测确认:建一条无 `end_at` 的月频规则,提交后立刻在交易列表看到
  12 笔一路排到 2027-07 的未来交易,完全不需要等 15 分钟排程),但这个
  页面的说明文字还停留在 Phase 1 「到期後每 15 分鐘自動產生一筆」的
  旧描述,会让使用者以为还是逐期排程生成,跟实际体验不一致。
- 建议修法:改成类似「建立規則時會立即批次生成未來一段視窗內的交易
  (視是否設定結束日期而定);之後可用『連同以後』或『終止未來週期』
  調整」。

### 0.5(低)`/app/installment-plans` 页面说明文案同样是 Phase 1 的旧行为描述

- 实测:页面顶部说明是「本帳本的分期付款計畫。建立計畫會立即產生第一期
  交易,剩餘…」(后半段被截断,推测是「剩餘各期由排程逐期產生」一类的
  旧描述)。Phase 1.5 已经改成建计画当下用攤還算法一次算出并生成全部
  期数(本次实测:建 1200/6 期/等額本息计画后,查看期數立刻看到全部
  6 期本金/利息明细,交易列表里 6 笔交易也都已经真实存在),不再是
  「只生第一期,其余排程推进」。文案需要跟着改。

**以上 5 项里,0.1/0.2/0.3 属于会让使用者拿到错误信息或多做一次操作的
功能性小 bug,建议排进这轮修;0.4/0.5 纯文案,风险最低但也最容易让人
误解新行为,建议顺手一起改。核心的批次生成/攤還演算法/差异化编辑/
退款反查这些主链路本身经过实测都是对的,不受这 5 项影响。**

### 已验证正确的行为(这次浏览器实测逐条走过,不需要你再重复点)

- §2.12.2:建交易勾「設為週期性收支」、不填結束時間 → 提交后**立刻**
  批次生成未来约 12 个月的交易(本例月频生成到 2027-07),不等排程
- `/app/recurring-rules` 展开「查看已生成交易」→ 单独编辑某一期金额 →
  该期标记「已單獨編輯」,金额只改这一笔
- 「連同以後」(update-from)→ 只更新该期起**未被单独编辑过**的后续期,
  已标记「已單獨編輯」的那期不受影响(实测：09-30 那期维持 999,
  08/10/11/12-30 全部套用新值 150,07-30 维持原值 88.8)
- 「終止未來週期」→ 删除所有未发生的未来交易,只保留已发生那笔,规则
  显示「已停用」;installment 的「終止未來分期」也验证了同样行为(状态
  变「已終止」,操作按钮消失)
- 建交易勾「設為分期付款」,期數 6、攤還方式「等額本息」、年利率
  `0.12`(注意是小数不是百分比整数,详见下面「⚠️」那条)→ 提交后立刻
  生成全部 6 期交易,每期约 207.06,查看期數的本金/利息拆分正确(本金
  递增、利息递减,加总精确等于 1200.00,尾差正确歸入最後一期)
- 「調整利率(連同未來)」(rebalance-from)从第 3 期开始改利率 →
  只有第 3 期起的金额重算,第 1/2 期不变
- 「部分還本」(early-repay-principal)還本 300 → 之后各期金额相应下调,
  剩余本金加总跟预期一致
- 「提前結清」(payoff)→ 计画状态变「已結清」,操作按钮消失
- 交易明细「退款」按钮只在支出类型交易上出现,收入(含退款本身)/转账
  交易上不显示
- 對同一筆支出建立兩次退款(40 + 30)→ 原交易明细正确顯示「已退款金額
  70.00」,退款清單兩筆都列出來(驗證「同一筆支出允許多筆退款」沒有被
  寫死成一對一)

---

## 一、我这边已自动验证的部分(全部通过,无需你重跑)

| 项目 | 命令 | 结果 |
|---|---|---|
| 后端单元/集成测试 | `.venv/bin/python -m pytest tests/ -q` | ✅ 486 个测试全过(含新增的 `test_installment_amortization.py`/`test_recurring_schedule.py`,以及重写过的 `test_installment_plans.py`/`test_recurring_rules.py`) |
| alembic 迁移 | `alembic upgrade head` | ✅ 干净跑到 `0023_installment_amortization_and_recurring_windows` |
| 前端单元测试 | `pnpm -C apps/web test` | ✅ 9 个测试文件、62 个 case 全过(含 `recurringInstallmentForms.test.ts`) |
| 前端生产构建 | `pnpm -C apps/web build` | ✅ `tsc -b` 类型检查 + `vite build` 都成功,新页面 `InstallmentPlansPage`/`RecurringRulesPage` 产物正常生成 |
| **对真实 server 的 API 端到端脚本**(新增,见下) | `python scripts/manual_test_phase1_5.py` | ✅ 全部检查通过 |

### 新增脚本:`scripts/manual_test_phase1_5.py`

跟仓库里已有的 `scripts/manual_test_recurring.py` 同一模式,对着**真正跑
起来的 server + 真正的 `beecount.db`**(不是 pytest 的临时库)走一遍完整
HTTP 链路,验证的核心契约点:

1. **建交易时带 `recurring` 参数**(带 `end_at`)→ 规则建立当下就**一次
   批次生成窗口内全部未来交易**(本例月频 + 95 天窗口,验证生成了 4 笔,
   不是等 15 分钟排程逐期出现)
2. `POST .../recurring-rules/{id}/terminate-future` → 正确只保留已发生
   的那笔、删除未发生的未来期,规则标记停用
3. **建分期计画时当场用攤還算法算出全部期数**(6 期等额本息)并生成对应
   交易,不再依赖排程逐期推进
4. `POST .../installment-plans/{id}/rebalance-from/{period_no}` → 调
   利率后,该期起的金额正确重算(且不影响 rebalance 起点之前的已发生期)
5. `POST .../early-repay-principal`、`POST .../payoff`(结清后
   `status=settled`)、`POST .../terminate-future`(终止后
   `status=terminated`)三个差异化编辑端点都返回成功且状态符合预期
6. 退款:建一笔支出 + 一笔带 `refund_of_id` 的退款收入 → 原支出交易读
   回时 `refunds` 反查字段正确带出退款金额/时间(§2.12.3 的反向查询)

这个脚本已经用一次性的测试账号 `phase15-manual-test-2@example.com`(密码
`test-password-123`)在你本机真实数据库里留了一个账本
`ledger_e678e27a67e9`(名称 `phase15-manual-test`),里面已经有跑通的
週期规则/3 个分期计画(rebalance/payoff/terminate 各一个)/退款交易 ——
**如果你想直接肉眼看已经跑通的数据长什么样,可以先用这个账号登录看一眼,
不想要就直接忽略或事后删掉这个账本即可**。之后每次改这块代码,重跑这个
脚本(可以加 `--email` 换新账号避免累积)就能几秒钟内验证核心链路没退化。

### ⚠️ 需要你在手动测试时特别留意的一点(不是 pytest/脚本能测出来的 UX 细节)

`installmentPlans.field.interestRate`(「年利率」输入框,在建交易表单的
「分期付款」区块跟独立的 `/app/installment-plans` 新增计画表单里都有)
**后端 `interest_rate` 语义是小数(0.12 = 12%/年),不是百分比整数**
(`src/services/installment_amortization.py` 顶部注释写明)。前端两处输入
框都只是纯数字框(`step="0.001"`,没有 `%` 后缀或 placeholder 提示),
我在写自动化脚本时手滑填了 `12`(以为是「12%」)结果生成的每期金额比
预期大了近 10 倍(年利率被当成 1200%)。这不是代码 bug——`step="0.001"`
其实就是暗示「这里要填小数」——但从 UX 角度很容易让真实用户填错。
**手动测试时麻烦你实际在这个输入框填一下 `0.12`(代表年利率 12%),确认
生成的每期利息金额换算回年化后确实接近 12%**,如果你也觉得容易填错,
可以之后单独提一个"输入框加 % 后缀,内部除以 100 再送 API"的小改动,
这次不在 Phase 1.5 范围内顺手改,只留个记录。

---

## 二、还需要你自己动手验证的清单(web UI,`http://localhost:5173`)

登录账号任选:`owner@example.com` / `123456`(admin,已用 `make
seed-demo` 建好,目前没有账本,登录后自己建一个新账本 + 至少一个账户 +
一个分类再开始测,这是仓库既有的"新账本无预置数据"设计,不是这次改动
引入的);或者用上面脚本建好、已经有数据的
`phase15-manual-test-2@example.com` / `test-password-123`(账本
`phase15-manual-test`)省去建账本的步骤,这次浏览器实测也是用的这个
账号,里面已经留了本轮测试产生的规则/计画/退款数据可以直接肉眼看。

**下面这份清单已经精简过**:凡是本轮 Safari 浏览器自动化已经实测走过
一遍、确认正确的项目(建交易当下批次生成、单独编辑/連同以後/終止未來
週期、等額本息攤還算法与尾差歸位、rebalance-from/早還/payoff/
terminate-future、退款按鈕僅支出顯示/多筆退款反查累加)都**不在**这份
清单里了,完整证据见上面「零、本轮浏览器实测发现的问题」末尾的「已验证
正确的行为」。这里只列**没能自动化验证、需要你亲自确认**的项目。

**下面向目我希望你也可以一起測試**

### 2.1 週期性收支(§2.12.2)—— 还需要你确认的部分

- [ ] 建一条帶 `end_at`(比如设成 3 个月后)的规则,确认只生成到
      `end_at` 为止,不多不少(本轮只测了「不填結束時間」的长期视窗
      分支,`end_at` 精确边界这条没测)
- [ ] 试一下「進階規則」欄位(建交易表单里週期性收支区块的「進階規則」
      输入,本轮没有填过这个,不确定它目前的实际效果/UI 呈现是否完整)

### 2.2 分期付款(§2.12.1)—— 还需要你确认的部分

- [ ] 分别试一下「等额本金」「固定利息」两种攤還方式(本轮只深入验证了
      「等額本息」,`ui-test-payoff`/`terminate` 两笔测试计画用的是等額
      本金但没有展开核对每期本金/利息数字),确认每期金额的变化趋势
      符合直觉(等额本金:本金固定、利息递减、总额递减;固定利息:每期
      利息固定)
- [ ] 试一下「餘額歸入首期」选项(本轮默认用的是「歸入末期」,没测
      「首期」分支),确认尾差确实歸到第一期而不是凭空消失
- [ ] 关掉「金額取整」开关试一次,确认允许出现非整数分的金额

### 2.3 退款(§2.12.3)—— 还需要你确认的部分

- [ ] **对一笔挂了账户的支出交易**发起退款,确认「账户」是否有跟
      「分類」一样的漏带问题(本轮测试用的原交易没有挂账户,没能验证
      账户这个字段;分類已确认漏带,见「零、0.2」)
- [ ] 拆帳交易(如果 §2.4 拆帳已经存在测试数据)对子項目退款的行为——
      本轮账本没有拆帳数据,没测

### 2.4 权限(共享账本场景,如果你有多用户可以测)

- [ ] 用非 owner 的账本成员登录,确认「週期性收支」「分期付款」的新增/
      编辑/差异化端点(rebalance/payoff/terminate-future/update-from
      等)全部被 disabled 或调用后 403——现有代码用
      `_OWNER_ONLY_ROLES` 限制这些写入,应该维持跟 Phase 1 一致的权限
      (本轮只有单一账号,没有第二个成员账号可测)

---

## 三、如果发现问题怎么反馈

上面任何一项如果跟预期不符,记下具体的:哪个页面 / 点了什么按钮 / 期望
结果 vs 实际结果(最好带一下 Network 面板里失败请求的 response body,
后端报错信息会包含具体原因),回来告诉我,我再对着 `src/routers/write/
recurring_rules.py` / `installment_plans.py` / 对应的 web-features 面板
去定位。
