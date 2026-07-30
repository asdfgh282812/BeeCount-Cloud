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

import type { ReadAccount, ReadRecurringRule, WorkspaceCategory } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { RecurringRuleForm } from '../forms'
import { recurringRuleDefaults } from '../forms'

type RecurringRulesPanelProps = {
  rules: readonly ReadRecurringRule[]
  categories: readonly WorkspaceCategory[]
  accounts: readonly ReadAccount[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  form: RecurringRuleForm
  onFormChange: (next: RecurringRuleForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (rule: ReadRecurringRule) => Promise<void> | void
  onToggleEnabled: (rule: ReadRecurringRule, enabled: boolean) => Promise<void> | void
  /** 账本 owner 才能写(server _OWNER_ONLY_ROLES),非 owner 时按钮禁用。 */
  canManage: boolean
}

/**
 * 週期性收支规则管理面板(MOZE_FEATURE_GAP_SD.md §2.2)。
 *
 * 到期后由 server `recurring_materializer` 定时任务(15 分钟一次)自动生成
 * 交易并推进 next_run_at —— 这个面板只负责规则本身的增删改查 + 启停开关,
 * 不提供"立即生成"操作。
 */
export function RecurringRulesPanel({
  rules,
  categories,
  accounts,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  onToggleEnabled,
  canManage,
}: RecurringRulesPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadRecurringRule | null>(null)
  const [deleting, setDeleting] = useState(false)

  const isTransfer = form.tx_type === 'transfer'
  const categoryKind = form.tx_type === 'income' ? 'income' : 'expense'

  const handleOpenCreate = () => {
    onFormChange(recurringRuleDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (rule: ReadRecurringRule) => {
    const account = rule.account_id ? accounts.find((a) => a.id === rule.account_id) : null
    const fromAccount = rule.from_account_id
      ? accounts.find((a) => a.id === rule.from_account_id)
      : null
    const toAccount = rule.to_account_id ? accounts.find((a) => a.id === rule.to_account_id) : null
    onFormChange({
      editingId: rule.id,
      tx_type: (rule.tx_type as RecurringRuleForm['tx_type']) || 'expense',
      amount: String(rule.amount),
      note: rule.note || '',
      category_id: rule.category_id || '',
      category_name: rule.category_name || '',
      account_id: rule.account_id || '',
      account_name: account?.name || '',
      from_account_id: rule.from_account_id || '',
      from_account_name: fromAccount?.name || '',
      to_account_id: rule.to_account_id || '',
      to_account_name: toAccount?.name || '',
      frequency: rule.frequency,
      interval: String(rule.interval),
      next_run_at: isoToLocalInput(rule.next_run_at),
      end_at: rule.end_at ? isoToLocalInput(rule.end_at) : '',
      enabled: rule.enabled,
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

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === categoryKind),
    [categories, categoryKind],
  )

  const canSubmit =
    Boolean(form.amount.trim()) &&
    Boolean(form.next_run_at.trim()) &&
    (isTransfer ? Boolean(form.from_account_id) && Boolean(form.to_account_id) : true)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('recurringRules.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('recurringRules.button.create')}
        </Button>
      </div>

      {rules.length === 0 ? (
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
              <path d="M21 12a9 9 0 1 1-3-6.7" />
              <path d="M21 3v6h-6" />
            </svg>
          }
          title={t('recurringRules.empty')}
          description={t('recurringRules.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => {
            const cat = rule.category_id
              ? categories.find((c) => c.id === rule.category_id)
              : null
            return (
              <RecurringRuleCard
                key={rule.id}
                rule={rule}
                category={cat || null}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                canManage={canManage}
                onEdit={() => handleOpenEdit(rule)}
                onDelete={() => setPendingDelete(rule)}
                onToggleEnabled={(next) => void onToggleEnabled(rule, next)}
              />
            )
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('recurringRules.button.update') : t('recurringRules.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            <div className="space-y-1">
              <Label>{t('transactions.table.type')}</Label>
              <Select
                value={form.tx_type}
                disabled={!!form.editingId}
                onValueChange={(value) =>
                  onFormChange({
                    ...form,
                    tx_type: value as RecurringRuleForm['tx_type'],
                    category_id: '',
                    category_name: '',
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
                  <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
                  <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label>{t('budgets.field.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                placeholder="0"
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
            </div>

            {isTransfer ? (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.fromAccountName')}</Label>
                  <Select
                    value={form.from_account_id || undefined}
                    onValueChange={(value) => {
                      const acc = accounts.find((a) => a.id === value)
                      onFormChange({ ...form, from_account_id: value, from_account_name: acc?.name || '' })
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accounts.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.toAccountName')}</Label>
                  <Select
                    value={form.to_account_id || undefined}
                    onValueChange={(value) => {
                      const acc = accounts.find((a) => a.id === value)
                      onFormChange({ ...form, to_account_id: value, to_account_name: acc?.name || '' })
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t('transactions.placeholder.accountName')} />
                    </SelectTrigger>
                    <SelectContent>
                      {accounts.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            ) : (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.table.category')}</Label>
                  <button
                    type="button"
                    onClick={() => setCategoryPickerOpen(true)}
                    className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
                  >
                    <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                      {form.category_name || t('transactions.placeholder.categoryName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.table.account')}</Label>
                  <Select
                    value={form.account_id || '__none__'}
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
              </>
            )}

            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>{t('recurringRules.field.frequency')}</Label>
                <Select
                  value={form.frequency}
                  onValueChange={(value) =>
                    onFormChange({ ...form, frequency: value as RecurringRuleForm['frequency'] })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="daily">{t('recurringRules.frequency.daily')}</SelectItem>
                    <SelectItem value="weekly">{t('recurringRules.frequency.weekly')}</SelectItem>
                    <SelectItem value="monthly">{t('recurringRules.frequency.monthly')}</SelectItem>
                    <SelectItem value="yearly">{t('recurringRules.frequency.yearly')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>{t('recurringRules.field.interval')}</Label>
                <Input
                  type="number"
                  inputMode="numeric"
                  min="1"
                  max="365"
                  value={form.interval}
                  onChange={(e) => onFormChange({ ...form, interval: e.target.value })}
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('recurringRules.field.nextRunAt')}</Label>
              <Input
                type="datetime-local"
                value={form.next_run_at}
                onChange={(e) => onFormChange({ ...form, next_run_at: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('recurringRules.field.endAt')}</Label>
              <Input
                type="datetime-local"
                value={form.end_at}
                onChange={(e) => onFormChange({ ...form, end_at: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <p className="text-sm font-medium">{t('recurringRules.field.enabled')}</p>
              <button
                type="button"
                role="switch"
                aria-checked={form.enabled}
                onClick={() => onFormChange({ ...form, enabled: !form.enabled })}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                  form.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    form.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId ? t('recurringRules.button.update') : t('recurringRules.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind={categoryKind}
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
        title={t('recurringRules.delete.title')}
        description={t('recurringRules.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function RecurringRuleCard({
  rule,
  category,
  currency,
  iconPreviewUrlByFileId,
  canManage,
  onEdit,
  onDelete,
  onToggleEnabled,
}: {
  rule: ReadRecurringRule
  category: WorkspaceCategory | null
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  canManage: boolean
  onEdit: () => void
  onDelete: () => void
  onToggleEnabled: (next: boolean) => void
}) {
  const t = useT()
  const title =
    rule.tx_type === 'transfer'
      ? t('enum.txType.transfer')
      : category?.name || rule.category_name || t('budgets.label.unknownCategory')

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          {rule.tx_type === 'transfer' ? (
            <span className="material-symbols-outlined text-2xl">sync_alt</span>
          ) : (
            <CategoryIcon
              icon={category?.icon}
              iconType={category?.icon_type || 'material'}
              iconCloudFileId={category?.icon_cloud_file_id || null}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              size={24}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{title}</span>
            {!rule.enabled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('recurringRules.disabled')}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {t(`recurringRules.frequency.${rule.frequency}`)}
            {rule.interval > 1 ? ` ×${rule.interval}` : ''} ·{' '}
            {t('recurringRules.label.nextRun')} {formatLocalDate(rule.next_run_at)}
          </div>
          {rule.note ? (
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{rule.note}</div>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={rule.amount} currency={currency} size="md" bold tone="default" />
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <button
          type="button"
          role="switch"
          aria-checked={rule.enabled}
          disabled={!canManage}
          onClick={() => onToggleEnabled(!rule.enabled)}
          className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
            rule.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
              rule.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
            }`}
          />
        </button>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
            {t('common.edit')}
          </Button>
          <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
            {t('common.delete')}
          </Button>
        </div>
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

function formatLocalDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString()
}
