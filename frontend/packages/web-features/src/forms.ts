import type {
  AttachmentRef,
  InstallmentInterestPeriod,
  InstallmentPlanCreatePayload,
  InstallmentPlanStatus,
  InstallmentRemainderPosition,
  InstallmentRepaymentMethod,
  RecurringAdvancedRule,
  RecurringInlineCreatePayload,
  TxSplitPayload,
} from '@beecount/api-client'

export type TxForm = {
  editingId: string | null
  editingOwnerUserId: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  happened_at: string
  note: string
  category_name: string
  category_kind: 'expense' | 'income' | 'transfer'
  account_name: string
  from_account_name: string
  to_account_name: string
  tags: string[]
  attachments: AttachmentRef[]
  /** 交易币种(v30 多币种):'' = 跟随账户/账本本位币;显式值 = 用户手选。
   *  选了币种后账户下拉按该币种过滤(币种优先联动)。 */
  currency: string
  /** 编辑模式:该笔原币种(''=本位币)。提交时币种未变 → 不发字段,金额
   *  变化由 server 按隐含汇率联动(防快照漂移);仅前端用,不进 payload。 */
  original_currency: string
  /** 不计入收支统计(income/expense 显示开关,transfer 隐藏)。 */
  exclude_from_stats: boolean
  /** 不计入预算用量(仅 expense 显示开关)。 */
  exclude_from_budget: boolean
  /** 退款(§2.6):这笔交易是对哪笔支出的退款,存目标交易 syncId。''=普通交易。 */
  refund_of_id: string
  /** 借還款追蹤(§2.5 體驗補強):这笔交易关联的欠款 syncId。''=不掛欠款,
   *  '__new__' = 用这笔交易顺便建一笔新欠款(见下面 new_debt_* 两个字段)。
   *  跟 refund_of_id 同款语意,expense/income 都可选,transfer 不适用。 */
  debt_id: string
  /** debt_id === '__new__' 时用:新欠款的对方名字(必填)/到期日(可选,
   *  纯日期字符串 yyyy-mm-dd)。direction 不在表单里存 —— 由 tx_type 在
   *  提交时推算(income=payable 我欠对方,expense=receivable 对方欠我),
   *  这笔交易本身是欠款的起点、不是还款,所以不会把它的 debt_id 设成
   *  这笔新欠款(设了的话 remaining_amount 会被这笔交易的金额立刻冲抵掉,
   *  见 read/ledgers.py::list_debts 的 repaid 累加逻辑)。 */
  new_debt_counterparty_name: string
  new_debt_due_at: string
  /** Phase 1.5(§2.12.2):建交易当下顺便設為週期性收支起点。只在新建
   *  (editingId=null)时可开启,跟 installment_enabled 互斥。 */
  recurring_enabled: boolean
  recurring_frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  recurring_interval: string
  /** 空字符串 = 不设结束日期(长期规则,只生成默认视窗)。 */
  recurring_end_at: string
  /** 简单 frequency+interval 表达不了时用进阶规则。'none' = 不用。 */
  recurring_advanced_mode: 'none' | 'weekly_days' | 'monthly_day'
  /** Python `datetime.weekday()` 惯例:Monday=0…Sunday=6。 */
  recurring_weekly_days: number[]
  recurring_monthly_day: string
  /** Phase 1.5(§2.12.1):建交易当下顺便設為分期付款起点。只在新建 expense
   *  时可开启,跟 recurring_enabled 互斥(挂在同一个 amount/happened_at 上,
   *  两者语意冲突,一次只能选一个)。 */
  installment_enabled: boolean
  installment_periods: string
  installment_repayment_method: 'equal_installment' | 'equal_principal' | 'fixed_interest'
  installment_interest_period: 'monthly' | 'daily'
  installment_interest_rate: string
  installment_round_amounts: boolean
  installment_remainder_position: 'first' | 'last'
  installment_grace_period_months: string
  /** 拆帳(§2.4):把这笔交易拆到多个分类。tx_type 只能 expense/income,跟
   *  transfer 互斥;开启时上面单选的 category_name 不使用,改看 splits。
   *  跟 recurring/installment 不同 —— 编辑既有交易时也能开关(对齐 Moze
   *  编辑既有交易能改分类的语意)。 */
  split_enabled: boolean
  splits: TxSplitFormItem[]
}

/** 拆帳(§2.4):`TxForm.splits` 单行明细的表单态(金额存字符串绑 input)。 */
export type TxSplitFormItem = {
  category_id: string
  category_name: string
  amount: string
  note: string
}

