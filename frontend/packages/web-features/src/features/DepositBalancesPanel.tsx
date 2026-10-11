import { useEffect, useMemo, useState } from 'react'

import {
  AmountInput,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Label,
  useT
} from '@beecount/ui'

import type {
  DepositBalanceCompletePayload,
  DepositBalanceUpdatePayload,
  ReadAccount,
  ReadDepositBalance
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DatePicker } from '../components/DatePicker'
import { DepositBalanceCompleteDialog } from '../components/DepositBalanceCompleteDialog'
import {
  depositDateFromWire,
  depositDateToWire,
  isDepositBalanceOverdue,
  localTodayDateString,
  parseDepositAmount,
  sortCompletedDepositBalances,
  sortPendingDepositBalances
} from '../lib/depositBalance'

type DepositBalancesPanelProps = {
  balances: readonly ReadDepositBalance[]
  accounts: readonly ReadAccount[]
  currency: string
  /** 補尾款:server 同一次寫入建立真實交易。回傳 true = 成功。 */
  onComplete: (
    balance: ReadDepositBalance,
    payload: DepositBalanceCompletePayload
  ) => Promise<boolean>
  /** 編輯尾款金額/日期(只限 pending)。回傳 true = 成功。 */
  onUpdate: (
    balance: ReadDepositBalance,
    payload: DepositBalanceUpdatePayload
  ) => Promise<boolean>
  /** 刪除尾款追蹤(pending:訂金交易保留;completed:尾款交易保留)。 */
  onDelete: (balance: ReadDepositBalance) => Promise<void> | void
  /** 點「查看訂金/尾款交易」跳去對應交易詳情。 */
  onJumpToTx: (txId: string) => void
  /** 從通知/連結帶來的 `?highlight=<id>`:高亮並捲到該列。 */
  highlightId?: string | null
  /** 只有帳本 owner 能補/編輯/刪除(server `_OWNER_ONLY_ROLES`)。 */
  canManage: boolean
}

/**
 * 待完成交易(訂金尾款追蹤,doc.moze.app/record/deposit):待補尾款清單
 * (逾期標紅、未定日期排最後)+ 可展開的「已補上」清單。尾款補上前不是
 * 交易,所以不影響任何統計/餘額;金額與日期皆可「未定」。
 */
