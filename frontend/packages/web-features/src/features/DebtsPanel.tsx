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
  Input,
  Label,
  useT,
} from '@beecount/ui'

import type {
  DebtCreatePayload,
  DebtDirection,
  DebtRepayPayload,
  DebtWriteOffPayload,
  ReadAccount,
  ReadCategory,
  ReadDebt,
  WorkspaceCategory,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DatePicker } from '../components/DatePicker'
import { DateTimePicker } from '../components/DateTimePicker'
import { DebtEntryForm } from './DebtEntryForm'
import type { DebtForm } from '../forms'
import {
  buildDebtEntryPayload,
  debtEntryDefaults,
  validateDebtEntry,
  type DebtEntryForm as DebtEntryFormState,
} from '../forms'
import {
  DEBT_TABS,
  allocateRepayment,
  counterpartyHistory,
  counterpartySummary,
  debtStartedAtMs,
  debtTabOf,
  debtTypeTag,
  groupDebtsByCounterparty,
  isDebtCompleted,
  type DebtTab,
} from '../lib/debtList'

type DebtsPanelProps = {
  debts: readonly ReadDebt[]
  accounts: readonly ReadAccount[]
  /** 欠款分類(應收/應付)與轉為支出/收入用的支出/收入分類。 */
  categories?: readonly ReadCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  /** 編輯既有欠款(對象/到期日/備註/不納入總餘額)。本金與方向建立後不可改。 */
  form: DebtForm
  onFormChange: (next: DebtForm) => void
  onSubmit: () => Promise<boolean> | boolean
  /** 新增款項(同記帳對話框的應收/應付分頁)。 */
  onCreateEntry: (payload: DebtCreatePayload) => Promise<boolean>
  onCreateCategory?: (
    name: string,
    kind: 'expense' | 'income' | 'receivable' | 'payable'
  ) => Promise<WorkspaceCategory | null>
  onDelete: (debt: ReadDebt) => Promise<void> | void
  /** 多筆收還款(單筆也走這裡)。 */
  onRepay: (payload: DebtRepayPayload) => Promise<boolean>
  /** 停止追蹤:結案 + 刪掉還沒到期的分期排程。 */
  onStopTracking: (debt: ReadDebt) => Promise<boolean>
  /** 轉為支出(應付:轉為收入)。 */
  onWriteOff: (debt: ReadDebt, payload: DebtWriteOffPayload) => Promise<boolean>
  onReopenDebt: (debt: ReadDebt) => Promise<void> | void
  onToggleExcluded: (debt: ReadDebt) => Promise<void> | void
  /** 雙向勾稽:點起點/收還款記錄跳去對應交易詳情。 */
  onJumpToTx: (txId: string) => void
  /** 從交易詳情跳轉過來時,要高亮的欠款 id。 */
  highlightDebtId?: string | null
  /** 账本 owner 才能新建/编辑/删除/停止追蹤(server `_OWNER_ONLY_ROLES`)。 */
  canManage: boolean
}

const fieldButtonClass =
  'flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50'

const round2 = (v: number) => Math.round(v * 100) / 100

/**
 * 應收應付款項(App v68 MOZE 化,對齊 App `lib/pages/debt/debt_list_page.dart`):
 * 進行中/未開始/已完成三個分頁、對象 chip、依對象分組(組頭合計 + 借還款歷史)、
 * 勾選多筆一起收還款、停止追蹤(可轉為支出/收入)。
 *
 * `remaining_amount`/`status`/`scheduled_amount` 由 server 即時算出(已還只算到
 * 現在),這裡不做任何客戶端累加。
 */