export const txSplitItemDefaults = (): TxSplitFormItem => ({
  category_id: '',
  category_name: '',
  amount: '',
  note: '',
})

export type AccountForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  account_type: string
  currency: string
  initial_balance: string
  /** 备注,所有类型可填。 */
  note: string
  /** 信用额度,仅 credit_card 类型有意义。空字符串 = 未填。 */
  credit_limit: string
  /** 账单日(1-31),仅 credit_card。空字符串 / NaN = 未填。 */
  billing_day: string
  /** 还款日(1-31),仅 credit_card。 */
  payment_due_day: string
  /** 开户行,bank_card / credit_card 元信息。 */
  bank_name: string
  /** 卡号后四位,bank_card / credit_card 元信息。 */
  card_last_four: string
  /** 账户隐藏(issue #240)。只在编辑已有账户时通过「隐藏/恢复」切换修改;
   *  新建默认 false。 */
  hidden: boolean
}

export type CategoryForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  kind: 'expense' | 'income' | 'transfer'
  level: string
  sort_order: string
  icon: string
  icon_type: string
  custom_icon_path: string
  icon_cloud_file_id: string
  icon_cloud_sha256: string
  parent_name: string
}

import { pickRandomTagColor } from './lib/tagColorPalette'

export type BudgetForm = {
  /** 编辑模式 = budget syncId,新建 = null。 */
  editingId: string | null
  /** 'total' / 'category' — 决定要不要展示 category 选择;新建后不可改类型,
   *  改类型走"删一条 + 新建一条"路径,跟 mobile 一致。 */
  type: 'total' | 'category'
  /** 选中的分类 syncId(仅 type='category' 有意义)。 */
  category_id: string
  /** 选中的分类显示名(给 UI 展示当前选中,不传给 server)。 */
  category_name: string
  /** 金额,字符串形式绑 input,提交转 number;<=0 校验失败。 */
  amount: string
  /** 起始日(1-28)。mobile 暂时隐藏 UI 默认 1,web 也跟着默认。 */
  start_day: string
  /** 周期,默认 monthly。 */
  period: 'monthly' | 'weekly' | 'yearly'
}

export type TagForm = {
  editingId: string | null
  editingOwnerUserId: string
  name: string
  color: string
}

export type RecurringRuleForm = {
  /** 编辑模式 = rule syncId,新建 = null。 */
  editingId: string | null
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  note: string
  category_id: string
  category_name: string
  account_id: string
  account_name: string
  from_account_id: string
  from_account_name: string
  to_account_id: string
  to_account_name: string
  frequency: 'daily' | 'weekly' | 'monthly' | 'yearly'
  interval: string
  /** datetime-local 输入用的本地时间字符串。 */
  next_run_at: string
  /** 空字符串 = 不设结束日期。 */
  end_at: string
  enabled: boolean
  /** Phase 1.5(§2.12.2)进阶规则,只在新建时生效(编辑既有规则不改规则本身
   *  的生成逻辑,只能改基础字段/enabled)。 */
  advanced_mode: 'none' | 'weekly_days' | 'monthly_day'
  weekly_days: number[]
  monthly_day: string
}

export type InstallmentPlanForm = {
  /** 编辑模式 = plan syncId,新建 = null。新建后期数/总额/攤還参数不可改
   *  (server 约束,要调这些走差异化端点 rebalance-from 等)。 */
  editingId: string | null
  total_amount: string
  periods: string
  first_period_at: string
  account_id: string
  account_name: string
  category_id: string
  category_name: string
  note: string
  status: InstallmentPlanStatus
  /** Phase 1.5(§2.12.1)攤還参数,只在新建时生效。 */
  repayment_method: InstallmentRepaymentMethod
  interest_period: InstallmentInterestPeriod
  interest_rate: string
  round_amounts: boolean
  remainder_position: InstallmentRemainderPosition
  grace_period_months: string
}

export type DebtForm = {
  /** 编辑模式 = debt syncId,新建 = null。`direction`/`principal_amount`
   *  建立后不可改(server 约束)。 */
  editingId: string | null
  direction: 'payable' | 'receivable'
  counterparty_name: string
  principal_amount: string
  /** datetime-local 输入用的本地时间字符串,空字符串 = 不设到期日。 */
  due_at: string
  note: string
}

export type TxTemplateForm = {
  /** 编辑模式 = template syncId,新建 = null。 */
  editingId: string | null
  name: string
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  note: string
  category_id: string
  category_name: string
  account_id: string
  account_name: string
  from_account_id: string
  from_account_name: string
  to_account_id: string
  to_account_name: string
  sort_order: string
}

