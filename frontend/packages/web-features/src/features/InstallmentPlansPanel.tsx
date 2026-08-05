import { useEffect, useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'

import type {
  InstallmentEarlyRepayPayload,
  InstallmentPeriodUpdatePayload,
  InstallmentRebalancePayload,
  ReadAccount,
  ReadInstallmentPeriod,
  ReadInstallmentPlan,
  WorkspaceCategory,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { InstallmentPlanForm } from '../forms'
import { installmentPlanDefaults } from '../forms'
import { formatAmountTrimmed } from '../format'

type InstallmentPlansPanelProps = {
  plans: readonly ReadInstallmentPlan[]
  categories: readonly WorkspaceCategory[]
  accounts: readonly ReadAccount[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  form: InstallmentPlanForm
  onFormChange: (next: InstallmentPlanForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (plan: ReadInstallmentPlan) => Promise<void> | void
  /** §2.12.1:每个计画的期数明细,懒加载(点开才 fetch),key=plan.id。 */
  periodsByPlanId: Readonly<Record<string, ReadInstallmentPeriod[] | undefined>>
  onLoadPeriods: (plan: ReadInstallmentPlan) => void
  onUpdatePeriod: (
    plan: ReadInstallmentPlan,
    periodNo: number,
    payload: InstallmentPeriodUpdatePayload,
  ) => Promise<void> | void
  onRebalance: (
    plan: ReadInstallmentPlan,
    periodNo: number,
    payload: InstallmentRebalancePayload,
  ) => Promise<void> | void
  onEarlyRepay: (plan: ReadInstallmentPlan, payload: InstallmentEarlyRepayPayload) => Promise<void> | void
  onPayoff: (plan: ReadInstallmentPlan) => Promise<void> | void
  onTerminateFuture: (plan: ReadInstallmentPlan) => Promise<void> | void
  /** 账本 owner 才能写(server _OWNER_ONLY_ROLES),非 owner 时按钮禁用。 */
  canManage: boolean
  /** 從交易詳情的編輯選擇彈窗跳轉過來時(2026-08-03 使用者反饋 #4),要
   *  高亮 + 捲動到的分期計畫 id。 */
  highlightPlanId?: string | null
  /** 同上跳轉來源,附帶「要自動打開哪一種差異化操作表單」——editThis/
   *  editFuture 需要先展開期數明細、按 txId 定位到具體那一期。 */
  autoOpenAction?: {
    planId: string
    action: 'editThis' | 'editFuture' | 'earlyRepay' | 'payoff'
    txId?: string
  } | null
  /** 自動打開的表單已經開好(或確定開不了)之後回呼一次,讓呼叫端清掉
   *  query 參數,避免使用者手動關掉表單後又被重新打開。 */
  onAutoOpenConsumed?: () => void
}

/**
 * 分期付款计划管理面板(MOZE_FEATURE_GAP_SD.md §2.3 / Phase 1.5 修正版 §2.12.1)。
 *
 * 建计画时 server 依攤還算法当下一次生成**全部**期数(不再是只生成第一期,
 * 剩余靠排程推进)。这里展开"期数明细"可以看到每期本金/利息拆分,并提供
 * 差异化编辑:单独改一期(overridden)、调利率连同未来(rebalance-from)、
 * 部分还本(early-repay-principal)、提前结清(payoff)、终止未来分期
 * (terminate-future)。创建后期数/总额/攤還参数不可改(要调这些走上面的
 * 差异化端点,不是打平这个 PATCH)。
 */
export function InstallmentPlansPanel({
  plans,
  categories,
  accounts,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  periodsByPlanId,
  onLoadPeriods,
  onUpdatePeriod,
  onRebalance,
  onEarlyRepay,
  onPayoff,
  onTerminateFuture,
  canManage,
  highlightPlanId,
  autoOpenAction,
  onAutoOpenConsumed,
}: InstallmentPlansPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadInstallmentPlan | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [expandedPlanId, setExpandedPlanId] = useState<string | null>(null)
  // 主帳戶(§2.9,2026-08-02 群組模型):account_group 是純管理容器,不能被
  // 分期付款掛账(後端 _assert_account_not_group 會拒),選擇器不該讓它出現。
  const tradableAccounts = accounts.filter((a) => a.account_type !== 'account_group')

  const handleOpenCreate = () => {
    onFormChange(installmentPlanDefaults())
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const toggleExpand = (plan: ReadInstallmentPlan) => {
    if (expandedPlanId === plan.id) {
      setExpandedPlanId(null)
      return
    }
    setExpandedPlanId(plan.id)
    if (!periodsByPlanId[plan.id]) onLoadPeriods(plan)
  }

  // 2026-08-03 使用者反饋 #4:editThis/editFuture 需要先展開目標計畫的期數
  // 明細,才能按 txId 找到具體要編輯的那一期(見下面 InstallmentPlanCard
  // 的 autoOpen 处理)。earlyRepay/payoff 不需要期数,不用展开。
  useEffect(() => {
    if (!autoOpenAction) return
    if (autoOpenAction.action !== 'editThis' && autoOpenAction.action !== 'editFuture') return
    const plan = plans.find((p) => p.id === autoOpenAction.planId)
    if (!plan) return
    setExpandedPlanId(plan.id)
    if (!periodsByPlanId[plan.id]) onLoadPeriods(plan)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpenAction, plans])

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === 'expense'),
    [categories],
  )

  // 新建模式才需要这几个字段校验(编辑模式只能改 note)。
  const canSubmit = form.editingId
    ? true
    : Boolean(form.total_amount.trim()) &&
      Boolean(form.periods.trim()) &&
      Boolean(form.first_period_at.trim())

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('installmentPlans.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('installmentPlans.button.create')}
        </Button>
      </div>

      {plans.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="2" y="5" width="20" height="14" rx="2" />
              <path d="M2 10h20" />
            </svg>
          }
          title={t('installmentPlans.empty')}
          description={t('installmentPlans.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {plans.map((plan) => {
            const cat = plan.category_id ? categories.find((c) => c.id === plan.category_id) : null
            return (
              <InstallmentPlanCard
                key={plan.id}
                plan={plan}
                category={cat || null}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                canManage={canManage}
                expanded={expandedPlanId === plan.id}
                periods={periodsByPlanId[plan.id]}
                onToggleExpand={() => toggleExpand(plan)}
                onDelete={() => setPendingDelete(plan)}
                onUpdatePeriod={(periodNo, payload) => onUpdatePeriod(plan, periodNo, payload)}
                onRebalance={(periodNo, payload) => onRebalance(plan, periodNo, payload)}
                onEarlyRepay={(payload) => onEarlyRepay(plan, payload)}
                onPayoff={() => onPayoff(plan)}
                onTerminateFuture={() => onTerminateFuture(plan)}
                highlighted={Boolean(highlightPlanId) && plan.id === highlightPlanId}
                autoOpen={autoOpenAction && autoOpenAction.planId === plan.id ? autoOpenAction : null}
                onAutoOpenConsumed={onAutoOpenConsumed}
              />
            )
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId
                ? t('installmentPlans.button.update')
                : t('installmentPlans.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.totalAmount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                disabled={!!form.editingId}
                placeholder="0"
                value={form.total_amount}
                onChange={(e) => onFormChange({ ...form, total_amount: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('installmentPlans.field.periods')}</Label>
              <Input
                type="number"
                inputMode="numeric"
                min="1"
                max="600"
                disabled={!!form.editingId}
                value={form.periods}
                onChange={(e) => onFormChange({ ...form, periods: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('installmentPlans.field.firstPeriodAt')}</Label>
              <Input
                type="datetime-local"
                disabled={!!form.editingId}
                value={form.first_period_at}
                onChange={(e) => onFormChange({ ...form, first_period_at: e.target.value })}
              />
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>{t('installmentPlans.field.repaymentMethod')}</Label>
                <Select
                  value={form.repayment_method}
                  disabled={!!form.editingId}
                  onValueChange={(value) =>
                    onFormChange({
                      ...form,
                      repayment_method: value as InstallmentPlanForm['repayment_method'],
                    })
                  }
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
                  disabled={!!form.editingId}
                  value={form.interest_rate}
                  onChange={(e) => onFormChange({ ...form, interest_rate: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('installmentPlans.field.interestPeriod')}</Label>
                <Select
                  value={form.interest_period}
                  disabled={!!form.editingId}
                  onValueChange={(value) =>
                    onFormChange({ ...form, interest_period: value as InstallmentPlanForm['interest_period'] })
                  }
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
              <div className="space-y-1">
                <Label>{t('installmentPlans.field.gracePeriodMonths')}</Label>
                <Input
                  type="number"
                  min="0"
                  disabled={!!form.editingId}
                  value={form.grace_period_months}
                  onChange={(e) => onFormChange({ ...form, grace_period_months: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('installmentPlans.field.remainderPosition')}</Label>
                <Select
                  value={form.remainder_position}
                  disabled={!!form.editingId}
                  onValueChange={(value) =>
                    onFormChange({ ...form, remainder_position: value as InstallmentPlanForm['remainder_position'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="first">{t('installmentPlans.remainderPosition.first')}</SelectItem>
                    <SelectItem value="last">{t('installmentPlans.remainderPosition.last')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <p className="text-sm font-medium">{t('installmentPlans.field.roundAmounts')}</p>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.round_amounts}
                  aria-label={t('installmentPlans.field.roundAmounts') as string}
                  disabled={!!form.editingId}
                  onClick={() => onFormChange({ ...form, round_amounts: !form.round_amounts })}
                  className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                    form.round_amounts ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.round_amounts ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('transactions.table.category')}</Label>
              <button
                type="button"
                disabled={!!form.editingId}
                onClick={() => setCategoryPickerOpen(true)}
                className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                  {form.category_name || t('transactions.placeholder.categoryName')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>

            <div className="space-y-1">
              <Label>{t('installmentPlans.field.account')}</Label>
              <Select
                value={form.account_id || '__none__'}
                disabled={!!form.editingId}
                onValueChange={(value) => {
                  if (value === '__none__') {
                    onFormChange({ ...form, account_id: '', account_name: '' })
                    return
                  }
                  const acc = accounts.find((a) => a.id === value)
                  onFormChange({ ...form, account_id: value, account_name: acc?.name || '' })
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__none__">
                    <span className="text-muted-foreground">
                      {t('transactions.placeholder.noAccount')}
                    </span>
                  </SelectItem>
                  {tradableAccounts.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId
                ? t('installmentPlans.button.update')
                : t('installmentPlans.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind="expense"
        rows={categoryPickerRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || undefined}
        title={t('transactions.placeholder.categoryName')}
        onSelect={(cat) =>
          onFormChange({ ...form, category_id: cat.id, category_name: cat.name })
        }
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('installmentPlans.delete.title')}
        description={t('installmentPlans.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function InstallmentPlanCard({
  plan,
  category,
  currency,
  iconPreviewUrlByFileId,
  canManage,
  expanded,
  periods,
  onToggleExpand,
  onDelete,
  onUpdatePeriod,
  onRebalance,
  onEarlyRepay,
  onPayoff,
  onTerminateFuture,
  highlighted,
  autoOpen,
  onAutoOpenConsumed,
}: {
  plan: ReadInstallmentPlan
  category: WorkspaceCategory | null
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  expanded: boolean
  periods: ReadInstallmentPeriod[] | undefined
  onToggleExpand: () => void
  onDelete: () => void
  onUpdatePeriod: (periodNo: number, payload: InstallmentPeriodUpdatePayload) => void
  onRebalance: (periodNo: number, payload: InstallmentRebalancePayload) => void
  onEarlyRepay: (payload: InstallmentEarlyRepayPayload) => void
  onPayoff: () => void
  onTerminateFuture: () => void
  highlighted?: boolean
  autoOpen?: {
    planId: string
    action: 'editThis' | 'editFuture' | 'earlyRepay' | 'payoff'
    txId?: string
  } | null
  onAutoOpenConsumed?: () => void
}) {
  const t = useT()
  const title = category?.name || plan.category_id || t('budgets.label.unknownCategory')
  const isDone = plan.status === 'settled' || plan.status === 'terminated'
  const ratio = plan.periods > 0 ? Math.min(plan.paid_periods / plan.periods, 1) : 0

  const [editingPeriod, setEditingPeriod] = useState<ReadInstallmentPeriod | null>(null)
  const [rebalanceOpen, setRebalanceOpen] = useState(false)
  const [rebalanceDefaultPeriodNo, setRebalanceDefaultPeriodNo] = useState<number | undefined>(undefined)
  const [earlyRepayOpen, setEarlyRepayOpen] = useState(false)
  const [pendingPayoff, setPendingPayoff] = useState(false)
  const [pendingTerminate, setPendingTerminate] = useState(false)
  const [busy, setBusy] = useState(false)

  // 2026-08-03 使用者反饋 #4:從交易詳情的編輯選擇彈窗跳轉過來,自動打開
  // 對應的差異化操作表單,不需要使用者自己在一堆按鈕裡再找一次。
  // editThis/editFuture 要等 periods 載入後按 txId 找到具體那一期才能開。
  useEffect(() => {
    if (!autoOpen || isDone) return
    if (autoOpen.action === 'earlyRepay') {
      setEarlyRepayOpen(true)
      onAutoOpenConsumed?.()
      return
    }
    if (autoOpen.action === 'payoff') {
      setPendingPayoff(true)
      onAutoOpenConsumed?.()
      return
    }
    if (!periods) return // 期数明细还没载入,等下一次 effect 重跑
    const target = periods.find((p) => p.tx_id === autoOpen.txId)
    if (!target) {
      onAutoOpenConsumed?.()
      return
    }
    if (autoOpen.action === 'editThis') {
      setEditingPeriod(target)
    } else {
      setRebalanceDefaultPeriodNo(target.period_no)
      setRebalanceOpen(true)
    }
    onAutoOpenConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoOpen, periods, isDone])

  return (
    <div
      id={`installment-plan-${plan.id}`}
      className={`rounded-xl border bg-card p-4 transition hover:border-primary/40 hover:shadow-sm ${
        highlighted ? 'border-primary ring-2 ring-primary/40' : 'border-border/60'
      }`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          <CategoryIcon
            icon={category?.icon}
            iconType={category?.icon_type || 'material'}
            iconCloudFileId={category?.icon_cloud_file_id || null}
            iconPreviewUrlByFileId={iconPreviewUrlByFileId}
            size={24}
          />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{title}</span>
            {plan.status === 'settled' ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('installmentPlans.status.settled')}
              </span>
            ) : null}
            {plan.status === 'terminated' ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('installmentPlans.status.terminated')}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {t('installmentPlans.label.progress')
              .replace('{paid}', String(plan.paid_periods))
              .replace('{total}', String(plan.periods))}
            {' · '}
            <Amount value={plan.period_amount} currency={currency} size="sm" tone="default" />
            {' / '}
            {t('installmentPlans.label.perPeriod')}
          </div>
          {plan.note ? (
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{plan.note}</div>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={plan.total_amount} currency={currency} size="md" bold tone="default" />
        </div>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-primary transition-all" style={{ width: `${ratio * 100}%` }} />
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onToggleExpand}>
          {expanded ? t('installmentPlans.button.hidePeriods') : t('installmentPlans.button.showPeriods')}
        </Button>
        {!isDone ? (
          <>
            <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setRebalanceOpen(true)}>
              {t('installmentPlans.button.rebalance')}
            </Button>
            <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setEarlyRepayOpen(true)}>
              {t('installmentPlans.button.earlyRepay')}
            </Button>
            <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingPayoff(true)}>
              {t('installmentPlans.button.payoff')}
            </Button>
            <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingTerminate(true)}>
              {t('installmentPlans.button.terminateFuture')}
            </Button>
          </>
        ) : null}
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
          {t('common.delete')}
        </Button>
      </div>

      {expanded ? (
        <div className="mt-3 space-y-1 rounded-lg border border-border/50 bg-muted/10 p-2">
          {!periods ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">{t('common.loading')}</p>
          ) : periods.length === 0 ? (
            <p className="px-2 py-3 text-center text-xs text-muted-foreground">
              {t('installmentPlans.periods.empty')}
            </p>
          ) : (
            <div className="max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <tbody>
                  {periods.map((p) => (
                    <tr key={p.id} className="border-b border-border/30 last:border-0">
                      <td className="whitespace-nowrap py-1.5 pr-2 text-muted-foreground">#{p.period_no}</td>
                      <td className="whitespace-nowrap py-1.5 pr-2 text-muted-foreground">
                        {formatDate(p.due_at)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2 text-right tabular-nums">
                        {formatAmountTrimmed(p.principal_amount)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2 text-right tabular-nums text-muted-foreground">
                        +{formatAmountTrimmed(p.interest_amount)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2 text-right font-medium tabular-nums">
                        {formatAmountTrimmed(p.total_amount)}
                      </td>
                      <td className="whitespace-nowrap py-1.5 pr-2">
                        {p.status === 'refunded' ? (
                          <span
                            className="rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive"
                            title={p.refund_amount != null ? formatAmountTrimmed(p.refund_amount) : undefined}
                          >
                            {t('installmentPlans.periods.refunded', {
                              date: formatDate(p.refunded_at || p.due_at),
                            })}
                          </span>
                        ) : p.status === 'overridden' ? (
                          <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            {t('installmentPlans.periods.overridden')}
                          </span>
                        ) : null}
                      </td>
                      <td className="whitespace-nowrap py-1.5 text-right">
                        <button
                          type="button"
                          disabled={!canManage || isDone}
                          className="text-[11px] text-primary underline-offset-2 hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                          onClick={() => setEditingPeriod(p)}
                        >
                          {t('common.edit')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : null}

      {editingPeriod ? (
        <EditPeriodDialog
          period={editingPeriod}
          busy={busy}
          onClose={() => setEditingPeriod(null)}
          onConfirm={async (payload) => {
            setBusy(true)
            try {
              await onUpdatePeriod(editingPeriod.period_no, payload)
              setEditingPeriod(null)
            } finally {
              setBusy(false)
            }
          }}
        />
      ) : null}

      {rebalanceOpen ? (
        <RebalanceDialog
          maxPeriodNo={plan.periods}
          defaultPeriodNo={rebalanceDefaultPeriodNo}
          busy={busy}
          onClose={() => setRebalanceOpen(false)}
          onConfirm={async (periodNo, payload) => {
            setBusy(true)
            try {
              await onRebalance(periodNo, payload)
              setRebalanceOpen(false)
            } finally {
              setBusy(false)
            }
          }}
        />
      ) : null}

      {earlyRepayOpen ? (
        <EarlyRepayDialog
          busy={busy}
          onClose={() => setEarlyRepayOpen(false)}
          onConfirm={async (payload) => {
            setBusy(true)
            try {
              await onEarlyRepay(payload)
              setEarlyRepayOpen(false)
            } finally {
              setBusy(false)
            }
          }}
        />
      ) : null}

      <ConfirmDialog
        open={pendingPayoff}
        onCancel={() => setPendingPayoff(false)}
        onConfirm={async () => {
          setBusy(true)
          try {
            await onPayoff()
            setPendingPayoff(false)
          } finally {
            setBusy(false)
          }
        }}
        loading={busy}
        title={t('installmentPlans.payoff.title')}
        description={t('installmentPlans.payoff.confirm')}
        confirmText={t('installmentPlans.button.payoff')}
        confirmVariant="destructive"
      />

      <ConfirmDialog
        open={pendingTerminate}
        onCancel={() => setPendingTerminate(false)}
        onConfirm={async () => {
          setBusy(true)
          try {
            await onTerminateFuture()
            setPendingTerminate(false)
          } finally {
            setBusy(false)
          }
        }}
        loading={busy}
        title={t('installmentPlans.terminateFuture.title')}
        description={t('installmentPlans.terminateFuture.confirm')}
        confirmText={t('installmentPlans.button.terminateFuture')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function EditPeriodDialog({
  period,
  busy,
  onClose,
  onConfirm,
}: {
  period: ReadInstallmentPeriod
  busy: boolean
  onClose: () => void
  onConfirm: (payload: InstallmentPeriodUpdatePayload) => void
}) {
  const t = useT()
  const [amount, setAmount] = useState(String(period.total_amount))
  const [note, setNote] = useState('')

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>
            {t('installmentPlans.periods.editTitle').replace('{no}', String(period.period_no))}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('transactions.table.amount')}</Label>
            <Input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              onConfirm({
                amount: Number(amount) > 0 ? Number(amount) : undefined,
                note: note || null,
              })
            }
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function RebalanceDialog({
  maxPeriodNo,
  defaultPeriodNo,
  busy,
  onClose,
  onConfirm,
}: {
  maxPeriodNo: number
  /** 從交易詳情「修改連同未來分期」跳轉過來時,預填該筆交易對應的期数
   *  (2026-08-03 使用者反饋 #4)。 */
  defaultPeriodNo?: number
  busy: boolean
  onClose: () => void
  onConfirm: (periodNo: number, payload: InstallmentRebalancePayload) => void
}) {
  const t = useT()
  const [periodNo, setPeriodNo] = useState(defaultPeriodNo ? String(defaultPeriodNo) : '1')
  const [rate, setRate] = useState('0')

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('installmentPlans.button.rebalance')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('installmentPlans.rebalance.fromPeriod')}</Label>
            <Input
              type="number"
              min={1}
              max={maxPeriodNo}
              value={periodNo}
              onChange={(e) => setPeriodNo(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('installmentPlans.field.interestRate')}</Label>
            <Input type="number" min="0" step="0.001" value={rate} onChange={(e) => setRate(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            disabled={busy}
            onClick={() => onConfirm(Math.max(1, Math.round(Number(periodNo)) || 1), { interest_rate: Number(rate) || 0 })}
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EarlyRepayDialog({
  busy,
  onClose,
  onConfirm,
}: {
  busy: boolean
  onClose: () => void
  onConfirm: (payload: InstallmentEarlyRepayPayload) => void
}) {
  const t = useT()
  const [amount, setAmount] = useState('')

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('installmentPlans.button.earlyRepay')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('installmentPlans.earlyRepay.amount')}</Label>
            <Input
              type="number"
              min="0.01"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            disabled={busy || !(Number(amount) > 0)}
            onClick={() => onConfirm({ payment_amount: Number(amount) })}
          >
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
