# Phase 0 / Phase 1 Web UI 手动测试报告

- 测试日期:2026-07-30
- 测试范围:[MOZE_FEATURE_GAP_SD.md](./MOZE_FEATURE_GAP_SD.md) §2.1 通知中心(Phase 0)、
  §2.2 週期性收支、§2.3 分期付款、§2.6 退款(Phase 1)的 web UI
- 测试方式:Claude Code 用 Chrome 浏览器自动化(claude-in-chrome),对着本机真实
  跑起来的 server(`python server.py`,`http://localhost:8080`)+ web dev server
  (`pnpm -C apps/web dev`,`http://localhost:5173`)做端到端点击测试,同一份
  `beecount.db`,非 mock。
- 测试账号:`recurring-manual-test@example.com`(admin),账本
  `recurring-manual-test`(`ledger_f5e5ae1291d0`)

## 一、测试环境准备

1. `.env` 从 `.env.example` 复制,追加 `REGISTRATION_ENABLED=true`
2. `alembic upgrade head` 跑到 `0022_recurring_and_installment`
3. `python server.py` 起后端,首次启动自动建了个 admin 账号
   (`owner@example.com`,密码见启动日志)
4. `scripts/manual_test_recurring.py`(本次测试新增,复用价值高,建议保留)
   跑通了一遍纯 API 链路:注册测试账号→建账本→建一条已过期的 monthly
   recurring rule→打 `POST /internal/tasks/materialize-recurring`→确认
   `recurring_transactions: 1` 且账本里真的多了一笔 88.8 的交易
5. 在此基础上用浏览器对 web UI 做手动交互测试(本报告主体)

## 二、测试结果总览

| 功能 | 结论 |
|---|---|
| §2.1 通知中心(Phase 0) | ✅ 通过 |
| §2.2 週期性收支(Phase 1) | ✅ 通过 |
| §2.3 分期付款(Phase 1) | ✅ 通过 |
| §2.6 退款(Phase 1) | ✅ 通过 |

**没有发现产品代码层面的 bug。** 测试过程中遇到的唯一异常(见「四、测试环境
异常记录」)是浏览器自动化沙箱环境本身的 HTTP 缓存问题,不是 BeeCount 代码
缺陷,已在下面单独说明并给出结论依据。

## 三、逐项测试步骤与结果

### 3.1 通知中心(header 🔔)

- 打开 `/app/overview`,header 右上角铃铛图标(`NotificationBell.tsx`)正常
  渲染,未读数量红点显示 `1`
- 点击铃铛,下拉面板正确显示:
  - 标题「通知」+「全部已讀」按钮
  - 一条通知:标题「週期性记账已自动生成」,内容
    `manual-test-recurring`,时间「XX 分鐘前」——跟 §3.3 週期性收支物化
    生成的那笔交易完全对应
- Network 面板确认轮询请求 `GET /api/v1/notifications?limit=20` 返回 200,
  打开面板时立即刷新一次(符合 `NotificationBell.tsx` 里
  "打开时立即刷新" 的实现注释)

结论:Phase 0 web UI 端到端正常,通知内容跟 recurring 物化逻辑正确联动。

### 3.2 週期性收支(`/app/recurring-rules`)

- 页面标题「週期性收支」+ 说明文字「到期後每 15 分鐘自動產生一筆交易」
- 已有一条由脚本建的规则正确显示:`（未知分類）每月 · 下次：2026/8/30`,
  备注 `manual-test-recurring`,enabled 开关为开——`next_run_at` 显示的是
  物化后推进一个月的值,证明 web 读到的是 materializer 处理后的最新状态
- 点「新增規則」→ 填金额 150 → 提交 → 新规则立即出现在列表
  (`每月 · 下次：2026/7/31`),确认 **建规则的 write 端点正常**
- 点新规则的启用开关 → 状态变成「（已停用）」灰色徽章 + 开关关闭,确认
  **PATCH(enable/disable)正常**

结论:CRUD + 物化联动全部符合预期。