export const txDefaults = (): TxForm => ({
  editingId: null,
  editingOwnerUserId: '',
  tx_type: 'expense',
  amount: '',
  happened_at: new Date().toISOString(),
  note: '',
  category_name: '',
  category_kind: 'expense',
  account_name: '',
  from_account_name: '',
  to_account_name: '',
  tags: [],
  attachments: [],
  currency: '',
  original_currency: '',
  exclude_from_stats: false,
  exclude_from_budget: false,
  refund_of_id: '',
  debt_id: '',
  new_debt_counterparty_name: '',
  new_debt_due_at: '',
  recurring_enabled: false,
  recurring_frequency: 'monthly',
  recurring_interval: '1',
  recurring_end_at: '',
  recurring_advanced_mode: 'none',
  recurring_weekly_days: [],
  recurring_monthly_day: '1',
  installment_enabled: false,
  installment_periods: '',
  installment_repayment_method: 'equal_principal',
  installment_interest_period: 'monthly',
  installment_interest_rate: '',
  installment_round_amounts: true,
  installment_remainder_position: 'last',
  installment_grace_period_months: '0',
  split_enabled: false,
  splits: [],
})

export const accountDefaults = (): AccountForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  account_type: 'cash',
  currency: 'CNY',
  initial_balance: '0',
  note: '',
  credit_limit: '',
  billing_day: '',
  payment_due_day: '',
  bank_name: '',
  card_last_four: '',
  hidden: false,
})

export const categoryDefaults = (): CategoryForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  kind: 'expense',
  level: '1',
  sort_order: '1',
  icon: '',
  icon_type: 'material',
  custom_icon_path: '',
  icon_cloud_file_id: '',
  icon_cloud_sha256: '',
  parent_name: ''
})

export const tagDefaults = (): TagForm => ({
  editingId: null,
  editingOwnerUserId: '',
  name: '',
  // 跟 app 端 TagSeedService.getRandomColor() 一致:每次新建从 20 色调色板
  // 里随机选一个,避免所有用户都默认 #F59E0B 导致全员标签同色。
  color: pickRandomTagColor()
})

export const budgetDefaults = (): BudgetForm => ({
  editingId: null,
  type: 'total',
  category_id: '',
  category_name: '',
  amount: '',
  start_day: '1',
  period: 'monthly',
})