export function DepositBalancesPanel({
  balances,
  accounts,
  currency,
  onComplete,
  onUpdate,
  onDelete,
  onJumpToTx,
  highlightId,
  canManage
}: DepositBalancesPanelProps) {
  const t = useT()
  const [showCompleted, setShowCompleted] = useState(false)
  const [completeTarget, setCompleteTarget] = useState<ReadDepositBalance | null>(null)
  const [editTarget, setEditTarget] = useState<ReadDepositBalance | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReadDepositBalance | null>(null)
  const [deleting, setDeleting] = useState(false)

  const pending = useMemo(
    () => sortPendingDepositBalances(balances.filter((b) => b.status === 'pending')),
    [balances]
  )
  const completed = useMemo(
    () => sortCompletedDepositBalances(balances.filter((b) => b.status === 'completed')),
    [balances]
  )
  const today = localTodayDateString()

  // 要高亮的是已補上的那一筆時,自動展開已補上清單。
  useEffect(() => {
    if (!highlightId) return
    if (completed.some((b) => b.id === highlightId)) setShowCompleted(true)
  }, [highlightId, completed])

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
    } finally {
      setDeleting(false)
      setPendingDelete(null)
    }
  }

  const renderRow = (b: ReadDepositBalance) => {
    const isPending = b.status === 'pending'
    const overdue = isDepositBalanceOverdue(b, today)
    const due = depositDateFromWire(b.due_at)
    const title =
      b.source_merchant?.trim() ||
      b.source_category_name?.trim() ||
      b.source_note?.trim() ||
      (t('depositBalance.row.unknownSource') as string)
    const subtitle =
      b.source_merchant?.trim() && b.source_category_name?.trim() ? b.source_category_name : null
    return (
      <div
        key={b.id}
        id={`deposit-balance-${b.id}`}
        data-testid={`deposit-balance-row-${b.id}`}
        className={[
          'flex flex-col gap-2 px-3 py-2.5 transition hover:bg-accent/30 sm:flex-row sm:items-center',
          highlightId === b.id ? 'bg-primary/10 ring-1 ring-inset ring-primary/40' : ''
        ].join(' ')}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">{title}</span>
            {isPending && overdue ? (
              <span className="shrink-0 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive">
                {t('depositBalance.row.overdue')}
              </span>
            ) : null}
            {!isPending ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('depositBalance.row.completedBadge')}
              </span>
            ) : null}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
            {subtitle ? <span>{subtitle}</span> : null}
            {b.source_happened_at ? (
              <span>{new Date(b.source_happened_at).toLocaleDateString()}</span>
            ) : null}
            <span className={isPending && overdue ? 'text-destructive' : ''}>
              {due
                ? (t('depositBalance.row.due') as string).replace('{date}', due)
                : (t('depositBalance.row.noDate') as string)}
            </span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-4 text-right">
          <div>
            <div className="text-[11px] text-muted-foreground">
              {t('depositBalance.row.deposit')}
            </div>
            {b.source_amount != null ? (
              <Amount value={b.source_amount} currency={currency} size="sm" tone="muted" />
            ) : (
              <span className="text-xs text-muted-foreground">{t('common.dash')}</span>
            )}
          </div>
          <div>
            <div className="text-[11px] text-muted-foreground">
              {t('depositBalance.row.balance')}
            </div>
            {b.amount != null ? (
              <Amount value={b.amount} currency={currency} size="md" bold tone="negative" />
            ) : (
              <span className="text-sm text-muted-foreground">
                {t('depositBalance.row.undetermined')}
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          {isPending ? (
            <>
              <Button
                size="sm"
                data-testid={`deposit-balance-complete-${b.id}`}
                disabled={!canManage}
                onClick={() => setCompleteTarget(b)}
              >
                {t('depositBalance.action.complete')}
              </Button>
              <Button
                size="sm"
                variant="outline"
                data-testid={`deposit-balance-edit-${b.id}`}
                disabled={!canManage}
                onClick={() => setEditTarget(b)}
              >
                {t('common.edit')}
              </Button>
            </>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => onJumpToTx(b.source_tx_id)}>
            {t('depositBalance.action.viewDeposit')}
          </Button>
          {!isPending && b.completed_tx_id ? (
            <Button size="sm" variant="ghost" onClick={() => onJumpToTx(b.completed_tx_id!)}>
              {t('depositBalance.action.viewCompleted')}
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="ghost"
            className="text-destructive hover:text-destructive"
            data-testid={`deposit-balance-delete-${b.id}`}
            disabled={!canManage}
            onClick={() => setPendingDelete(b)}
          >
            {t('common.delete')}
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-16">
      <p className="text-xs text-muted-foreground">{t('depositBalance.desc')}</p>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold">
          {t('depositBalance.section.pending')}
          <span className="ml-1.5 text-xs font-normal text-muted-foreground">{pending.length}</span>
        </h3>
        {pending.length === 0 ? (
          <EmptyState title={t('depositBalance.empty') as string} />
        ) : (
          <div className="divide-y divide-border/40 rounded-lg border border-border/60">
            {pending.map(renderRow)}
          </div>
        )}
      </section>

      {completed.length > 0 ? (
        <section className="space-y-2">
          <button
            type="button"
            data-testid="deposit-balances-toggle-completed"
            aria-expanded={showCompleted}
            onClick={() => setShowCompleted((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground hover:text-foreground"
          >
            <span className="text-xs">{showCompleted ? '▾' : '▸'}</span>
            {t('depositBalance.section.completed')}
            <span className="text-xs font-normal">{completed.length}</span>
          </button>
          {showCompleted ? (
            <div className="divide-y divide-border/40 rounded-lg border border-border/60 opacity-90">
              {completed.map(renderRow)}
            </div>
          ) : null}
        </section>
      ) : null}

      <DepositBalanceCompleteDialog
        balance={completeTarget}
        accounts={accounts}
        onClose={() => setCompleteTarget(null)}
        onSubmit={onComplete}
      />

      <EditBalanceDialog
        balance={editTarget}
        onClose={() => setEditTarget(null)}
        onSubmit={onUpdate}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('depositBalance.delete.title') as string}
        description={
          (pendingDelete?.status === 'completed'
            ? t('depositBalance.delete.confirmCompleted')
            : t('depositBalance.delete.confirm')) as string
        }
        confirmText={t('common.delete') as string}
        cancelText={t('dialog.cancel') as string}
        confirmVariant="destructive"
      />
    </div>
  )
}

/** 編輯尾款:金額 / 預計日期,兩者都可清空(= 未定)。 */
function EditBalanceDialog({
  balance,
  onClose,
  onSubmit
}: {
  balance: ReadDepositBalance | null
  onClose: () => void
  onSubmit: (balance: ReadDepositBalance, payload: DepositBalanceUpdatePayload) => Promise<boolean>
}) {
  const t = useT()
  const [amount, setAmount] = useState('')
  const [due, setDue] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [lastId, setLastId] = useState<string | null>(null)
  const currentId = balance?.id ?? null
  if (currentId !== lastId) {
    setLastId(currentId)
    if (balance) {
      setAmount(balance.amount != null ? String(balance.amount) : '')
      setDue(depositDateFromWire(balance.due_at))
      setError(null)
      setSaving(false)
    }
  }

  const submit = async () => {
    if (!balance) return
    const parsed = parseDepositAmount(amount)
    if (amount.trim() && parsed === null) {
      setError(t('depositBalance.error.amountInvalid') as string)
      return
    }
    setError(null)
    setSaving(true)
    try {
      // 兩個 key 都送:空值 = null = 清空(改回未定)。
      const ok = await onSubmit(balance, { amount: parsed, due_at: depositDateToWire(due) })
      if (ok) onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={balance !== null} onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('depositBalance.edit.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('depositBalance.form.amount')}</Label>
            <AmountInput
              data-testid="deposit-edit-amount"
              value={amount}
              onChange={setAmount}
              placeholder={t('depositBalance.form.amountPlaceholder') as string}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('depositBalance.form.dueAt')}</Label>
            <DatePicker
              value={due}
              onChange={setDue}
              clearable
              placeholder={t('depositBalance.form.dueAtPlaceholder') as string}
            />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button data-testid="deposit-edit-submit" disabled={saving} onClick={() => void submit()}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
