import type { AttachmentRef } from '@beecount/api-client'

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
}

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
}

export type InstallmentPlanForm = {
  /** 编辑模式 = plan syncId,新建 = null。新建后期数/总额不可改(server 约束)。 */
  editingId: string | null
  total_amount: string
  periods: string
  first_period_at: string
  account_id: string
  account_name: string
  category_id: string
  category_name: string
  note: string
  status: 'active' | 'settled'
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
  refund_of_id: ''
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
})