/** next_run_at 默认明天此刻,避免用户忘填被 server 拒绝(next_run_at 必填)。 */
function tomorrowLocalDatetime(): string {
  const d = new Date(Date.now() + 24 * 60 * 60 * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export const recurringRuleDefaults = (): RecurringRuleForm => ({
  editingId: null,
  tx_type: 'expense',
  amount: '',
  note: '',
  category_id: '',
  category_name: '',
  account_id: '',
  account_name: '',
  from_account_id: '',
  from_account_name: '',
  to_account_id: '',
  to_account_name: '',
  frequency: 'monthly',
  interval: '1',
  next_run_at: tomorrowLocalDatetime(),
  end_at: '',
  enabled: true,
  advanced_mode: 'none',
  weekly_days: [],
  monthly_day: '1',
})

export const installmentPlanDefaults = (): InstallmentPlanForm => ({
  editingId: null,
  total_amount: '',
  periods: '',
  first_period_at: (() => {
    const d = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
  })(),
  account_id: '',
  account_name: '',
  category_id: '',
  category_name: '',
  note: '',
  status: 'active',
  repayment_method: 'equal_principal',
  interest_period: 'monthly',
  interest_rate: '0',
  round_amounts: true,
  remainder_position: 'last',
  grace_period_months: '0',
})

export const debtDefaults = (): DebtForm => ({
  editingId: null,
  direction: 'payable',
  counterparty_name: '',
  principal_amount: '',
  due_at: '',
  note: '',
})

export const txTemplateDefaults = (): TxTemplateForm => ({
  editingId: null,
  name: '',
  tx_type: 'expense',
  amount: '',
  note: '',
  category_id: '',
  category_name: '',
  account_id: '',
  account_name: '',
  from_account_id: '',
  from_account_name: '',
  to_account_id: '',
  to_account_name: '',
  sort_order: '0',
})

/**
 * Phase 1.5(§2.12.2):把 `TxForm` 里的週期性收支开关字段組成
 * `RecurringInlineCreatePayload`,挂在 `createTransaction` 的
 * `TxPayload.recurring` 上。`form.recurring_enabled=false` → 返回 null
 * (不带这个字段,走普通建交易流程)。
 */
export function buildRecurringInlinePayload(form: TxForm): RecurringInlineCreatePayload | null {
  if (!form.recurring_enabled) return null
  let advanced_rule_json: RecurringAdvancedRule | null = null
  if (form.recurring_advanced_mode === 'weekly_days' && form.recurring_weekly_days.length > 0) {
    advanced_rule_json = { type: 'weekly_days', days: form.recurring_weekly_days }
  } else if (form.recurring_advanced_mode === 'monthly_day') {
    const day = Math.round(Number(form.recurring_monthly_day))
    if (Number.isFinite(day) && day >= 1 && day <= 31) {
      advanced_rule_json = { type: 'monthly_day', day }
    }
  }
  const interval = Math.max(1, Math.min(365, Math.round(Number(form.recurring_interval)) || 1))
  return {
    frequency: form.recurring_frequency,
    interval,
    end_at: form.recurring_end_at.trim() ? new Date(form.recurring_end_at).toISOString() : null,
    advanced_rule_json,
  }
}

/**
 * Phase 1.5(§2.12.1):把 `TxForm` 里的分期付款开关字段組成
 * `InstallmentPlanCreatePayload`,走独立的 `createInstallmentPlan` 调用
 * (分期跟一般交易欄位差異太大,不能塞进 `TxPayload`)。`account_id`/
 * `category_id` 由调用方解析 name→id 传入(跟一般建交易的解析逻辑共用)。
 * `form.installment_enabled=false` → 返回 null。
 */
export function buildInstallmentPlanPayload(
  form: TxForm,
  resolved: { account_id: string | null; category_id: string | null },
): InstallmentPlanCreatePayload | null {
  if (!form.installment_enabled) return null
  const periods = Math.max(1, Math.min(600, Math.round(Number(form.installment_periods)) || 0))
  const graceMonths = Math.max(0, Math.round(Number(form.installment_grace_period_months)) || 0)
  return {
    total_amount: Number(form.amount || 0),
    periods,
    first_period_at: form.happened_at,
    account_id: resolved.account_id,
    category_id: resolved.category_id,
    note: form.note || null,
    repayment_method: form.installment_repayment_method,
    interest_period: form.installment_interest_period,
    interest_rate: Number(form.installment_interest_rate || 0),
    round_amounts: form.installment_round_amounts,
    remainder_position: form.installment_remainder_position,
    grace_period_months: graceMonths,
  }
}

/**
 * 拆帳(§2.4):`TxForm.splits` 里已经填了分类的行(空分类行忽略,让用户能
 * 先加一行还没选分类的占位)。UI 校验 + 组 payload 共用同一份过滤逻辑,
 * 避免两处各写一套导致行数不一致。
 */
function _filledTxSplitRows(form: TxForm): TxSplitFormItem[] {
  return form.splits.filter((row) => row.category_id.trim().length > 0)
}

/**
 * 拆帳(§2.4)表单校验,`GlobalEditDialogs.tsx`/`TransactionsPage.tsx` 两处
 * 提交前共用,回传 i18n key(null = 通过)。跟 server 端
 * `write/_shared.py::_validate_tx_splits` 同一套规则:tx_type 只能
 * expense/income、至少 2 笔、每笔金额 > 0、加总等于交易 amount。
 */
export function validateTxSplits(form: TxForm, amountNum: number): string | null {
  if (!form.split_enabled) return null
  if (form.tx_type === 'transfer') return 'transactions.error.splitTransferNotAllowed'
  const rows = _filledTxSplitRows(form)
  if (rows.length < 2) return 'transactions.error.splitNeedsTwo'
  if (rows.some((row) => !(Number(row.amount) > 0))) return 'transactions.error.splitAmountInvalid'
  const sum = rows.reduce((acc, row) => acc + (Number(row.amount) || 0), 0)
  if (Math.abs(sum - amountNum) > 0.01) return 'transactions.error.splitSumMismatch'
  return null
}

/**
 * 拆帳(§2.4):把 `TxForm.splits` 组成 `TxPayload.splits`。调用前必须先跑
 * `validateTxSplits` 确认通过(本函数不重新校验,只负责转型)。
 * `form.split_enabled=false` → 返回空数组(server 语义:显式 [] 清空既有
 * splits,回到单一 category;create 场景下等同"没有 splits")。
 */
export function buildTxSplitsPayload(form: TxForm): TxSplitPayload[] {
  if (!form.split_enabled) return []
  return _filledTxSplitRows(form).map((row) => ({
    category_id: row.category_id.trim(),
    category_name: row.category_name.trim() || null,
    amount: Number(row.amount) || 0,
    note: row.note.trim() || null,
  }))
}
