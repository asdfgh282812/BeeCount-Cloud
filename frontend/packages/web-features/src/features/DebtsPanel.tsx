import { useState } from 'react'

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
  useT,
} from '@beecount/ui'

import type { ReadAccount, ReadDebt } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { DebtForm } from '../forms'
import { debtDefaults } from '../forms'

export type RepaymentPayload = {
  amount: number
  account_id: string | null
  happened_at: string
  note: string | null
}

type DebtsPanelProps = {
  debts: readonly ReadDebt[]
  accounts: readonly ReadAccount[]
  currency: string
  form: DebtForm
  onFormChange: (next: DebtForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (debt: ReadDebt) => Promise<void> | void
  onRecordRepayment: (debt: ReadDebt, payload: RepaymentPayload) => Promise<void> | void
  /** 雙向勾稽(體驗補強):點還款記錄跳去對應交易詳情。 */
  onJumpToTx: (txId: string) => void
  /** 結案/重新開啟(體驗補強):不一定要還清全額才算結束,PATCH 帶
   *  closed_at(結案)/清空 closed_at(重新開啟)。 */
  onCloseDebt: (debt: ReadDebt) => Promise<void> | void
  onReopenDebt: (debt: ReadDebt) => Promise<void> | void
  /** 從交易詳情跳轉過來時,要高亮的欠款 id。 */
  highlightDebtId?: string | null
  /** 账本 owner 才能新建/编辑/删除欠款(server `_OWNER_ONLY_ROLES`)。还款/
   *  收款走一般交易写权限,跟 owner-only 无关,不受这个开关限制。 */
  canManage: boolean
}

/**
 * 借還款追蹤面板(MOZE_FEATURE_GAP_SD.md §2.5 Phase 3)。
 *
 * `remaining_amount`/`status` 由 server 从反查交易即时算出,不在这里做任何
 * 客户端累加。还款/收款走独立的小 dialog(内部只是建一笔带 `debt_id` 的
 * 普通交易),不复用主交易表单 —— 欠款关联对一般记账场景是小众需求,不值得
 * 在已经很复杂的 TransactionsPanel 里再加一个字段。
 */
export function DebtsPanel({
  debts,
  accounts,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  onRecordRepayment,
  onJumpToTx,
  onCloseDebt,
  onReopenDebt,
  highlightDebtId,
  canManage,
}: DebtsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadDebt | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [pendingClose, setPendingClose] = useState<ReadDebt | null>(null)
  const [closing, setClosing] = useState(false)
  const [reopeningId, setReopeningId] = useState<string | null>(null)
  const [repaymentTarget, setRepaymentTarget] = useState<ReadDebt | null>(null)
  const [repaymentAmount, setRepaymentAmount] = useState('')
  const [repaymentAccountId, setRepaymentAccountId] = useState('')
  const [repaymentAt, setRepaymentAt] = useState('')
  const [repaymentNote, setRepaymentNote] = useState('')
  const [recordingRepayment, setRecordingRepayment] = useState(false)
  const [repaymentAccountPickerOpen, setRepaymentAccountPickerOpen] = useState(false)
  const accountNameById = new Map(accounts.map((a) => [a.id, a.name]))

  const handleOpenCreate = () => {
    onFormChange(debtDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (debt: ReadDebt) => {
    onFormChange({
      editingId: debt.id,
      direction: debt.direction,
      counterparty_name: debt.counterparty_name,
      principal_amount: String(debt.principal_amount),
      due_at: debt.due_at ? isoToDateInput(debt.due_at) : '',
      note: debt.note || '',
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

  const openRepaymentDialog = (debt: ReadDebt) => {
    setRepaymentTarget(debt)
    setRepaymentAmount(String(debt.remaining_amount > 0 ? debt.remaining_amount : ''))
    setRepaymentAccountId('')
    setRepaymentAt(nowLocalInput())
    setRepaymentNote('')
  }

  const handleConfirmRepayment = async () => {
    if (!repaymentTarget) return
    const amount = Number(repaymentAmount)
    if (!Number.isFinite(amount) || amount <= 0) return
    setRecordingRepayment(true)
    try {
      await onRecordRepayment(repaymentTarget, {
        amount,
        account_id: repaymentAccountId || null,
        happened_at: new Date(repaymentAt).toISOString(),
        note: repaymentNote || null,
      })
      setRepaymentTarget(null)
    } finally {
      setRecordingRepayment(false)
    }
  }

  const handleConfirmClose = async () => {
    if (!pendingClose) return
    setClosing(true)
    try {
      await onCloseDebt(pendingClose)
      setPendingClose(null)
    } finally {
      setClosing(false)
    }
  }

  const handleReopen = async (debt: ReadDebt) => {
    setReopeningId(debt.id)
    try {
      await onReopenDebt(debt)
    } finally {
      setReopeningId(null)
    }
  }

  const canSubmit =
    Boolean(form.counterparty_name.trim()) &&
    Boolean(form.principal_amount.trim()) &&
    Number(form.principal_amount) > 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('debts.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('debts.button.create')}
        </Button>
      </div>

      {debts.length === 0 ? (
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
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          }
          title={t('debts.empty')}
          description={t('debts.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {debts.map((debt) => (
            <DebtCard
              key={debt.id}
              debt={debt}
              currency={currency}
              canManage={canManage}
              highlighted={Boolean(highlightDebtId) && debt.id === highlightDebtId}
              onEdit={() => handleOpenEdit(debt)}
              onDelete={() => setPendingDelete(debt)}
              onRecordRepayment={() => openRepaymentDialog(debt)}
              onJumpToTx={onJumpToTx}
              onClose={() => setPendingClose(debt)}
              onReopen={() => void handleReopen(debt)}
              reopening={reopeningId === debt.id}
            />
          ))}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('debts.button.update') : t('debts.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('debts.field.direction')}</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  disabled={!!form.editingId}
                  onClick={() => onFormChange({ ...form, direction: 'payable' })}
                  className={[
                    'flex flex-col items-center gap-1 rounded-md border px-3 py-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    form.direction === 'payable'
                      ? 'border-primary/60 bg-primary/10 text-primary'
                      : 'border-border/60 hover:bg-accent/40',
                  ].join(' ')}
                >
                  {t('debts.direction.payable')}
                </button>
                <button
                  type="button"
                  disabled={!!form.editingId}
                  onClick={() => onFormChange({ ...form, direction: 'receivable' })}
                  className={[
                    'flex flex-col items-center gap-1 rounded-md border px-3 py-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    form.direction === 'receivable'
                      ? 'border-primary/60 bg-primary/10 text-primary'
                      : 'border-border/60 hover:bg-accent/40',
                  ].join(' ')}
                >
                  {t('debts.direction.receivable')}
                </button>
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('debts.field.counterparty')}</Label>
              <Input
                value={form.counterparty_name}
                onChange={(e) => onFormChange({ ...form, counterparty_name: e.target.value })}
                placeholder={t('debts.placeholder.counterparty')}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('debts.field.principalAmount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                placeholder="0"
                disabled={!!form.editingId}
                value={form.principal_amount}
                onChange={(e) => onFormChange({ ...form, principal_amount: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('debts.field.dueAt')}</Label>
              <Input
                type="date"
                value={form.due_at}
                onChange={(e) => onFormChange({ ...form, due_at: e.target.value })}
              />
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
              {form.editingId ? t('debts.button.update') : t('debts.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 还款/收款 dialog */}
      <Dialog open={repaymentTarget !== null} onOpenChange={(next) => !next && setRepaymentTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {repaymentTarget?.direction === 'receivable'
                ? t('debts.repayment.titleReceive')
                : t('debts.repayment.titlePay')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('budgets.field.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={repaymentAmount}
                onChange={(e) => setRepaymentAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('installmentPlans.field.account')}</Label>
              <button
                type="button"
                onClick={() => setRepaymentAccountPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
              >
                <span className={`flex-1 truncate ${repaymentAccountId ? '' : 'text-muted-foreground'}`}>
                  {repaymentAccountId
                    ? accountNameById.get(repaymentAccountId) || repaymentAccountId
                    : t('transactions.placeholder.accountName')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
            <div className="space-y-1">
              <Label>{t('recurringRules.field.nextRunAt')}</Label>
              <Input
                type="datetime-local"
                value={repaymentAt}
                onChange={(e) => setRepaymentAt(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input value={repaymentNote} onChange={(e) => setRepaymentNote(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={recordingRepayment}
              onClick={() => setRepaymentTarget(null)}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={recordingRepayment || !repaymentAmount.trim() || Number(repaymentAmount) <= 0}
              onClick={() => void handleConfirmRepayment()}
            >
              {t('debts.repayment.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AccountPickerDialog
        open={repaymentAccountPickerOpen}
        onClose={() => setRepaymentAccountPickerOpen(false)}
        accounts={accounts}
        value={repaymentAccountId ? accountNameById.get(repaymentAccountId) || '' : ''}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount')}
        title={t('installmentPlans.field.account')}
        onSelect={(row) => setRepaymentAccountId(row.id)}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('debts.delete.title')}
        description={t('debts.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />

      <ConfirmDialog
        open={pendingClose !== null}
        onCancel={() => {
          if (!closing) setPendingClose(null)
        }}
        onConfirm={() => void handleConfirmClose()}
        loading={closing}
        title={t('debts.confirm.closeTitle')}
        description={
          pendingClose
            ? t('debts.confirm.closeDescription', {
                amount: pendingClose.remaining_amount.toLocaleString('zh-CN', {
                  minimumFractionDigits: 0,
                  maximumFractionDigits: 2,
                }),
              })
            : ''
        }
        confirmText={t('debts.button.close')}
      />
    </div>
  )
}

function DebtCard({
  debt,
  currency,
  canManage,
  highlighted,
  onEdit,
  onDelete,
  onRecordRepayment,
  onJumpToTx,
  onClose,
  onReopen,
  reopening,
}: {
  debt: ReadDebt
  currency: string
  canManage: boolean
  highlighted: boolean
  onEdit: () => void
  onDelete: () => void
  onRecordRepayment: () => void
  onJumpToTx: (txId: string) => void
  onClose: () => void
  onReopen: () => void
  reopening: boolean
}) {
  const t = useT()
  const ratio = debt.principal_amount > 0
    ? Math.min((debt.principal_amount - debt.remaining_amount) / debt.principal_amount, 1)
    : 0
  const isClosed = debt.status === 'closed'
  const barColor = isClosed
    ? 'bg-muted-foreground/60'
    : debt.status === 'settled' ? 'bg-green-500' : debt.status === 'partial' ? 'bg-orange-500' : 'bg-muted-foreground/40'
  const hasRepayments = debt.repayments.length > 0

  return (
    <div
      id={`debt-${debt.id}`}
      className={[
        'rounded-xl border bg-card p-4 transition hover:border-primary/40 hover:shadow-sm',
        highlighted ? 'border-primary ring-2 ring-primary/40' : 'border-border/60',
      ].join(' ')}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
        >
          <span className="material-symbols-outlined text-2xl">
            {debt.direction === 'receivable' ? 'call_received' : 'call_made'}
          </span>
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">{debt.counterparty_name}</span>
            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {t(`debts.direction.${debt.direction}`)}
            </span>
            <span
              className={[
                'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                isClosed
                  ? 'bg-muted text-muted-foreground'
                  : debt.status === 'settled'
                    ? 'bg-green-500/15 text-green-600'
                    : debt.status === 'partial'
                      ? 'bg-orange-500/15 text-orange-600'
                      : 'bg-muted text-muted-foreground',
              ].join(' ')}
            >
              {t(`debts.status.${debt.status}`)}
            </span>
          </div>
          {debt.due_at ? (
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              {t('debts.label.dueAt')} {formatDateOnlyUTC(debt.due_at)}
            </div>
          ) : null}
          {debt.note ? (
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{debt.note}</div>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={debt.remaining_amount} currency={currency} size="md" bold tone="default" />
          <div className="text-[11px] text-muted-foreground">
            {t('debts.label.principal')}{' '}
            <Amount value={debt.principal_amount} currency={currency} size="sm" tone="muted" />
          </div>
        </div>
      </div>

      <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
        <div className={`h-full transition-all ${barColor}`} style={{ width: `${ratio * 100}%` }} />
      </div>

      {hasRepayments ? (
        <div className="mt-2 space-y-1">
          <div className="text-[11px] font-medium text-muted-foreground">
            {t('debts.label.repayments')}
          </div>
          {debt.repayments.slice(0, 5).map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => onJumpToTx(r.id)}
              title={t('debts.label.linkedTx.jumpHint')}
              className="flex w-full items-center justify-between rounded px-1 py-0.5 text-[11px] text-muted-foreground transition hover:bg-accent/40 hover:text-primary"
            >
              <span>{new Date(r.happened_at).toLocaleDateString()}</span>
              <Amount value={r.amount} currency={currency} size="xs" tone="muted" />
            </button>
          ))}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={debt.status === 'settled' || isClosed}
          onClick={onRecordRepayment}
        >
          {debt.direction === 'receivable' ? t('debts.button.recordReceive') : t('debts.button.recordPay')}
        </Button>
        {isClosed ? (
          <Button size="sm" variant="outline" disabled={!canManage || reopening} onClick={onReopen}>
            {t('debts.button.reopen')}
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled={!canManage} onClick={onClose}>
            {t('debts.button.close')}
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!canManage || hasRepayments}
          title={hasRepayments ? t('debts.delete.blockedByRepayments') : undefined}
          onClick={onDelete}
        >
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}

function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function nowLocalInput(): string {
  return isoToLocalInput(new Date().toISOString())
}

/**
 * 到期日只存日期(體驗補強):`due_at` 語意是純日曆日期(UTC 零點落庫),
 * 用 **UTC** getter 取值餵給 `<input type="date">` —— 若沿用本地時區的
 * `Date` 方法會有少一天的回填 bug。`due_at` 从後端回來時是不帶時區位移的
 * naive datetime 字串,`new Date("...T00:00:00")` 沒有位移標記時 JS 會當
 * 「本地時間」解析,UTC+8 使用者反而會多踩一天(本地 2026-08-02 被換成
 * 2026-08-01T16:00:00Z),不限 UTC 負時區——缺位移標記時補上 "Z" 強制當
 * UTC 解析,對齊「這串日期本來就是 UTC」的後端語意。
 */
function forceUtcDate(iso: string): Date {
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  return new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
}

function isoToDateInput(iso: string): string {
  const d = forceUtcDate(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

/** 同 `isoToDateInput` 的理由,展示到期日文字時同样用 UTC getter,不用
 *  `toLocaleDateString()`(那个走本地时区,会有同款少一天风险)。 */
function formatDateOnlyUTC(iso: string): string {
  const d = forceUtcDate(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}
