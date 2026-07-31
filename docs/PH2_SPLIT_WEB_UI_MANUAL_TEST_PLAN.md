# Phase 2 拆帳（§2.4 MOZE_FEATURE_GAP_SD.md）— 测试报告 + 手动测试清单

- 测试日期：2026-07-31
- 测试范围：[MOZE_FEATURE_GAP_SD.md](./MOZE_FEATURE_GAP_SD.md) §2.4 —— 一笔
  交易拆成多个分类的明细（server + web UI）
- 本轮环境：没有跑起来的浏览器可用（本次会话未启动 `make dev-web`/
  `make dev-api`），所以 **server 端契约已用 pytest 自动化验证**，
  **web UI 的实际点击流程需要你自己在浏览器里过一遍**，清单见下方「二」。

---

## 一、已自动化验证的部分（这次会话跑过，全部通过）

### 1. Backend

```
.venv/Scripts/python.exe -m pytest tests/ -q
```

- 新增 `tests/test_tx_splits.py`：14 个用例全过，覆盖：
  - 建交易带 `splits` 落库（`has_splits=True`、父行 `category_id`/
    `category_name` 清空、`read_tx_split_projection` 明细行正确）+ 读接口
    透传
  - 校验：金额加总不等于交易 amount → 400；少于 2 笔 → 400；
    `tx_type=transfer` 带 splits → 400
  - 互斥：splits + `refund_of_id` 同时出现 → 400；对一笔已拆帳的交易发起
    整笔退款 → 400
  - PATCH 的 LWW 语义：只改其它字段不传 `splits` → 保留旧值；显式传
    `splits: []` → 清空回到单一 category；只改 amount 不改 splits → 仍按
    旧 splits 加总校验（对不上照样 400）
  - 删除拆帳交易 → `read_tx_split_projection` 明细行一并清掉
  - mobile `/sync/push` 的 merge 契约：partial update 不带 `splits` key
    时保留旧值（走 `sync_applier._MergeSpec` 而不是 web fast path）
  - `read/workspace.py::workspace_analytics` 分类排行按 split 明细展开
    分别累加，不是整笔归到（此时已是 NULL 的）父行 category
  - 分类预算用量（`GET /ledgers/{id}/budgets/usage`）把 split 明细计入
    对应分类
- 全量回归：`pytest tests/ -q` 除两个**跟本次改动无关的既有 flaky 用例**
  外全过（`test_ai_test_provider.py::test_rate_limit_after_30_requests`
  时序相关；`test_recurring_rules.py::
  test_recurring_occurrence_update_overridden_skipped_by_update_from`
  会按当前日期算出 3 or 4 个月频 occurrence，属于该测试自身对"现在"敏感，
  开工前单独确认过这两个在改动前就会间歇失败，不是这次拆帳改动引入的）
- Alembic 迁移 `0024_tx_splits` 在真实 SQLite 文件上跑过
  `upgrade head` / `downgrade -1`，双向都成功

### 2. Frontend

```
cd frontend && pnpm -C apps/web build      # tsc -b && vite build，无报错
cd frontend && pnpm -C apps/web test:unit  # vitest run src
```

- `pnpm -C apps/web build` 干净通过（无 TS 类型错误）
- 新增 `frontend/apps/web/src/txSplitForms.test.ts`：11 个用例，覆盖
  `validateTxSplits`/`buildTxSplitsPayload` 纯函数逻辑（跟 server 端
  `_validate_tx_splits` 同一套规则：至少 2 笔/每笔 >0/加总匹配/
  transfer 拒绝/浮点容差）
- 全量 `pnpm -C apps/web test:unit`：10 个文件 73 个用例全过，无回归

**没有做的**：没有起 dev server 用浏览器实际点击过 UI（跟 Phase 1.5 那轮
不一样，这次没有可用的浏览器自动化环境）。下面「二」是需要你实机走一遍
的清单。

---

## 二、需要你在浏览器里手动过一遍的清单

先 `make dev-api`（另开一个终端）+ `make dev-web`，登录任意账号，进
「交易」页（或首页任意「新建交易」入口，两处 UI 逻辑相同，见
`GlobalEditDialogs.tsx` / `TransactionsPage.tsx` 都调用同一个
`TransactionsPanel` 组件）。

### 2.1 新建拆帳交易（核心流程）

1. 点「新建交易」，类型选「支出」，金额填 `200`
2. 分类字段右上角应该有个「拆分到多个分类」链接按钮 —— 点击
3. 单一分类选择器消失，变成一个可增删行的列表，默认给了 2 个空行
4. 第一行点分类按钮，选「餐饮」，金额填 `150`
5. 第二行点分类按钮，选「交通」，金额填 `50`
6. 列表下方应该显示「已分配 200 / 总额 200」，颜色是正常色（不是红色）
7. 点「新建交易」提交 —— 应该成功，弹窗关闭
8. 回到交易列表，这笔交易的分类列应该显示「餐饮、交通」（两个分类名拼接），
   不是空白或「-」

