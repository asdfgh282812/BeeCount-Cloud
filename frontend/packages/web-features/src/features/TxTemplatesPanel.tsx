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

import type { ReadAccount, ReadTxTemplate, TxTemplateApplyPayload, WorkspaceCategory } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { TxTemplateForm } from '../forms'
import { txTemplateDefaults } from '../forms'

type TxTemplatesPanelProps = {
  templates: readonly ReadTxTemplate[]
  categories: readonly WorkspaceCategory[]
  accounts: readonly ReadAccount[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  form: TxTemplateForm
  onFormChange: (next: TxTemplateForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (template: ReadTxTemplate) => Promise<void> | void
  onApply: (template: ReadTxTemplate, payload: TxTemplateApplyPayload) => Promise<void> | void
  /** 账本 owner 才能新建/编辑/删除範本(server `_OWNER_ONLY_ROLES`)。套用範本
   *  走一般交易写权限,不受这个开关限制。 */
  canManage: boolean
}

/**
 * 交易範本面板(MOZE_FEATURE_GAP_SD.md §2.7 Phase 3)。存一组常用的
 * tx_type/amount/category/account 组合,「套用」直接把内容套成一笔新交易
 * (金额/备注可在套用时临时覆盖,其余栏位固定沿用範本)。
 */
export function TxTemplatesPanel({
  templates,
  categories,
  accounts,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  onApply,
  canManage,
}: TxTemplatesPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadTxTemplate | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [applyTarget, setApplyTarget] = useState<ReadTxTemplate | null>(null)
  // 主帳戶(§2.9,2026-08-02 群組模型):account_group 是純管理容器,不能被
  // 範本套用挂账(後端 _assert_account_not_group 會拒),選擇器不該讓它出現。
  const tradableAccounts = accounts.filter((a) => a.account_type !== 'account_group')
  const [applyAt, setApplyAt] = useState('')
  const [applyAmount, setApplyAmount] = useState('')
  const [applyNote, setApplyNote] = useState('')
  const [applying, setApplying] = useState(false)

  const isTransfer = form.tx_type === 'transfer'
  const categoryKind = form.tx_type === 'income' ? 'income' : 'expense'

  const handleOpenCreate = () => {
    onFormChange(txTemplateDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (template: ReadTxTemplate) => {
    onFormChange({
      editingId: template.id,
      name: template.name,
      tx_type: (template.tx_type as TxTemplateForm['tx_type']) || 'expense',
      amount: String(template.amount),
      note: template.note || '',
      category_id: template.category_id || '',
      category_name: template.category_name || '',
      account_id: template.account_id || '',
      account_name: template.account_name || '',
      from_account_id: template.from_account_id || '',
      from_account_name: template.from_account_name || '',
      to_account_id: template.to_account_id || '',
      to_account_name: template.to_account_name || '',
      sort_order: String(template.sort_order ?? 0),
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

  const openApplyDialog = (template: ReadTxTemplate) => {
    setApplyTarget(template)
    setApplyAt(nowLocalInput())
    setApplyAmount(String(template.amount))
    setApplyNote(template.note || '')
  }

  const handleConfirmApply = async () => {
    if (!applyTarget) return
    setApplying(true)
    try {
      await onApply(applyTarget, {
        happened_at: new Date(applyAt).toISOString(),
        amount: Number(applyAmount) || null,
        note: applyNote || null,
      })
      setApplyTarget(null)
    } finally {
      setApplying(false)
    }
  }

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === categoryKind),
    [categories, categoryKind],
  )

  const canSubmit =
    Boolean(form.name.trim()) &&
    Boolean(form.amount.trim()) &&
    Number(form.amount) > 0 &&
    (isTransfer ? Boolean(form.from_account_id) && Boolean(form.to_account_id) : true)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('txTemplates.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('txTemplates.button.create')}
        </Button>
      </div>

      {templates.length === 0 ? (
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
              <rect x="4" y="3" width="16" height="18" rx="2" />
              <path d="M8 8h8M8 12h8M8 16h5" />
            </svg>
          }
          title={t('txTemplates.empty')}
          description={t('txTemplates.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {templates.map((template) => {
            const cat = template.category_id
              ? categories.find((c) => c.id === template.category_id)
              : null
            return (
              <div
                key={template.id}
                className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <span
                    aria-hidden
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    {template.tx_type === 'transfer' ? (
                      <span className="material-symbols-outlined text-2xl">swap_horiz</span>
                    ) : (
                      <CategoryIcon
                        icon={cat?.icon}
                        iconType={cat?.icon_type || 'material'}
                        iconCloudFileId={cat?.icon_cloud_file_id || null}
                        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                        size={24}
                      />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold">{template.name}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {template.tx_type === 'transfer'
                        ? `${template.from_account_name || ''} → ${template.to_account_name || ''}`
                        : [template.category_name, template.account_name].filter(Boolean).join(' · ')}
                    </div>
                    {template.note ? (
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {template.note}
                      </div>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-right">
                    <Amount value={template.amount} currency={currency} size="md" bold tone="default" />
                  </div>
                </div>

                <div className="mt-3 flex items-center justify-end gap-2">
                  <Button size="sm" onClick={() => openApplyDialog(template)}>
                    {t('txTemplates.button.apply')}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenEdit(template)}>
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canManage}
                    onClick={() => setPendingDelete(template)}
                  >
                    {t('common.delete')}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('txTemplates.button.update') : t('txTemplates.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
            <div className="space-y-1">
              <Label>{t('txTemplates.field.name')}</Label>
              <Input
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
                placeholder={t('txTemplates.placeholder.name')}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('transactions.table.type')}</Label>
              <Select
                value={form.tx_type}
                onValueChange={(value) =>
                  onFormChange({
                    ...form,
                    tx_type: value as TxTemplateForm['tx_type'],
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
                    value={form.from_account_id || '__none__'}
                    onValueChange={(value) => {
                      if (value === '__none__') {
                        onFormChange({ ...form, from_account_id: '', from_account_name: '' })
                        return
                      }
                      const acc = accounts.find((a) => a.id === value)
                      onFormChange({ ...form, from_account_id: value, from_account_name: acc?.name || '' })
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
                  <Label>{t('transactions.placeholder.toAccountName')}</Label>
                  <Select
                    value={form.to_account_id || '__none__'}
                    onValueChange={(value) => {
                      if (value === '__none__') {
                        onFormChange({ ...form, to_account_id: '', to_account_name: '' })
                        return
                      }
                      const acc = accounts.find((a) => a.id === value)
                      onFormChange({ ...form, to_account_id: value, to_account_name: acc?.name || '' })
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
                  <Label>{t('installmentPlans.field.account')}</Label>
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
                      {tradableAccounts.map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

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
              {form.editingId ? t('txTemplates.button.update') : t('txTemplates.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 套用範本 dialog */}
      <Dialog open={applyTarget !== null} onOpenChange={(next) => !next && setApplyTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('txTemplates.apply.title')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('recurringRules.field.nextRunAt')}</Label>
              <Input type="datetime-local" value={applyAt} onChange={(e) => setApplyAt(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('budgets.field.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={applyAmount}
                onChange={(e) => setApplyAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input value={applyNote} onChange={(e) => setApplyNote(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={applying} onClick={() => setApplyTarget(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={applying} onClick={() => void handleConfirmApply()}>
              {t('txTemplates.button.apply')}
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
          onFormChange({
            ...form,
            category_id: cat.id,
            category_name: cat.name,
          })
        }
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('txTemplates.delete.title')}
        description={t('txTemplates.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function nowLocalInput(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
