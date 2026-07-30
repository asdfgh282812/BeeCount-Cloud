import { useMemo, useState } from 'react'

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

import type { ReadAccount, ReadInstallmentPlan, WorkspaceCategory } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { InstallmentPlanForm } from '../forms'
import { installmentPlanDefaults } from '../forms'

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
  onSettle: (plan: ReadInstallmentPlan) => Promise<void> | void
  /** 账本 owner 才能写(server _OWNER_ONLY_ROLES),非 owner 时按钮禁用。 */
  canManage: boolean
}

/**
 * 分期付款计划管理面板(MOZE_FEATURE_GAP_SD.md §2.3)。
 *
 * 建计画时 server 会同事务生成第一期交易;剩余各期由后端定时任务
 * (跟 §2.2 週期性收支共用同一个 worker)按月自动推进,这里只做计画本身的
 * 增删 + 提前结清,不提供"手动生成下一期"操作。
 * 创建后期数 / 总额 / 首期日不可改(server 约束,改这些走删除重建)。
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
  onSettle,
  canManage,
}: InstallmentPlansPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadInstallmentPlan | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [pendingSettle, setPendingSettle] = useState<ReadInstallmentPlan | null>(null)
  const [settling, setSettling] = useState(false)

  const handleOpenCreate = () => {
    onFormChange(installmentPlanDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (plan: ReadInstallmentPlan) => {
    const account = plan.account_id ? accounts.find((a) => a.id === plan.account_id) : null
    const cat = plan.category_id ? categories.find((c) => c.id === plan.category_id) : null
    onFormChange({
      editingId: plan.id,
      total_amount: String(plan.total_amount),
      periods: String(plan.periods),
      first_period_at: isoToLocalInput(plan.first_period_at),
      account_id: plan.account_id || '',
      account_name: account?.name || '',
      category_id: plan.category_id || '',
      category_name: cat?.name || plan.category_id || '',
      note: plan.note || '',
      status: plan.status,
    })
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

  const handleConfirmSettle = async () => {
    if (!pendingSettle) return
    setSettling(true)
    try {
      await onSettle(pendingSettle)
      setPendingSettle(null)
    } finally {
      setSettling(false)
    }
  }

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === 'expense'),
    [categories],
  )

  // 编辑模式下 total_amount/periods/first_period_at/category/account 都禁用
  // 输入(server 只允许改 note/status),所以只有新建模式需要这几个字段校验。
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
                onEdit={() => handleOpenEdit(plan)}
                onDelete={() => setPendingDelete(plan)}
                onSettle={() => setPendingSettle(plan)}
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
                max="120"
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
                  {accounts.map((a) => (
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

      <ConfirmDialog
        open={pendingSettle !== null}
        onCancel={() => {
          if (!settling) setPendingSettle(null)
        }}
        onConfirm={() => void handleConfirmSettle()}
        loading={settling}
        title={t('installmentPlans.settle.title')}
        description={t('installmentPlans.settle.confirm')}
        confirmText={t('installmentPlans.settle.confirmButton')}
        confirmVariant="default"
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
  onEdit,
  onDelete,
  onSettle,
}: {
  plan: ReadInstallmentPlan
  category: WorkspaceCategory | null
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  onEdit: () => void
  onDelete: () => void
  onSettle: () => void
}) {
  const t = useT()
  const title = category?.name || plan.category_id || t('budgets.label.unknownCategory')
  const isSettled = plan.status === 'settled'
  const ratio = plan.periods > 0 ? Math.min(plan.paid_periods / plan.periods, 1) : 0

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm">
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
            {isSettled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('installmentPlans.status.settled')}
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

      <div className="mt-3 flex items-center justify-end gap-2">
        {!isSettled ? (
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={onSettle}>
            {t('installmentPlans.button.settle')}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}

function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