**预期**：提交成功，列表里能看到这笔交易

### 2.2 金额不匹配时的即时提示 + 阻止提交

1. 重复 2.1 步骤 1-5，但第二行金额改填 `40`（加总变 190，跟总额 200 对不上）
2. 「已分配 190 / 总额 200」那行文字应该变成**红色**
3. 点「新建交易」—— 应该被挡下，弹出错误提示（金额加总必须等于交易金额）

**预期**：不能提交，看到清晰的错误提示

### 2.3 少于 2 笔分类

1. 开启拆分开关后，删掉一行只剩 1 行，填好分类+金额（等于总额）
2. 点提交 —— 应该被挡下，提示"至少需要 2 个分类"

### 2.4 转账交易不能拆分

1. 类型切到「转账」—— 分类字段应该整个替换成"无"的禁用输入框，
   看不到「拆分到多个分类」按钮了（如果之前开着会自动关掉）

### 2.5 编辑既有拆帳交易

1. 点开 2.1 建的那笔交易的详情，应该能看到：
   - 金额上方类型文字旁有一个灰色「拆分」徽章
   - "分类明细"这一行（不是"分类"）列出两行：餐饮 150.00 / 交通 50.00
   - 右下角「退款」按钮应该是**灰的**，鼠标悬停有提示（拆帳交易不能整笔退款）
2. 点「编辑」进入编辑表单 —— 应该自动回显为拆分模式,两行分类+金额都对
3. 改第二行金额从 50 改成 60（加总变 210,故意搞错）—— 应该拦截提交
4. 改回 50,改成把第一行分类换成「其它」—— 提交应该成功
5. 再次打开详情确认分类明细已经更新

### 2.6 从拆帳改回单一分类

1. 打开 2.1 那笔交易编辑
2. 点「取消拆分」—— 变回单一分类选择器（空的,需要重新选)
3. 选一个分类,提交
4. 详情页应该显示为普通单一分类交易（没有「拆分」徽章、「分类明细」变回
   普通「分类」一行）

### 2.7 跟週期性收支/分期付款/退款的互斥

1. 新建交易时，先开「拆分到多个分类」，再尝试开「设为週期性收支」的开关
   —— 提交应该被挡下并提示"拆分到多个分类暂不支持跟週期性收支/分期付款
   同时使用"（或者 UI 层面两个开关互斥，看当时实现细节，只要不会静默丢
   数据即可）
2. 从某笔支出的详情页点「退款」发起退款交易（预填的新建交易表单），
   这个退款专用表单里不应该出现「拆分到多个分类」的可操作按钮，或者即使
   出现，勾选后提交应该被挡下

### 2.8 统计页 / 预算页联动（如果懒得手点,已经有 pytest 覆盖,可跳过）

1. 建好 2.1 那笔拆帳交易后，去统计页看分类排行，「餐饮」「交通」两个
   分类应该分别体现这笔交易贡献的 150/50，而不是整笔 200 挂在某一个
   分类或"未分类"上
2. 如果给「餐饮」分类设了预算，用量应该包含这笔交易分给餐饮的 150

---

## 三、已知限制 / 故意不做的部分（跟文档 §2.4/§2.12.3 对齐)

- **拆帳子项目退款**：Moze 原文支持对拆帳交易的某个子分类单独退款，这次
  没做（`§2.6`/`§2.12.3` 已经记录这个 backlog，依赖拆帳先落地）。这次的
  处理是直接不允许对拆帳交易整笔退款（`_assert_refund_target_has_no_
  splits`），需要退款时用户得先撤销拆帳再退，不够优雅但不会产生脏数据。
- **CSV 导出**：`workspace/transactions.csv` 没有加拆帳明细列，导出拆帳
  交易时分类列会是空的（这点在退款那轮也是同样的已知限制，见
  `MOZE_FEATURE_GAP_SD.md` §2.6 备注）。
- **mobile 端**：本轮只做了 server + web，mobile 本地 SQLite schema 没有
  对应子表，mobile 端拉到 `splits` 字段目前会被忽略（不会崩，但看不到
  拆帳明细，也不能在 mobile 上编辑）。跟 mobile 团队排期时需要同步
  `read_tx_split_projection` 的数据形状。
- **週期性收支/分期付款生成的交易不支持拆帳**：`create_tx` 的 recurring
  内联创建路径、`installment_plans.py` 生成各期交易的路径都没有接
  `splits` 参数，即使 server 端技术上不报错，这些路径本来就不会带
  `splits` key，所以生成的交易永远是普通单一分类。
