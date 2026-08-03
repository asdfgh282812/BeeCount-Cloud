import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type {
  AccountBillingSummary,
  AccountInterestFreeSuggestion,
  InstallmentInterestPeriod,
  InstallmentRepaymentMethod,
  ReadAccount,
  WorkspaceAccount,
  WorkspaceCategory,
  WorkspaceTag,
  WorkspaceTransaction
} from '@beecount/api-client'
import {
  cardPayment,
  createCategory,
  createInstallmentPlan,
  fetchAccountBillingSummary,
  fetchAccountInterestFreeSuggestion,
  fetchReadAccounts,
  fetchWorkspaceCategories
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast
} from '@beecount/ui'
import { TransactionList } from '@beecount/web-features'
import {
  Banknote,
  Calendar as CalendarIcon,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CreditCard
} from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { localizeError } from '../../i18n/errors'
import type { DetailScope } from '../../lib/txDialogEvents'
import { dispatchOpenDetailTx, dispatchOpenEditAccount } from '../../lib/txDialogEvents'
import { CardRewardRulesSection } from './CardRewardRulesSection'
import { DetailScopeToggle } from './DetailScopeToggle'

type AccountWithStats = ReadAccount & {
  tx_count?: number | null
  income_total?: number | null
  expense_total?: number | null
  balance?: number | null
}

interface Props {
  account: AccountWithStats | null
  scope: DetailScope
  onScopeChange: (next: DetailScope) => void
  transactions: WorkspaceTransaction[]
  total: number
  offset: number
  loading: boolean
  tags: WorkspaceTag[]
  onClose: () => void
  onLoadMore: (accountId: string, offset: number) => void
  onPreviewAttachment?: (ctx: unknown) => void
  resolveAttachmentPreviewUrl?: (att: unknown) => string | null
  /** 信用卡帳戶瀏覽帳單週期時,下面交易列表跟著过滤到该週期日期区间;非
   *  信用卡帳戶 / 週期未定時传 null(见 CreditCardBillingSection 用法)。 */
  onPeriodRangeChange?: (range: { start: string; end: string } | null) => void
}