export function DebtsPanel({
  debts,
  accounts,
  categories = [],
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onCreateEntry,
  onCreateCategory,
  onDelete,
  onRepay,
  onStopTracking,
  onWriteOff,
  onReopenDebt,
  onToggleExcluded,
  onJumpToTx,
  highlightDebtId,
  canManage,
}: DebtsPanelProps) {
  const t = useT()
  const nowMs = Date.now()
  const [tab, setTab] = useState<DebtTab>('active')
  const [counterpartyFilter, setCounterpartyFilter] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [mixedWarning, setMixedWarning] = useState(false)

  const [createOpen, setCreateOpen] = useState(false)
  const [createDirection, setCreateDirection] = useState<DebtDirection>('receivable')
  const [createForms, setCreateForms] = useState(() => ({
    receivable: debtEntryDefaults(),
    payable: debtEntryDefaults(),
  }))
  const [createError, setCreateError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const [editOpen, setEditOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [actionTarget, setActionTarget] = useState<ReadDebt | null>(null)
  const [detailTarget, setDetailTarget] = useState<ReadDebt | null>(null)
  const [historyName, setHistoryName] = useState<string | null>(null)
  const [repayIds, setRepayIds] = useState<string[] | null>(null)
  const [stopTarget, setStopTarget] = useState<ReadDebt | null>(null)
  const [writeOffTarget, setWriteOffTarget] = useState<ReadDebt | null>(null)
  const [pendingDelete, setPendingDelete] = useState<ReadDebt | null>(null)
  const [deleting, setDeleting] = useState(false)

  const debtById = useMemo(() => new Map(debts.map((d) => [d.id, d])), [debts])

  // 從交易詳情跳轉過來(?highlight=):切到那筆欠款所在的分頁。
  useEffect(() => {
    if (!highlightDebtId) return
    const d = debts.find((x) => x.id === highlightDebtId)
    if (d) {
      setTab(debtTabOf(d, Date.now()))
      setCounterpartyFilter(null)
    }
  }, [highlightDebtId, debts])

  // 資料刷新後,清掉已經不存在或不再可收還款的勾選。
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => {
        const d = debtById.get(id)
        return d && !isDebtCompleted(d)
      }))
      return next.size === prev.size ? prev : next
    })
  }, [debtById])

  const tabCounts = useMemo(() => {
    const c: Record<DebtTab, number> = { active: 0, notStarted: 0, completed: 0 }
    for (const d of debts) c[debtTabOf(d, nowMs)] += 1
    return c
    // nowMs 每次 render 都變,只在 debts 變化時重算即可。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debts])

  const tabDebts = debts.filter((d) => debtTabOf(d, nowMs) === tab)
  const counterparties = [...new Set(tabDebts.map((d) => d.counterparty_name.trim()))].sort()
  const visible = counterpartyFilter
    ? tabDebts.filter((d) => d.counterparty_name.trim() === counterpartyFilter)
    : tabDebts
  const groups = groupDebtsByCounterparty(visible)

  const selectedDebts = [...selected].map((id) => debtById.get(id)).filter(Boolean) as ReadDebt[]
  const selectedDirections = new Set(selectedDebts.map((d) => d.direction))
  const selectedTotal = round2(selectedDebts.reduce((s, d) => s + d.remaining_amount, 0))

  const toggleSelect = (ids: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const id of ids) {
        if (on) next.add(id)
        else next.delete(id)
      }
      const dirs = new Set([...next].map((id) => debtById.get(id)?.direction))
      setMixedWarning(dirs.size > 1)
      return next
    })
  }

  const openCreate = () => {
    setCreateForms({ receivable: debtEntryDefaults(), payable: debtEntryDefaults() })
    setCreateError(null)
    setCreateOpen(true)
  }

  const handleCreate = async () => {
    const draft = createForms[createDirection]
    const errorKey = validateDebtEntry(draft, createDirection)
    if (errorKey) {
      setCreateError(t(errorKey) as string)
      return
    }
    setCreating(true)
    try {
      if (await onCreateEntry(buildDebtEntryPayload(draft, createDirection))) setCreateOpen(false)
    } finally {
      setCreating(false)
    }
  }

  const openEdit = (debt: ReadDebt) => {
    onFormChange({
      editingId: debt.id,
      direction: debt.direction,
      counterparty_name: debt.counterparty_name,
      principal_amount: String(debt.principal_amount),
      due_at: debt.due_at ? isoToDateInput(debt.due_at) : '',
      note: debt.note || '',
      excluded_from_total: debt.excluded_from_total,
    })
    setEditOpen(true)
  }

  const handleSubmitEdit = async () => {
    setSubmitting(true)
    try {
      if (await onSubmit()) setEditOpen(false)
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
      setDetailTarget(null)
    } finally {
      setDeleting(false)
    }
  }

  const statusText = (d: ReadDebt): string => {
    if (d.status === 'closed') return t('debts.row.stopped') as string
    if (d.status === 'settled') return t('debts.status.settled') as string
    if (debtTabOf(d, nowMs) === 'notStarted') return t('debts.row.notStarted') as string
    const receivable = d.direction === 'receivable'
    if (d.status === 'partial') {
      return t(receivable ? 'debts.row.partialCollected' : 'debts.row.partialRepaid') as string
    }
    return t(receivable ? 'debts.row.uncollected' : 'debts.row.unpaid') as string
  }

  const tagText = (d: ReadDebt) => {
    const tag = debtTypeTag(d)
    return 'text' in tag ? tag.text : (t(tag.key) as string)
  }

  return (
    <div className="space-y-4 pb-16">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">{t('debts.desc')}</p>
        <Button size="sm" data-testid="debts-create" disabled={!canManage} onClick={openCreate}>
          {t('debts.button.create')}
        </Button>
      </div>

      <div className="flex gap-1 rounded-lg bg-muted p-1" role="tablist">
        {DEBT_TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            data-testid={`debts-tab-${key}`}
            aria-selected={tab === key}
            onClick={() => {
              setTab(key)
              setCounterpartyFilter(null)
            }}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
              tab === key ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {t(`debts.tab.${key}`)}
            {tabCounts[key] ? <span className="ml-1 text-xs opacity-70">{tabCounts[key]}</span> : null}
          </button>
        ))}
      </div>

      {counterparties.length > 1 ? (
        <div className="flex flex-wrap gap-2">
          {[null, ...counterparties].map((name) => (
            <button
              key={name ?? '__all__'}
              type="button"
              onClick={() => setCounterpartyFilter(name)}
              className={[
                'rounded-full border px-3 py-1 text-xs transition-colors',
                counterpartyFilter === name
                  ? 'border-primary/60 bg-primary/10 text-primary'
                  : 'border-border/60 text-muted-foreground hover:bg-accent/40',
              ].join(' ')}
            >
              {name ?? t('debts.filter.all')}
            </button>
          ))}
        </div>
      ) : null}

      {groups.length === 0 ? (
        <EmptyState
          title={debts.length === 0 ? t('debts.empty') : t('debts.tab.empty')}
          description={debts.length === 0 ? t('debts.emptyDesc') : undefined}
        />
      ) : (
        <div className="space-y-3">
          {groups.map((group) => {
            const selectable = group.debts.filter((d) => !isDebtCompleted(d))
            const allChecked = selectable.length > 0 && selectable.every((d) => selected.has(d.id))
            return (
              <div key={group.counterparty} className="overflow-hidden rounded-xl border border-border/60 bg-card">
                <div className="flex items-center gap-2 border-b border-border/40 bg-muted/30 px-3 py-2">
                  {selectable.length > 0 ? (
                    <input
                      type="checkbox"
                      aria-label={group.counterparty}
                      checked={allChecked}
                      onChange={(e) => toggleSelect(selectable.map((d) => d.id), e.target.checked)}
                    />
                  ) : (
                    <span className="w-[13px]" />
                  )}
                  <span className="flex-1 truncate text-sm font-semibold">
                    {group.counterparty} ({group.debts.length})
                  </span>
                  <Amount
                    value={group.net}
                    currency={currency}
                    size="sm"
                    bold
                    sign="always"
                    tone={group.net >= 0 ? 'positive' : 'negative'}
                  />
                  <button
                    type="button"
                    title={t('debts.history.title') as string}
                    aria-label={t('debts.history.title') as string}
                    data-testid={`debts-history-${group.counterparty}`}
                    onClick={() => setHistoryName(group.counterparty)}
                    className="rounded p-1 text-muted-foreground hover:bg-accent/40 hover:text-foreground"
                  >
                    <span className="material-symbols-outlined text-base">receipt_long</span>
                  </button>
                </div>
                <div className="divide-y divide-border/40">
                  {group.debts.map((d) => {
                    const startedMs = debtStartedAtMs(d)
                    const overdue =
                      d.due_at && !isDebtCompleted(d) && dateOnlyUtcMs(d.due_at) < startOfTodayUtcMs()
                    const account = d.origin_transaction?.account_name
                    return (
                      <div
                        key={d.id}
                        id={`debt-${d.id}`}
                        className={[
                          'flex items-center gap-2 px-3 py-2 transition hover:bg-accent/30',
                          highlightDebtId === d.id ? 'bg-primary/10 ring-1 ring-inset ring-primary/40' : '',
                        ].join(' ')}
                      >
                        {isDebtCompleted(d) ? (
                          <span className="w-[13px]" />
                        ) : (
                          <input
                            type="checkbox"
                            aria-label={d.counterparty_name}
                            checked={selected.has(d.id)}
                            onChange={(e) => toggleSelect([d.id], e.target.checked)}
                          />
                        )}
                        <button
                          type="button"
                          data-testid={`debt-row-${d.id}`}
                          onClick={() => setActionTarget(d)}
                          className="flex min-w-0 flex-1 items-center gap-3 text-left"
                        >
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm">
                              {d.note?.trim() || tagText(d)}
                            </div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
                              {startedMs ? <span>{new Date(startedMs).toLocaleDateString()}</span> : null}
                              {d.installment_count ? (
                                <span>
                                  {d.installment_no
                                    ? t('debts.row.installmentNo')
                                        .replace('{no}', String(d.installment_no))
                                        .replace('{count}', String(d.installment_count))
                                    : t('debtEntry.summary.installment').replace(
                                        '{count}',
                                        String(d.installment_count)
                                      )}
                                </span>
                              ) : null}
                              {d.due_at ? (
                                <span className={overdue ? 'text-destructive' : ''}>
                                  {overdue
                                    ? t('debts.row.overdue')
                                    : t('debts.row.due').replace('{date}', formatDateOnlyUTC(d.due_at))}
                                </span>
                              ) : null}
                            </div>
                          </div>
                          <div className="shrink-0 text-right">
                            <div className="text-[11px] text-muted-foreground">
                              {statusText(d)}
                              {d.scheduled_amount && !isDebtCompleted(d) ? (
                                <span className="ml-1">
                                  · {t('debts.row.scheduled').replace(
                                    '{amount}',
                                    d.scheduled_amount.toLocaleString()
                                  )}
                                </span>
                              ) : null}
                            </div>
                            <Amount
                              value={d.remaining_amount}
                              currency={currency}
                              size="md"
                              bold
                              tone={d.direction === 'receivable' ? 'positive' : 'negative'}
                            />
                            <div className="mt-0.5 flex flex-wrap justify-end gap-1">
                              <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                {tagText(d)}
                              </span>
                              {account ? (
                                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                  {account}
                                </span>
                              ) : null}
                              {d.excluded_from_total ? (
                                <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                  {t('debtEntry.excludedFromTotal')}
                                </span>
                              ) : null}
                            </div>
                          </div>
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 勾選多筆:底部「新增收款 +$X」+ 停止追蹤。 */}
      {selectedDebts.length > 0 ? (
        <div className="sticky bottom-2 z-10 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border/60 bg-card/95 px-3 py-2 shadow-lg backdrop-blur">
          <span className="text-xs text-muted-foreground">
            {mixedWarning || selectedDirections.size > 1
              ? t('debts.select.mixedDirection')
              : t('debts.select.count').replace('{count}', String(selectedDebts.length))}
          </span>
          <div className="flex gap-2">
            <Button size="sm" variant="ghost" onClick={() => toggleSelect([...selected], false)}>
              {t('dialog.cancel')}
            </Button>
            {selectedDebts.length === 1 && canManage ? (
              <Button size="sm" variant="outline" onClick={() => setStopTarget(selectedDebts[0])}>
                {t('debts.action.stopTracking')}
              </Button>
            ) : null}
            <Button
              size="sm"
              data-testid="debts-repay-selected"
              disabled={selectedDirections.size !== 1}
              onClick={() => setRepayIds(selectedDebts.map((d) => d.id))}
            >
              {t(
                selectedDebts[0]?.direction === 'receivable'
                  ? 'debts.select.collect'
                  : 'debts.select.repay'
              ).replace('{amount}', selectedTotal.toLocaleString())}
            </Button>
          </div>
        </div>
      ) : null}

      {/* 點一列:選項 */}
      <Dialog open={actionTarget !== null} onOpenChange={(next) => !next && setActionTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {actionTarget ? `${actionTarget.counterparty_name} · ${tagText(actionTarget)}` : ''}
            </DialogTitle>
          </DialogHeader>
          {actionTarget ? (
            <div className="flex flex-col gap-1">
              <ActionItem
                icon="description"
                label={t('debts.action.detail')}
                onClick={() => {
                  setDetailTarget(actionTarget)
                  setActionTarget(null)
                }}
              />
              {!isDebtCompleted(actionTarget) ? (
                <ActionItem
                  icon="payments"
                  testId="debt-action-repay"
                  label={t(
                    actionTarget.direction === 'receivable' ? 'debts.action.collect' : 'debts.action.repay'
                  )}
                  onClick={() => {
                    setRepayIds([actionTarget.id])
                    setActionTarget(null)
                  }}
                />
              ) : null}
              <ActionItem
                icon="receipt_long"
                label={t(
                  actionTarget.direction === 'receivable'
                    ? 'debts.action.historyReceivable'
                    : 'debts.action.historyPayable'
                )}
                onClick={() => {
                  setHistoryName(actionTarget.counterparty_name.trim())
                  setActionTarget(null)
                }}
              />
              {actionTarget.status === 'closed' ? (
                <ActionItem
                  icon="refresh"
                  disabled={!canManage}
                  label={t('debts.action.resumeTracking')}
                  onClick={() => {
                    void onReopenDebt(actionTarget)
                    setActionTarget(null)
                  }}
                />
              ) : actionTarget.status !== 'settled' ? (
                <ActionItem
                  icon="money_off"
                  testId="debt-action-stop"
                  disabled={!canManage}
                  label={t('debts.action.stopTracking')}
                  onClick={() => {
                    setStopTarget(actionTarget)
                    setActionTarget(null)
                  }}
                />
              ) : null}
              <ActionItem
                icon="balance"
                disabled={!canManage}
                label={t(actionTarget.excluded_from_total ? 'debts.action.include' : 'debts.action.exclude')}
                onClick={() => {
                  void onToggleExcluded(actionTarget)
                  setActionTarget(null)
                }}
              />
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* 查看詳情:起點交易 + 收還款紀錄 + 編輯/刪除 */}
      <Dialog open={detailTarget !== null} onOpenChange={(next) => !next && setDetailTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('debts.action.detail')}</DialogTitle>
          </DialogHeader>
          {detailTarget ? (
            <DebtDetail
              debt={debtById.get(detailTarget.id) || detailTarget}
              currency={currency}
              canManage={canManage}
              onJumpToTx={onJumpToTx}
              onEdit={() => {
                openEdit(detailTarget)
                setDetailTarget(null)
              }}
              onDelete={() => setPendingDelete(detailTarget)}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      {/* 新增款項:應收/應付 + DebtEntryForm(同記帳對話框的分頁) */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle>{t('debts.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="flex gap-1 rounded-lg bg-muted/40 p-1 md:col-span-2" role="tablist">
                {(['receivable', 'payable'] as const).map((dir) => (
                  <button
                    key={dir}
                    type="button"
                    role="tab"
                    data-testid={`debts-create-${dir}`}
                    aria-selected={createDirection === dir}
                    onClick={() => {
                      setCreateDirection(dir)
                      setCreateError(null)
                    }}
                    className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                      createDirection === dir
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {t(`enum.txType.${dir}`)}
                  </button>
                ))}
              </div>
              <DebtEntryForm
                direction={createDirection}
                form={createForms[createDirection]}
                onChange={(next: DebtEntryFormState) => {
                  setCreateError(null)
                  setCreateForms((prev) => ({ ...prev, [createDirection]: next }))
                }}
                accounts={accounts}
                categories={categories}
                debts={debts}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                onCreateCategory={onCreateCategory}
              />
            </div>
            {createError ? <p className="mt-3 text-sm text-destructive">{createError}</p> : null}
          </div>
          <DialogFooter className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
            <Button variant="outline" disabled={creating} onClick={() => setCreateOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button data-testid="debts-create-submit" disabled={creating || !canManage} onClick={() => void handleCreate()}>
              {t('debts.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 編輯既有欠款 */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('debts.button.update')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('debts.field.counterparty')}</Label>
              <Input
                value={form.counterparty_name}
                onChange={(e) => onFormChange({ ...form, counterparty_name: e.target.value })}
                placeholder={t('debts.placeholder.counterparty')}
              />
              <p className="text-xs text-muted-foreground">{t('debts.field.counterpartyRenameHint')}</p>
            </div>
            <div className="space-y-1">
              <Label>{t('debts.field.dueAt')}</Label>
              <DatePicker
                value={form.due_at}
                onChange={(next) => onFormChange({ ...form, due_at: next })}
                clearable
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input value={form.note} onChange={(e) => onFormChange({ ...form, note: e.target.value })} />
            </div>
            <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
              <div className="min-w-0 pr-3">
                <p className="text-sm font-medium">{t('debts.excludedFromTotal.toggleLabel')}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">{t('debts.excludedFromTotal.toggleHint')}</p>
              </div>
              <Switch
                checked={form.excluded_from_total}
                label={t('debts.excludedFromTotal.toggleLabel') as string}
                onToggle={() => onFormChange({ ...form, excluded_from_total: !form.excluded_from_total })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setEditOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={submitting || !canManage || !form.counterparty_name.trim()}
              onClick={() => void handleSubmitEdit()}
            >
              {t('debts.button.update')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <RepayDialog
        open={repayIds !== null}
        initialIds={repayIds || []}
        debts={debts}
        accounts={accounts}
        onClose={() => setRepayIds(null)}
        onSubmit={async (payload) => {
          const ok = await onRepay(payload)
          if (ok) {
            setRepayIds(null)
            setSelected(new Set())
            setMixedWarning(false)
          }
          return ok
        }}
      />

      <StopTrackingDialog
        debt={stopTarget}
        onClose={() => setStopTarget(null)}
        onStop={async (debt) => {
          if (await onStopTracking(debt)) {
            setStopTarget(null)
            toggleSelect([debt.id], false)
          }
        }}
        onWriteOff={(debt) => {
          setStopTarget(null)
          setWriteOffTarget(debt)
        }}
      />

      <WriteOffDialog
        debt={writeOffTarget}
        accounts={accounts}
        categories={categories}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        currency={currency}
        onClose={() => setWriteOffTarget(null)}
        onSubmit={async (debt, payload) => {
          const ok = await onWriteOff(debt, payload)
          if (ok) {
            setWriteOffTarget(null)
            toggleSelect([debt.id], false)
          }
          return ok
        }}
      />

      <HistoryDialog
        counterparty={historyName}
        debts={debts.filter((d) => d.counterparty_name.trim() === historyName)}
        currency={currency}
        onClose={() => setHistoryName(null)}
        onJumpToTx={onJumpToTx}
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
    </div>
  )
}

/** 圖示名稱必須在 apps/web/index.html 的 Material Symbols `icon_names` 子集裡,
 *  不在清單裡的會直接顯示成英文字。 */
function ActionItem({
  icon,
  label,
  onClick,
  disabled,
  testId,
}: {
  icon: string
  label: string
  onClick: () => void
  disabled?: boolean
  testId?: string
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      className="flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span className="material-symbols-outlined text-lg text-muted-foreground">{icon}</span>
      {label}
    </button>
  )
}

function Switch({ checked, onToggle, label }: { checked: boolean; onToggle: () => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onToggle}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
        checked ? 'bg-primary' : 'bg-muted-foreground/30'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-[18px]' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}

function DebtDetail({
  debt,
  currency,
  canManage,
  onJumpToTx,
  onEdit,
  onDelete,
}: {
  debt: ReadDebt
  currency: string
  canManage: boolean
  onJumpToTx: (txId: string) => void
  onEdit: () => void
  onDelete: () => void
}) {
  const t = useT()
  const pastRepayments = debt.repayments.filter((r) => !r.scheduled)
  const hasRepayments = pastRepayments.length > 0
  const ratio = debt.principal_amount > 0
    ? Math.min((debt.principal_amount - debt.remaining_amount) / debt.principal_amount, 1)
    : 0
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{debt.counterparty_name}</div>
          <div className="text-[11px] text-muted-foreground">
            {t(`debts.status.${debt.status}`)}
            {debt.due_at ? ` · ${t('debts.label.dueAt')} ${formatDateOnlyUTC(debt.due_at)}` : ''}
          </div>
          {debt.note ? <div className="mt-0.5 text-[11px] text-muted-foreground">{debt.note}</div> : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={debt.remaining_amount} currency={currency} size="md" bold />
          <div className="text-[11px] text-muted-foreground">
            {t('debts.label.principal')}{' '}
            <Amount value={debt.principal_amount} currency={currency} size="sm" tone="muted" />
          </div>
        </div>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-primary transition-all" style={{ width: `${ratio * 100}%` }} />
      </div>

      {debt.origin_transaction ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-muted-foreground">
            {t('debts.label.originTransaction')}
            {debt.from_split ? ` · ${t('debts.fromSplit')}` : ''}
          </div>
          <TxLine
            atIso={debt.origin_transaction.happened_at}
            amount={debt.origin_transaction.amount}
            account={debt.origin_transaction.account_name}
            currency={currency}
            onClick={() => onJumpToTx(debt.origin_transaction!.id)}
          />
        </div>
      ) : null}

      {debt.repayments.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[11px] font-medium text-muted-foreground">{t('debts.label.repayments')}</div>
          <div className="max-h-56 space-y-0.5 overflow-y-auto">
            {debt.repayments.map((r) => (
              <TxLine
                key={r.id}
                atIso={r.happened_at}
                amount={r.amount}
                account={r.account_name}
                scheduled={r.scheduled}
                currency={currency}
                onClick={() => onJumpToTx(r.id)}
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!canManage || hasRepayments || Boolean(debt.from_split)}
          title={
            debt.from_split
              ? t('error.DEBT_FROM_SPLIT')
              : hasRepayments
                ? t('debts.delete.blockedByRepayments')
                : undefined
          }
          onClick={onDelete}
        >
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}

function TxLine({
  atIso,
  amount,
  account,
  scheduled,
  currency,
  onClick,
}: {
  atIso: string
  amount: number
  account?: string | null
  scheduled?: boolean
  currency: string
  onClick: () => void
}) {
  const t = useT()
  return (
    <button
      type="button"
      onClick={onClick}
      title={t('debts.label.linkedTx.jumpHint') as string}
      className="flex w-full items-center justify-between gap-2 rounded px-1 py-0.5 text-[11px] text-muted-foreground transition hover:bg-accent/40 hover:text-primary"
    >
      <span className="truncate">
        {new Date(atIso).toLocaleDateString()}
        {account ? ` · ${account}` : ''}
        {scheduled ? ` · ${t('debts.row.scheduledShort')}` : ''}
      </span>
      <Amount value={Math.abs(amount)} currency={currency} size="xs" tone="muted" />
    </button>
  )
}

/** 收還款(MOZE「新增收款」):上方列出選中的款項與各自分到的金額(依借出
 *  日由舊到新分配),可再加入同方向款項;底部顯示剩餘款項與不足金額。 */
function RepayDialog({
  open,
  initialIds,
  debts,
  accounts,
  onClose,
  onSubmit,
}: {
  open: boolean
  initialIds: string[]
  debts: readonly ReadDebt[]
  accounts: readonly ReadAccount[]
  onClose: () => void
  onSubmit: (payload: DebtRepayPayload) => Promise<boolean>
}) {
  const t = useT()
  const [ids, setIds] = useState<string[]>([])
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [at, setAt] = useState('')
  const [note, setNote] = useState('')
  const [accountOpen, setAccountOpen] = useState(false)
  const [shortAsk, setShortAsk] = useState(false)
  const [saving, setSaving] = useState(false)
  const [addOpen, setAddOpen] = useState(false)

  const byId = useMemo(() => new Map(debts.map((d) => [d.id, d])), [debts])
  const chosen = ids.map((id) => byId.get(id)).filter(Boolean) as ReadDebt[]
  const direction = chosen[0]?.direction
  const totalRemaining = round2(chosen.reduce((s, d) => s + d.remaining_amount, 0))

  const [lastOpen, setLastOpen] = useState(false)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) {
      const initial = initialIds.map((id) => byId.get(id)).filter(Boolean) as ReadDebt[]
      setIds(initialIds)
      setAmount(String(round2(initial.reduce((s, d) => s + d.remaining_amount, 0)) || ''))
      // 預設帳戶:第一筆起點交易的帳戶(同 App)。
      const originAccount = initial.find((d) => d.origin_transaction?.account_id)?.origin_transaction?.account_id
      setAccountId(originAccount || '')
      setAt(isoToLocalInput(new Date().toISOString()))
      setNote('')
      setShortAsk(false)
      setAddOpen(false)
    }
  }

  const amountNum = Number(amount)
  const valid = Number.isFinite(amountNum) && amountNum > 0
  const over = valid && amountNum > totalRemaining + 0.004
  const short = valid && !over ? round2(totalRemaining - amountNum) : 0
  const allocations = valid && !over ? allocateRepayment(chosen, amountNum) : []
  const allocById = new Map(allocations.map((a) => [a.debt.id, a.amount]))
  const addable = debts.filter(
    (d) => d.direction === direction && !isDebtCompleted(d) && !ids.includes(d.id)
  )
  const accountName = accounts.find((a) => a.id === accountId)?.name

  const submit = async (settle: boolean) => {
    setSaving(true)
    try {
      const settleIds = settle
        ? chosen
            .filter((d) => round2(d.remaining_amount - (allocById.get(d.id) || 0)) > 0.004)
            .map((d) => d.id)
        : []
      await onSubmit({
        allocations: allocations.map((a) => ({ debt_id: a.debt.id, amount: a.amount })),
        account_id: accountId || null,
        happened_at: new Date(at).toISOString(),
        note: note.trim() || null,
        settle_debt_ids: settleIds,
      })
    } finally {
      setSaving(false)
      setShortAsk(false)
    }
  }

  const receivable = direction === 'receivable'
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {chosen.length > 1
              ? t(receivable ? 'debts.repay.multiCollectTitle' : 'debts.repay.multiRepayTitle')
              : t(receivable ? 'debts.repayment.titleReceive' : 'debts.repayment.titlePay')}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1 rounded-lg border border-border/60 p-2">
            {chosen.map((d) => (
              <div key={d.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="min-w-0 flex-1 truncate">
                  {d.counterparty_name} · {d.note?.trim() || new Date(debtStartedAtMs(d) || Date.now()).toLocaleDateString()}
                </span>
                <span className="shrink-0 text-muted-foreground">
                  {(allocById.get(d.id) || 0).toLocaleString()} / {d.remaining_amount.toLocaleString()}
                </span>
                {chosen.length > 1 ? (
                  <button
                    type="button"
                    aria-label={t('common.delete') as string}
                    onClick={() => setIds(ids.filter((x) => x !== d.id))}
                    className="px-1 text-muted-foreground hover:text-destructive"
                  >
                    ✕
                  </button>
                ) : null}
              </div>
            ))}
            {addable.length > 0 ? (
              addOpen ? (
                <div className="space-y-1 border-t border-border/40 pt-1">
                  {addable.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => {
                        setIds([...ids, d.id])
                        setAmount(String(round2(totalRemaining + d.remaining_amount)))
                        setAddOpen(false)
                      }}
                      className="flex w-full items-center justify-between rounded px-1 py-0.5 text-xs hover:bg-accent/40"
                    >
                      <span className="truncate">
                        {d.counterparty_name} · {d.note?.trim() || ''}
                      </span>
                      <span className="text-muted-foreground">{d.remaining_amount.toLocaleString()}</span>
                    </button>
                  ))}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setAddOpen(true)}
                  className="text-xs text-primary hover:underline"
                >
                  + {t('debts.repay.addMore')}
                </button>
              )
            ) : null}
          </div>

          <div className="space-y-1">
            <Label>{t('debtEntry.amount.plain')}</Label>
            <AmountInput data-testid="debt-repay-amount" value={amount} onChange={setAmount} />
            <p className={`text-xs ${over ? 'text-destructive' : 'text-muted-foreground'}`}>
              {over
                ? t('debts.repay.overLimit').replace('{amount}', totalRemaining.toLocaleString())
                : t('debts.repay.remainingFooter').replace('{amount}', totalRemaining.toLocaleString()) +
                  (short > 0 ? t('debts.repay.shortFooter').replace('{amount}', short.toLocaleString()) : '')}
            </p>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.account')}</Label>
            <button type="button" onClick={() => setAccountOpen(true)} className={fieldButtonClass}>
              <span className={`flex-1 truncate ${accountName ? '' : 'text-muted-foreground'}`}>
                {accountName || t('transactions.placeholder.noAccount')}
              </span>
              <span className="text-xs text-muted-foreground opacity-60">▾</span>
            </button>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.time')}</Label>
            <DateTimePicker value={at} onChange={setAt} />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {shortAsk ? (
            <div className="space-y-2 rounded-lg border border-orange-500/40 bg-orange-500/5 p-2 text-xs">
              <p>
                {t(receivable ? 'debts.repay.shortMessageReceivable' : 'debts.repay.shortMessagePayable').replace(
                  '{amount}',
                  short.toLocaleString()
                )}
              </p>
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="outline" disabled={saving} onClick={() => void submit(false)}>
                  {t('debts.repay.shortContinue')}
                </Button>
                <Button size="sm" data-testid="debt-repay-settle" disabled={saving} onClick={() => void submit(true)}>
                  {t('debts.repay.shortSettle')}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            data-testid="debt-repay-submit"
            disabled={saving || !valid || over || allocations.length === 0 || !at}
            onClick={() => {
              // 多筆款項收不滿時,問要繼續追蹤剩下的還是視為結清(同 App)。
              if (short > 0.004 && chosen.length > 1 && !shortAsk) {
                setShortAsk(true)
                return
              }
              void submit(false)
            }}
          >
            {t('debts.repayment.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <AccountPickerDialog
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        accounts={accounts as ReadAccount[]}
        value={accountName || ''}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount') as string}
        title={t('transactions.table.account') as string}
        onSelect={(row) => {
          setAccountId(row.id)
          setAccountOpen(false)
        }}
      />
    </Dialog>
  )
}

/** 停止追蹤:「轉為支出(應付:轉為收入)/停止追蹤/取消」。有未來分期時提示
 *  會一併刪除。 */
function StopTrackingDialog({
  debt,
  onClose,
  onStop,
  onWriteOff,
}: {
  debt: ReadDebt | null
  onClose: () => void
  onStop: (debt: ReadDebt) => Promise<void>
  onWriteOff: (debt: ReadDebt) => void
}) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  if (!debt) return null
  const receivable = debt.direction === 'receivable'
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('debts.action.stopTracking')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 text-sm">
          <p>{t(receivable ? 'debts.stop.messageReceivable' : 'debts.stop.messagePayable')}</p>
          {debt.scheduled_amount ? (
            <p className="text-xs text-muted-foreground">{t('debts.stop.scheduledNote')}</p>
          ) : null}
        </div>
        <DialogFooter className="flex-col gap-2 sm:flex-row">
          <Button variant="outline" disabled={busy} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            variant="outline"
            data-testid="debt-stop-confirm"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onStop(debt)
              } finally {
                setBusy(false)
              }
            }}
          >
            {t('debts.action.stopTracking')}
          </Button>
          <Button
            data-testid="debt-stop-writeoff"
            disabled={busy || debt.remaining_amount <= 0.004}
            onClick={() => onWriteOff(debt)}
          >
            {t(receivable ? 'debts.stop.writeOffExpense' : 'debts.stop.writeOffIncome')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 轉為支出/收入:剩餘金額記一筆收還款(不計收支)+ 一筆同額計入收支的
 *  支出/收入,帳戶餘額不變。分類必填。 */
function WriteOffDialog({
  debt,
  accounts,
  categories,
  iconPreviewUrlByFileId,
  currency,
  onClose,
  onSubmit,
}: {
  debt: ReadDebt | null
  accounts: readonly ReadAccount[]
  categories: readonly ReadCategory[]
  iconPreviewUrlByFileId?: Record<string, string>
  currency: string
  onClose: () => void
  onSubmit: (debt: ReadDebt, payload: DebtWriteOffPayload) => Promise<boolean>
}) {
  const t = useT()
  const [categoryId, setCategoryId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [at, setAt] = useState('')
  const [note, setNote] = useState('')
  const [categoryOpen, setCategoryOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [lastId, setLastId] = useState<string | null>(null)
  const currentId = debt?.id ?? null
  if (currentId !== lastId) {
    setLastId(currentId)
    if (debt) {
      setCategoryId('')
      setAccountId(debt.origin_transaction?.account_id || '')
      setAt(isoToLocalInput(new Date().toISOString()))
      setNote(t('debts.writeOff.noteDefault') as string)
      setError(null)
    }
  }
  if (!debt) return null
  const receivable = debt.direction === 'receivable'
  const kind = receivable ? 'expense' : 'income'
  const category = categories.find((c) => c.id === categoryId)
  const accountName = accounts.find((a) => a.id === accountId)?.name

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t(receivable ? 'debts.stop.writeOffExpense' : 'debts.stop.writeOffIncome')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {t(receivable ? 'debts.writeOff.hintReceivable' : 'debts.writeOff.hintPayable')}
          </p>
          <div className="flex items-center justify-between text-sm">
            <span>{t('debtEntry.amount.plain')}</span>
            <Amount value={debt.remaining_amount} currency={currency} size="md" bold />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.category')}</Label>
            <button
              type="button"
              data-testid="debt-writeoff-category"
              onClick={() => setCategoryOpen(true)}
              className={fieldButtonClass}
            >
              <span className={`flex-1 truncate ${category ? '' : 'text-muted-foreground'}`}>
                {category?.name || t('transactions.placeholder.categoryName')}
              </span>
              <span className="text-xs text-muted-foreground opacity-60">▾</span>
            </button>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.account')}</Label>
            <button type="button" onClick={() => setAccountOpen(true)} className={fieldButtonClass}>
              <span className={`flex-1 truncate ${accountName ? '' : 'text-muted-foreground'}`}>
                {accountName || t('transactions.placeholder.noAccount')}
              </span>
              <span className="text-xs text-muted-foreground opacity-60">▾</span>
            </button>
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.time')}</Label>
            <DateTimePicker value={at} onChange={setAt} />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button
            data-testid="debt-writeoff-submit"
            disabled={saving}
            onClick={async () => {
              if (!categoryId) {
                setError(t('debtEntry.error.category') as string)
                return
              }
              setSaving(true)
              try {
                await onSubmit(debt, {
                  category_id: categoryId,
                  account_id: accountId || null,
                  happened_at: new Date(at).toISOString(),
                  note: note.trim() || null,
                })
              } finally {
                setSaving(false)
              }
            }}
          >
            {t(receivable ? 'debts.stop.writeOffExpense' : 'debts.stop.writeOffIncome')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <CategoryPickerDialog
        open={categoryOpen}
        onClose={() => setCategoryOpen(false)}
        kind={kind}
        rows={categories as WorkspaceCategory[]}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={categoryId || undefined}
        title={t('transactions.placeholder.categoryName') as string}
        onSelect={(cat) => {
          setCategoryId(cat.id)
          setError(null)
          setCategoryOpen(false)
        }}
      />
      <AccountPickerDialog
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        accounts={accounts as ReadAccount[]}
        value={accountName || ''}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount') as string}
        title={t('transactions.table.account') as string}
        onSelect={(row) => {
          setAccountId(row.id)
          setAccountOpen(false)
        }}
      />
    </Dialog>
  )
}

/** 對象的借還款歷史:上方兩列彙總,下方時間軸由新到舊,每列右側是累計淨額。 */
function HistoryDialog({
  counterparty,
  debts,
  currency,
  onClose,
  onJumpToTx,
}: {
  counterparty: string | null
  debts: readonly ReadDebt[]
  currency: string
  onClose: () => void
  onJumpToTx: (txId: string) => void
}) {
  const t = useT()
  if (counterparty === null) return null
  const summary = counterpartySummary(debts)
  const entries = counterpartyHistory(debts)
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-md flex-col">
        <DialogHeader>
          <DialogTitle>
            {counterparty} · {t('debts.history.title')}
          </DialogTitle>
        </DialogHeader>
        <div className="grid grid-cols-3 gap-2 rounded-lg bg-muted/40 p-2 text-center text-[11px]">
          <SummaryCell label={t('debts.summary.payable')} value={summary.payable} currency={currency} />
          <SummaryCell label={t('debts.summary.repaid')} value={summary.repaid} currency={currency} />
          <SummaryCell label={t('debts.summary.remaining')} value={summary.payableRemaining} currency={currency} />
          <SummaryCell label={t('debts.summary.receivable')} value={summary.receivable} currency={currency} />
          <SummaryCell label={t('debts.summary.collected')} value={summary.collected} currency={currency} />
          <SummaryCell label={t('debts.summary.remaining')} value={summary.receivableRemaining} currency={currency} />
        </div>
        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {entries.length === 0 ? (
            <p className="py-6 text-center text-xs text-muted-foreground">{t('debts.history.empty')}</p>
          ) : (
            entries.map((e, i) => {
              const tag = debtTypeTag(e.debt)
              const label = e.isOrigin
                ? 'text' in tag
                  ? tag.text
                  : t(tag.key)
                : t(e.debt.direction === 'receivable' ? 'debts.tag.collect' : 'debts.tag.repay')
              return (
                <button
                  key={`${e.debt.id}-${e.txId || 'origin'}-${i}`}
                  type="button"
                  disabled={!e.txId}
                  onClick={() => e.txId && onJumpToTx(e.txId)}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition hover:bg-accent/40 disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate">
                      <span className="mr-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                        {label}
                      </span>
                      {e.debt.note?.trim() || ''}
                    </div>
                    <div className="mt-0.5 text-[10px] text-muted-foreground">
                      {e.atMs ? new Date(e.atMs).toLocaleString() : ''}
                      {e.accountName ? ` · ${e.accountName}` : ''}
                      {e.scheduled ? ` · ${t('debts.row.scheduledShort')}` : ''}
                    </div>
                  </div>
                  <div className="shrink-0 text-right">
                    <Amount value={e.amount} currency={currency} size="sm" />
                    <div className="text-[10px] text-muted-foreground">
                      <Amount value={e.runningNet} currency={currency} size="xs" tone="muted" sign="always" />
                    </div>
                  </div>
                </button>
              )
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function SummaryCell({ label, value, currency }: { label: string; value: number; currency: string }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <Amount value={value} currency={currency} size="sm" bold />
    </div>
  )
}

function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
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

function dateOnlyUtcMs(iso: string): number {
  const d = forceUtcDate(iso)
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())
}

/** 今天(本地日期)的 UTC 零點毫秒,跟純日期欄位的 UTC 零點編碼比較。 */
function startOfTodayUtcMs(): number {
  const now = new Date()
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())
}