### 3.3 分期付款(`/app/installment-plans`)

- 空状态文案正确:「該帳本還沒有分期付款計畫。點擊右上角「新增計畫」把一
  筆消費拆成多期。」
- 点「新增計畫」→ 總金額 1200、期數 12 → 提交 → 立即出现
  `（未知分類）已還 1 / 12 期 · 100.00 / 每期`,進度條顯示約 8%
- 切到「交易」頁確認:分期計畫建立時同事務生成的**第一期交易**
  (100.00,2026-07-30 14:16)真的落到 `read_tx_projection`,不是只停在
  UI 层假状态

结论:「建计划立即生成第一期交易」这条 Phase 1 核心契约验证通过。

### 3.4 退款(交易表单「退款對象」)

- 交易列表「建立交易」→ 類型切成「收入」→ 「退款對象」下拉才出现
  (支出/转账类型下确认不出现,符合「僅 income 類型顯示」的设计)
- 下拉候選正確列出當前已載入的兩筆支出交易(`2026/7/30 · 100`、
  `2026/7/30 · 88.8`)——验证了「候選來自當前已載入的交易列表,非全量
  搜尋」這條已知限制的实际行为
- 建了一筆收入交易(88.8,分類「退款測試收入」,退款對象選
  `2026/7/30 · 88.8`)→ 提交成功
- 打开交易詳情彈窗:金額 `+88.80` 旁邊正確顯示紅色「退款」徽章

結論:退款關聯寫入 + detail 徽章展示均正常。

（测试中途踩到一个纯粹是我自己表单填写疏漏的点:全新建的测试帐本没有
预置任何分类,而 web 端「非转账交易必须选分类」是 `TransactionsPage.tsx`
里明确的客户端校验(`transactions.error.categoryRequired`),跟 mobile
端保持一致,是既有的正确行为,不是 bug——顺手在「分類」頁建了一個收入
分類「退款測試收入」后测试才能往下走。）

## 四、测试环境异常记录(非产品 bug)

测试过程中在同一个浏览器 tab 里反复 `navigate` 到不同路由时,多次遇到
页面整体白屏 / 报 `Invalid hook call` / `Cannot read properties of null
(reading 'useContext')` 的 React 崩溃错误。排查后确认:

- 根因是这个 Chrome 自动化沙箱环境对 Vite dev server 的
  `Cache-Control: no-cache` 响应处理不彻底,导致同一个 tab 在标准
  `navigate` 后仍会复用**跨越不同 dep-optimize 版本**的缓存 JS chunk
  (表现为同一次页面加载里,不同文件引用了不同 hash 的 `chunk-XXXX.js`,
  等价于加载了两份 React,进而 `useContext` 拿到 null)
- **不是 vite.config.ts 的 alias/dedupe 配置问题**(检查过
  `resolve.alias` / `resolve.dedupe`,配置正确),也不是仓库里有第二份
  代码副本(核实过 `D:\BeeCount-Cloud` 是空目录,`node_modules/@beecount/*`
  符号链接也都指向正确的 `D:\Bookkeeping\BeeCount-Cloud\...`)
- 确认修复方法:每次 `navigate` 后紧跟一次真正的硬刷新
  (`Ctrl+Shift+R`,绕过缓存)即可稳定复现正确渲染;本报告里所有实际
  验证到的功能结果,都是在硬刷新后的干净状态下截图/取值确认的
- 真实用户直接用浏览器打开网址(非这套自动化沙箱)不会触发这个问题——
  这是自动化测试脚手架的限制,不需要为此改动任何产品代码

## 五、遗留脚本

`scripts/manual_test_recurring.py`(新增,已 git status 显示为 `??`):
对着一个真正跑起来的 server 走一遍「建规则→立即触发物化→读回验证」的
API 链路,比手工 curl 快,后续验证 recurring/installment 相关改动可以
直接重跑。跑之前需要 `.env` 开 `REGISTRATION_ENABLED=true`。