/** 点账户卡片弹出的详情:顶部账户名 + 当前余额/累计收入/累计支出 + 交易列表(无限滚动加载)。 */
export function AccountDetailDialog({
  account,
  scope,
  onScopeChange,
  transactions,
  total,
  offset,
  loading,
  tags,
  onClose,
  onLoadMore,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  onPeriodRangeChange,
}: Props) {
  const t = useT()
  const { profileMe } = useAuth()
  const noteDisplayMode = profileMe?.appearance?.note_display_mode ?? 'category'
  // §2.9.5.4 使用者反饋:紅利回饋卡片沒有跟著上面的帳單週期選擇器走。
  // `CreditCardBillingSection` 的 `cycleOffset`(0=最近一次已結束的週期)
  // 透過 onCycleOffsetChange 回報到這一層,再換算成 card-rewards 的
  // `period_offset` 語意(0=目前還在累積、尚未結束的那期)傳給
  // `CardRewardRulesSection`——兩者對「0」的定義差一期,見
  // `read/ledgers.py::get_account_card_rewards` docstring。
  const [billingCycleOffset, setBillingCycleOffset] = useState(0)
  useEffect(() => {
    setBillingCycleOffset(0)
  }, [account?.id])
  return (
    <Dialog open={Boolean(account)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-border/60 px-6 py-4">
          <DialogTitle className="truncate">{account?.name || ''}</DialogTitle>
          <DetailScopeToggle value={scope} onChange={onScopeChange} className="shrink-0" />
        </DialogHeader>
        {account ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 统计:优先 server 返回的 balance/income/expense,缺失时兜底 initial_balance.
                注意:这里的统计来自 props 上的 account 实体,本身是按上层 scope
                取的(GlobalEntityDialogs / AccountsPage 通过 fetchWorkspaceAccounts
                的 ledgerId 参数控制)。弹窗顶部 scope 切换只影响交易列表过滤,
                上面这块 KPI 沿用打开时的快照,不跟随 scope 实时切换 —
                跟 mobile 端 account_detail_page 行为一致。 */}
            <AccountStatsHeader account={account} t={t} />

            {/* 信用卡 / 银行卡专属信息:bank_name / 卡号末 4 / 信用额度 /
                账单日 / 还款日 + 倒计时。普通账户类型不渲染。 */}
            <AccountCardInfo account={account} t={t} />

            {/* 信用卡合併帳單 + 免息期建议 + 繳款(§2.9 Phase 4,2026-08-02
                改版為群組模型,同日第二輪放寬到單卡)。只在
                account_type=account_group(主帳戶),或沒有掛靠任何群組的
                獨立信用卡,且当前有选中账本时渲染(帐单摘要是
                ledger-scoped 读端点)。 */}
            <CreditCardBillingSection
              account={account}
              t={t}
              onClose={onClose}
              onPeriodRangeChange={onPeriodRangeChange}
              onCycleOffsetChange={setBillingCycleOffset}
            />

            {/* 信用卡紅利回饋(§2.9.5 Phase 4.5):規則管理 + 當期回饋預覽。
                綁定的是這張真實的 credit_card 帳戶(獨立卡或掛靠群組的子卡
                都可以),不是 account_group 本身。periodOffset 跟著上面的
                帳單週期選擇器走(§2.9.5.4)。 */}
            <CardRewardRulesSection
              accountId={account.id}
              accountType={account.account_type}
              periodOffset={billingCycleOffset - 1}
            />

            <div className="min-h-0 flex-1 overflow-y-auto">
              <TransactionList
                items={transactions}
                tags={tags}
                variant="compact"
                loading={loading}
                hasMore={transactions.length < total}
                onSelect={(row) => dispatchOpenDetailTx(row as WorkspaceTransaction)}
                onLoadMore={() => {
                  if (!loading) onLoadMore(account.id, offset)
                }}
                onPreviewAttachment={onPreviewAttachment as never}
                resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl as never}
                emptyTitle={t('transactions.empty.forAccount.title')}
                showLedger={scope === 'all'}
                noteDisplayMode={noteDisplayMode}
              />
            </div>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function AccountStatsHeader({
  account,
  t,
}: {
  account: AccountWithStats
  t: (key: string) => string
}) {
  const hasServerStats = typeof account.balance === 'number'
  const balance = hasServerStats ? account.balance! : account.initial_balance ?? 0
  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  // 信用卡按负债展示:只显当前欠款(= -balance,余额为负=欠款),不显示
  // 累计收入/支出(信用卡入账是还款/退款,非收入);额度/可用/账单在下方
  // AccountCardInfo。对齐 mobile account_detail_page。
  if ((account.account_type || '') === 'credit_card') {
    const owed = Math.max(0, -balance)
    return (
      <div className="border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('accounts.bankcard.currentOwed')}
        </div>
        <div className="mt-0.5 font-mono text-lg font-bold tabular-nums text-expense">
          {fmt(owed)}
        </div>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-3 gap-3 border-b border-border/60 bg-muted/20 px-6 py-4 text-center">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.currentBalance')}
        </div>
        <div
          className={`mt-0.5 font-mono text-base font-bold tabular-nums ${
            balance >= 0 ? 'text-foreground' : 'text-expense'
          }`}
        >
          {fmt(balance)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumIncome')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-income">
          {fmt(account.income_total ?? 0)}
        </div>
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('detail.stats.accumExpense')}
        </div>
        <div className="mt-0.5 font-mono text-base font-bold tabular-nums text-expense">
          {fmt(account.expense_total ?? 0)}
        </div>
      </div>
    </div>
  )
}

/**
 * 信用卡 / 银行卡专属信息卡片。
 *
 * 展示规则:
 *   - bank_card: 银行 + 卡号末 4 位(2 项中只要任一有值就显示)
 *   - credit_card: 上面 2 项 + 信用额度 + 账单日 + 还款日 + 倒计时
 *   - 其它账户类型(cash / alipay / etc):整块不渲染
 *
 * 倒计时算法:今天日号 ≤ 目标日号 → 目标日号 - 今天日号;否则 → 跨月,
 * (本月剩余天数) + 目标日号。"今天"取本地时区,因为还款日是法律日历日,
 * 不需要 UTC。
 */
function AccountCardInfo({
  account,
  t,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
}) {
  const accountType = account.account_type || ''
  const isCreditCard = accountType === 'credit_card'
  // 主帳戶(§2.9 Phase 4,2026-08-02 改版,同日第二輪放寬到單卡):信用
  // 額度/帳單日/還款日設在 account_group 自己身上,或沒有掛靠任何群組的
  // 獨立信用卡自己身上;已經掛靠群組的子帳戶不再各自帶這些欄位——沿用
  // 群組的結帳週期,見下面 CreditCardBillingSection。
  const isAccountGroup = accountType === 'account_group'
  const isStandaloneCreditCard = isCreditCard && !account.parent_account_id
  const showsBillingFields = isAccountGroup || isStandaloneCreditCard
  const isBankOrCredit = isCreditCard || isAccountGroup || accountType === 'bank_card'
  if (!isBankOrCredit) return null

  const bankName = account.bank_name?.trim() || ''
  const cardLastFour = account.card_last_four?.trim() || ''
  const creditLimit = showsBillingFields ? account.credit_limit : null
  const billingDay = showsBillingFields ? account.billing_day : null
  const paymentDueDay = showsBillingFields ? account.payment_due_day : null

  // 信用卡已用额度 = -balance(余额为负表示欠款),剩余额度 = limit - used。
  // 这是粗略估算 — 没考虑账单周期,只看终身累计。但作为"大致还能刷多少"
  // 的信号已经够用,后续要精确版本应该按 billing_day 分账期算。
  //
  // 为什么用 balance 而不是 expense_total - income_total:后者会漏掉"储蓄卡
  // 转账到信用卡还款"这种 transfer-in 操作 —— 转账既不是 expense 也不是
  // income,只在 backend 的 balance 计算里被加回去(workspace.py 的
  // transfer_to bucket)。balance 已经统一包含初始余额 + 全部 income /
  // expense / transfer-in / transfer-out,跟 mobile 端
  // `getCreditCardUsedAmount` (balance < 0 ? -balance : 0) 完全一致。
  // 修复 issue #26:储蓄卡转账到信用卡额度没恢复。
  // 注意:account_group 自己的 balance 只反映"溢繳結轉"那类极少数直接打
  // 在群組身上的交易,不是真正的合併應繳金額——那个数字由下方
  // CreditCardBillingSection 的 available_credit(§2.9 Phase 4)才是准的。
  // 这里只static展示 credit_limit 本身,不再用 balance 推算"已用/剩余",
  // 避免跟下面权威数字打架。

  // 没有任何要展示的就直接不渲染(用户没填这些字段时)
  if (
    !bankName &&
    !cardLastFour &&
    creditLimit === null &&
    !billingDay &&
    !paymentDueDay
  ) {
    return null
  }

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  return (
    <div className="border-b border-border/60 bg-muted/10 px-6 py-3">
      {/* 第一行:银行 + 卡号末 4 位 + 类型 icon */}
      {(bankName || cardLastFour) ? (
        <div className="flex items-center gap-2 text-sm">
          {isCreditCard || isAccountGroup ? (
            <CreditCard className="h-4 w-4 text-muted-foreground" />
          ) : (
            <Banknote className="h-4 w-4 text-muted-foreground" />
          )}
          <span className="font-medium">{bankName || t('detail.account.bankUnknown')}</span>
          {cardLastFour ? (
            <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
              •••• {cardLastFour}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* 主帳戶群組 / 獨立信用卡:额度 + 账单日 + 还款日 + 倒计时 */}
      {showsBillingFields ? (
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
          {creditLimit !== null && creditLimit !== undefined ? (
            <CardInfoItem label={t('detail.account.creditLimit')} value={fmt(creditLimit)} />
          ) : null}
          {billingDay ? (
            <CardInfoItem
              label={t('detail.account.billingDay')}
              value={t('detail.account.dayOfMonth', { day: billingDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(billingDay) })}
            />
          ) : null}
          {paymentDueDay ? (
            <CardInfoItem
              label={t('detail.account.paymentDueDay')}
              value={t('detail.account.dayOfMonth', { day: paymentDueDay })}
              hint={t('detail.account.daysUntil', { days: daysUntilDay(paymentDueDay) })}
              urgent={daysUntilDay(paymentDueDay) <= 3}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function CardInfoItem({
  label,
  value,
  hint,
  urgent,
}: {
  label: string
  value: string
  hint?: string
  urgent?: boolean
}) {
  return (
    <div>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        <CalendarIcon className="h-3 w-3" />
        <span>{label}</span>
      </div>
      <div className="mt-0.5 text-sm font-medium tabular-nums">{value}</div>
      {hint ? (
        <div
          className={`text-[10px] tabular-nums ${
            urgent ? 'text-expense font-semibold' : 'text-muted-foreground'
          }`}
        >
          {hint}
        </div>
      ) : null}
    </div>
  )
}

/**
 * 今天到目标日号还有多少天。
 *   - 今天 ≤ 目标 → 本月内,直接差值
 *   - 今天 > 目标 → 跨月,本月剩余 + 目标日号
 *
 * 目标日号超过当月最大天数(比如 31 号但 2 月)按当月最后一天兜底,跟
 * mobile 端 AccountDetailPage 算法对齐。
 */
function daysUntilDay(targetDay: number): number {
  if (!Number.isFinite(targetDay) || targetDay < 1 || targetDay > 31) return 0
  const now = new Date()
  const today = now.getDate()
  if (today <= targetDay) {
    // 同月内 — 验证当月有这一天(2 月没有 31 号)
    const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
    const effective = Math.min(targetDay, lastDayThisMonth)
    return effective - today
  }
  // 跨月 — 算本月剩余 + 下月目标日(下月可能也没那一天)
  const lastDayThisMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate()
  const lastDayNextMonth = new Date(now.getFullYear(), now.getMonth() + 2, 0).getDate()
  const effective = Math.min(targetDay, lastDayNextMonth)
  return (lastDayThisMonth - today) + effective
}

/** `2026-08-05T00:00:00Z` -> `2026/08/05`,對齊 Moze 參考截圖的日期顯示格式。 */
function formatDateSlash(iso: string): string {
  return iso.slice(0, 10).replace(/-/g, '/')
}

/**
 * 信用卡合併帳單(§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md)。
 *
 * 只在 account_type=credit_card 且当前有选中账本(activeLedgerId,帐单摘要
 * 是 ledger-scoped 读端点)时渲染。帳戶没設定 billing_day/payment_due_day
 * 时后端回 400,静默不渲染(不是 error 状态,只是这张卡还没配置帐单周期)。
 * 「繳款」按钮开一个小弹窗,建一笔 transfer 交易(cardPayment 语意化端点),
 * 成功后重新拉一次帳單摘要反映最新应缴金额。
 */
function CreditCardBillingSection({
  account,
  t,
  onClose,
  onPeriodRangeChange,
  onCycleOffsetChange,
}: {
  account: AccountWithStats
  t: (key: string, params?: Record<string, string | number>) => string
  onClose: () => void
  /** 下面交易明細列表要跟著目前瀏覽的帳單週期走(2026-08-02 用户反馈:
   *  切期数下面明细却不动)—— 每次選中週期的日期區間變化時往上通知一次,
   *  非信用卡帳戶 / 週期還沒算出來時傳 null 清空過濾。 */
  onPeriodRangeChange?: (range: { start: string; end: string } | null) => void
  /** §2.9.5.4:目前瀏覽的帳單週期(cycleOffset,0=最近一次已結束)往上
   *  回報,讓 CardRewardRulesSection 能跟著同步。非 billing-root 帳戶
   *  (沒有週期選擇器)固定回報 0。 */
  onCycleOffsetChange?: (offset: number) => void
}) {
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const toast = useToast()
  const navigate = useNavigate()
  // §2.9 Phase 4(2026-08-02 改版,同日第二輪放寬到單卡):合併帳單只對
  // account_group(主帳戶群組),或沒有掛靠任何群組的獨立信用卡查——已經
  // 掛靠群組的子卡不算,要透過它的群組查(對齊後端 is_billing_root)。
  const isBillingRoot =
    (account.account_type || '') === 'account_group' ||
    ((account.account_type || '') === 'credit_card' && !account.parent_account_id)

  const [summary, setSummary] = useState<AccountBillingSummary | null>(null)
  const [suggestion, setSuggestion] = useState<AccountInterestFreeSuggestion | null>(null)
  const [loading, setLoading] = useState(false)
  const [available, setAvailable] = useState(false)
  // 帳單週期瀏覽(§2.9 補強,2026-08-02):0 = 最近一次已結束的週期,負數 =
  // 更早的歷史週期,+1 = 目前還在累積中的那期。切換帳戶時歸零,不沿用上一
  // 張卡瀏覽到的期數。
  const [cycleOffset, setCycleOffset] = useState(0)
  // 2026-08-03 使用者反饋:上期(cycleOffset=0)如果已經繳清,預設畫面不該
  // 停在一筆「已經結案」的舊帳單,應該自動跳去這個月還在累積中的那期(+1)。
  // 用 ref 而非 state 記「這次開卡是否已經自動跳過一次」——只在初次載入
  // (cycleOffset === 0 的那次 fetch)判斷一次,避免使用者手動切回上期後又
  // 被重新導回去。
  const autoAdvancedRef = useRef(false)

  // 2026-08-03 使用者反饋:手機頁面這個區塊太長,展開時交易列表被推到看
  // 不見的地方——加個折疊開關,預設收合只留一行「目前應繳」,點了才展開
  // 看週期明細/子卡明細/免息期建議/自動扣繳等細項。切換帳戶時歸零(不沿用
  // 上一張卡的展開狀態)。
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setExpanded(false)
  }, [account.id])

  const [payOpen, setPayOpen] = useState(false)
  const [payAmount, setPayAmount] = useState('')
  const [payFromAccountId, setPayFromAccountId] = useState('')
  const [payAccounts, setPayAccounts] = useState<ReadAccount[]>([])
  const [paying, setPaying] = useState(false)

  const [installOpen, setInstallOpen] = useState(false)

  useEffect(() => {
    autoAdvancedRef.current = false
    setCycleOffset(0)
  }, [account.id])

  useEffect(() => {
    onCycleOffsetChange?.(isBillingRoot ? cycleOffset : 0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBillingRoot, cycleOffset])

  useEffect(() => {
    if (!isBillingRoot || !token || !activeLedgerId) {
      setAvailable(false)
      setSummary(null)
      setSuggestion(null)
      return
    }
    let cancelled = false
    setLoading(true)
    Promise.all([
      fetchAccountBillingSummary(token, activeLedgerId, account.id, cycleOffset),
      fetchAccountInterestFreeSuggestion(token, activeLedgerId, account.id),
    ])
      .then(([s, sug]) => {
        if (cancelled) return
        setSummary(s)
        setSuggestion(sug)
        setAvailable(true)
        if (
          !autoAdvancedRef.current &&
          cycleOffset === 0 &&
          s.period_remaining_due <= 0 &&
          s.period_has_newer
        ) {
          autoAdvancedRef.current = true
          setCycleOffset(1)
        }
      })
      .catch(() => {
        if (cancelled) return
        setAvailable(false)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBillingRoot, token, activeLedgerId, account.id, cycleOffset])

  useEffect(() => {
    if (!isBillingRoot || !summary) {
      onPeriodRangeChange?.(null)
      return
    }
    onPeriodRangeChange?.({ start: summary.period_cycle_start, end: summary.period_cycle_end })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isBillingRoot, summary])

  // 卸载(切换到别的账户 / 关闭详情弹窗)时清掉过滤,不让下一个账户的交易
  // 列表继承上一张卡選中的期数窗口。
  useEffect(() => {
    return () => onPeriodRangeChange?.(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!isBillingRoot || !activeLedgerId) return null
  if (loading) {
    return (
      <div className="border-b border-border/60 px-6 py-3 text-xs text-muted-foreground">
        {t('cardBilling.loading')}
      </div>
    )
  }
  if (!available || !summary) return null

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const openPayDialog = () => {
    setPayAmount(summary.remaining_due > 0 ? String(summary.remaining_due) : '')
    setPayFromAccountId('')
    setPayOpen(true)
    if (token && activeLedgerId) {
      fetchReadAccounts(token, activeLedgerId)
        .then((accts) =>
          // 付款來源不能是這個群組自己,也不能是任何 account_group(§2.9
          // Phase 4:群組沒有自己的資金,不能拿來當繳費來源,對齊後端
          // `_assert_account_not_group` 校驗)。
          setPayAccounts(accts.filter((a) => a.id !== account.id && a.account_type !== 'account_group'))
        )
        .catch(() => setPayAccounts([]))
    }
  }

  const submitPayment = async () => {
    if (!token || !activeLedgerId) return
    const amount = Number(payAmount)
    if (!Number.isFinite(amount) || amount <= 0 || !payFromAccountId) return
    setPaying(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        cardPayment(token, activeLedgerId, account.id, base, {
          amount,
          from_account_id: payFromAccountId,
        }),
      )
      toast.success(t('cardPayment.notice.success'), t('notice.success'))
      setPayOpen(false)
      fetchAccountBillingSummary(token, activeLedgerId, account.id, cycleOffset)
        .then(setSummary)
        .catch(() => {})
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setPaying(false)
    }
  }

  return (
    <div className="space-y-3 border-b border-border/60 bg-muted/10 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {t('cardBilling.title')}
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
        {/* 帳單週期日期翻頁(2026-08-03 使用者反饋:移到收合開關旁,永遠
            可見,不受 expanded 影響)—— 下面交易列表跟著這個日期區間過濾
            (見 onPeriodRangeChange),使用者要看某一期交易不該還得先展開
            這個區塊。 */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={!summary.period_has_older}
            aria-label={t('cardBilling.period.prev')}
            onClick={() => setCycleOffset((v) => v - 1)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="font-mono text-xs font-medium tabular-nums">
            {formatDateSlash(summary.period_cycle_start)} – {formatDateSlash(summary.period_cycle_end)}
          </span>
          <button
            type="button"
            disabled={!summary.period_has_newer}
            aria-label={t('cardBilling.period.next')}
            onClick={() => setCycleOffset((v) => v + 1)}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <button
          type="button"
          className="rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground hover:opacity-90"
          onClick={openPayDialog}
        >
          {t('cardBilling.payButton')}
        </button>
      </div>
      {!expanded ? (
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">{t('cardBilling.remainingDue')}</span>
          <span className="font-mono font-semibold tabular-nums text-expense">
            {fmt(summary.remaining_due)}
          </span>
        </div>
      ) : null}
      {expanded ? (
        <>
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.remainingDue')}
          </div>
          <div className="font-mono font-semibold tabular-nums text-expense">
            {fmt(summary.remaining_due)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.dueDate')}
          </div>
          <div className="font-mono">{summary.due_date.slice(0, 10)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.statementAmount')}
          </div>
          <div className="font-mono tabular-nums">{fmt(summary.statement_amount)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
            {t('cardBilling.openCycleSpend')}
          </div>
          <div className="font-mono tabular-nums">{fmt(summary.open_cycle_spend)}</div>
        </div>
        {summary.available_credit !== null && summary.available_credit !== undefined ? (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.availableCredit')}
            </div>
            <div className="font-mono font-semibold tabular-nums text-income">
              {fmt(summary.available_credit)}
            </div>
          </div>
        ) : null}
      </div>
      {/* 帳單週期明細(§2.9 補強,2026-08-02):日期翻頁本身已移到上面收合
          開關旁永遠可見,這裡只保留該週期的統計明細。 */}
      <div className="rounded-lg border border-border/50 bg-background/40 p-3">
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.period.newSpend')}
            </div>
            <div className="font-mono tabular-nums">{fmt(summary.period_new_spend)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.period.carryoverDue')}
            </div>
            <div className="font-mono tabular-nums">{fmt(summary.period_carryover_due)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.period.totalDue')}
            </div>
            <div className="font-mono font-semibold tabular-nums">{fmt(summary.period_total_due)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.period.paidInCycle')}
            </div>
            <div className="font-mono tabular-nums text-income">{fmt(summary.period_paid_in_cycle)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {t('cardBilling.period.remainingDue')}
            </div>
            <div className="font-mono font-semibold tabular-nums text-expense">
              {fmt(summary.period_remaining_due)}
            </div>
          </div>
        </div>
      </div>
      {summary.members.length > 1 ? (
        <div className="space-y-1 text-xs text-muted-foreground">
          <div className="font-medium">{t('cardBilling.members')}</div>
          {summary.members.map((m) => (
            <div key={m.account_id} className="flex items-center justify-between">
              <span>{m.account_name}</span>
              <span className="font-mono tabular-nums">{fmt(m.cycle_spend)}</span>
            </div>
          ))}
        </div>
      ) : null}
      {suggestion ? (
        <div className="rounded-md bg-background/60 px-3 py-2 text-xs text-muted-foreground">
          {t('cardBilling.interestFreeHint', {
            date: suggestion.next_cycle_start.slice(0, 10),
            due: suggestion.next_cycle_due_date.slice(0, 10),
            days: suggestion.max_interest_free_days,
          })}
        </div>
      ) : null}

      {/* 自動扣繳(§2.9,2026-08-04 改版)状态:開關 + 來源帳戶現在直接設在
          這張卡/群組自己的編輯表單裡(帳戶列表頁「編輯」),不再是另一個
          頁面的週期性收支規則,這裡只顯示目前狀態 + 指引入口。分期入口
          (§2.3)維持既有的另一個通用頁面。 */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span
          className={`rounded-md px-2.5 py-1 font-medium ${
            account.auto_pay_enabled
              ? 'bg-primary/15 text-primary'
              : 'bg-muted/50 text-muted-foreground'
          }`}
        >
          {account.auto_pay_enabled ? t('cardBilling.autoPayOn') : t('cardBilling.autoPayOff')}
        </span>
        <button
          type="button"
          className="rounded-md border border-border px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted/50"
          onClick={() => {
            // 详情弹窗是全局挂载的(可能从任意页面打开),先关自己 + 跳到
            // 账户列表页,再派发 openEditAccount 让该页把这个账户装进编辑
            // 表单 —— 单纯 navigate 在已经身处 /app/accounts 时是个 no-op,
            // 弹窗也不会自己关掉,点了跟没点一样(2026-08-02 用户实测反馈)。
            onClose()
            navigate('/app/accounts')
            dispatchOpenEditAccount(account as WorkspaceAccount)
          }}
        >
          {t('cardBilling.manageAutoPay')}
        </button>
        <button
          type="button"
          disabled={summary.remaining_due <= 0}
          title={summary.remaining_due <= 0 ? t('cardBilling.installment.noBalanceHint') : undefined}
          className="rounded-md border border-border px-2.5 py-1 font-medium text-muted-foreground hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => setInstallOpen(true)}
        >
          {t('cardBilling.setupInstallment')}
        </button>
      </div>
        </>
      ) : null}

      <Dialog open={payOpen} onOpenChange={setPayOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('cardPayment.dialog.title')}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1">
              <Label>{t('cardPayment.dialog.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                value={payAmount}
                onChange={(e) => setPayAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('cardPayment.dialog.fromAccount')}</Label>
              <Select value={payFromAccountId} onValueChange={setPayFromAccountId}>
                <SelectTrigger>
                  <SelectValue placeholder={t('cardPayment.dialog.fromAccountPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {payAccounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-sm"
              onClick={() => setPayOpen(false)}
            >
              {t('dialog.cancel')}
            </button>
            <button
              type="button"
              disabled={paying || !payFromAccountId || !payAmount}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
              onClick={() => void submitPayment()}
            >
              {t('cardPayment.dialog.submit')}
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {installOpen ? (
        <InstallmentQuickCreateDialog
          onClose={() => setInstallOpen(false)}
          token={token || ''}
          ledgerId={activeLedgerId}
          accountId={account.id}
          accountLabel={account.name}
          defaultAmount={summary.period_total_due > 0 ? summary.period_total_due : summary.remaining_due}
          defaultDueDate={summary.due_date}
          retryOnConflict={retryOnConflict}
          onCreated={() => {
            fetchAccountBillingSummary(token || '', activeLedgerId, account.id, cycleOffset)
              .then(setSummary)
              .catch(() => {})
          }}
        />
      ) : null}
    </div>
  )
}

/**
 * 帳單分期快速建立(§2.3,2026-08-02 補強):不再只靠導去 `/app/
 * installment-plans` 頁面 —— 直接在帳單卡片這裡開一個小視窗設定,建完之後
 * 一樣是普通的分期付款計畫,還是可以在 `/app/installment-plans` 總頁面
 * 管理(調利率/提前還本/提前結清等差異化操作留在那邊,這裡只做「新建」)。
 * 攤還方式對應信用卡常見的三種算法:等額本金 / 等額本息 / 固定利率。
 */
function InstallmentQuickCreateDialog({
  onClose,
  token,
  ledgerId,
  accountId,
  accountLabel,
  defaultAmount,
  defaultDueDate,
  retryOnConflict,
  onCreated,
}: {
  onClose: () => void
  token: string
  ledgerId: string | null
  /** 建分期的目標帳戶(§2.3,2026-08-02 第三輪):永遠是使用者正在瀏覽帳單
   *  的那個帳戶(可能是 account_group 主帳戶,也可能是沒有掛靠任何群組的
   *  獨立信用卡)——不再讓使用者從子卡清單裡手動挑一張,用戶反饋「有主帳戶
   *  就該以主帳戶為單位分期,不是選擇信用卡」,server 端會自動把沖銷/各期
   *  交易分攤/掛到正確的真實子帳戶上。 */
  accountId: string
  accountLabel: string
  defaultAmount: number
  defaultDueDate: string
  retryOnConflict: <T,>(ledgerId: string, submit: (baseChangeId: number) => Promise<T>) => Promise<T>
  onCreated: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [amount, setAmount] = useState(defaultAmount > 0 ? String(defaultAmount) : '')
  const [periods, setPeriods] = useState('3')
  const [firstPeriodAt, setFirstPeriodAt] = useState(defaultDueDate.slice(0, 10))
  const [repaymentMethod, setRepaymentMethod] = useState<InstallmentRepaymentMethod>('equal_principal')
  const [interestRate, setInterestRate] = useState('0')
  const [interestPeriod, setInterestPeriod] = useState<InstallmentInterestPeriod>('monthly')
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // 分類(2026-08-02 補強):不选就跟以前一样落空(交易列表显示"-")——用户
  // 反馈这样完全看不出那笔分期扣款是什么,所以加个选择器,并且比照 §2.5
  // 借還款「+ 建立新欠款」的既有 sentinel 模式,允许直接现场新建一个分類,
  // 不用跳去分類頁面來回切換。
  const NEW_CATEGORY_VALUE = '__new__'
  const [categories, setCategories] = useState<WorkspaceCategory[]>([])
  const [categoryId, setCategoryId] = useState('')
  const [newCategoryName, setNewCategoryName] = useState('')

  useEffect(() => {
    if (!ledgerId) return
    let cancelled = false
    fetchWorkspaceCategories(token, { ledgerId, limit: 500 })
      .then((rows) => {
        if (!cancelled) setCategories(rows.filter((c) => c.kind === 'expense'))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [token, ledgerId])

  const canSubmit =
    Boolean(accountId) &&
    Number(amount) > 0 &&
    Number(periods) >= 1 &&
    Boolean(firstPeriodAt) &&
    Boolean(ledgerId) &&
    (categoryId !== NEW_CATEGORY_VALUE || Boolean(newCategoryName.trim()))

  const handleSubmit = async () => {
    if (!ledgerId || !canSubmit) return
    setSubmitting(true)
    try {
      // 新建分類跟建分期計畫是兩次獨立的 retryOnConflict 呼叫(對齊 §2.5
      // 借還款「+ 建立新欠款」的既有慣例——分類建立失敗只提示,不影響
      // 使用者接下來仍可以選別的既有分類重試,不用整份表單重填)。
      let finalCategoryId = categoryId
      if (categoryId === NEW_CATEGORY_VALUE) {
        const trimmed = newCategoryName.trim()
        const created = await retryOnConflict(ledgerId, (base) =>
          createCategory(token, ledgerId, base, { name: trimmed, kind: 'expense' }),
        )
        finalCategoryId = created.entity_id || ''
      }
      await retryOnConflict(ledgerId, (base) =>
        createInstallmentPlan(token, ledgerId, base, {
          total_amount: Number(amount),
          periods: Math.round(Number(periods)),
          first_period_at: new Date(`${firstPeriodAt}T00:00:00`).toISOString(),
          account_id: accountId,
          category_id: finalCategoryId || null,
          note: note || null,
          repayment_method: repaymentMethod,
          interest_period: interestPeriod,
          interest_rate: Number(interestRate) || 0,
          // 从帳單卡片這裡建的分期,永遠是「把已經欠下的帳單轉成分期」——
          // 一定要沖銷,不然原本那筆消費金額會跟分期各期新生成的 expense
          // 重複計入應繳金額(§2.9,2026-08-02 補強)。
          offset_existing_balance: true,
        }),
      )
      toast.success(t('cardBilling.installment.notice.success'), t('notice.success'))
      onCreated()
      onClose()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('cardBilling.installment.dialogTitle')}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <p className="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            {t('cardBilling.installment.offsetNotice')}
          </p>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.account')}</Label>
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
              {accountLabel}
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.category')}</Label>
            <Select
              value={categoryId}
              onValueChange={(value) => {
                setCategoryId(value)
                if (value !== NEW_CATEGORY_VALUE) setNewCategoryName('')
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder={t('transactions.placeholder.categoryName')} />
              </SelectTrigger>
              <SelectContent>
                {categories.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
                <SelectItem value={NEW_CATEGORY_VALUE}>
                  {t('cardBilling.installment.newCategory')}
                </SelectItem>
              </SelectContent>
            </Select>
            {categoryId === NEW_CATEGORY_VALUE ? (
              <Input
                autoFocus
                value={newCategoryName}
                onChange={(e) => setNewCategoryName(e.target.value)}
                placeholder={t('cardBilling.installment.newCategoryPlaceholder')}
              />
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.totalAmount')}</Label>
            <Input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.periods')}</Label>
            <Input
              type="number"
              inputMode="numeric"
              min="1"
              max="600"
              value={periods}
              onChange={(e) => setPeriods(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.firstPeriodAt')}</Label>
            <Input type="date" value={firstPeriodAt} onChange={(e) => setFirstPeriodAt(e.target.value)} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.repaymentMethod')}</Label>
              <Select
                value={repaymentMethod}
                onValueChange={(value) => setRepaymentMethod(value as InstallmentRepaymentMethod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="equal_principal">
                    {t('installmentPlans.repaymentMethod.equalPrincipal')}
                  </SelectItem>
                  <SelectItem value="equal_installment">
                    {t('installmentPlans.repaymentMethod.equalInstallment')}
                  </SelectItem>
                  <SelectItem value="fixed_interest">
                    {t('installmentPlans.repaymentMethod.fixedInterest')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.interestRate')}</Label>
              <Input
                type="number"
                min="0"
                step="0.001"
                value={interestRate}
                onChange={(e) => setInterestRate(e.target.value)}
              />
            </div>
            <div className="space-y-1 sm:col-span-2">
              <Label>{t('installmentPlans.field.interestPeriod')}</Label>
              <Select
                value={interestPeriod}
                onValueChange={(value) => setInterestPeriod(value as InstallmentInterestPeriod)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">{t('installmentPlans.interestPeriod.monthly')}</SelectItem>
                  <SelectItem value="daily">{t('installmentPlans.interestPeriod.daily')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="rounded-md border border-border px-3 py-1.5 text-sm"
            onClick={onClose}
          >
            {t('dialog.cancel')}
          </button>
          <button
            type="button"
            disabled={submitting || !canSubmit}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={() => void handleSubmit()}
          >
            {t('installmentPlans.button.create')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
