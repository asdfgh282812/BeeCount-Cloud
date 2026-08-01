import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createDebt,
  createTransaction,
  deleteDebt,
  fetchReadAccounts,
  fetchReadDebts,
  fetchWorkspaceTransactions,
  updateDebt,
  type ReadAccount,
  type ReadDebt,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import { DebtsPanel, debtDefaults, type DebtForm, type RepaymentPayload } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dispatchOpenDetailTx } from '../../lib/txDialogEvents'

/**
 * 借還款追蹤页(MOZE_FEATURE_GAP_SD.md §2.5 Phase 3)—— 结构照抄 BudgetsPage:
 * 账本级实体走 fetchReadDebts,`remaining_amount`/`status` 由 server 从反查
 * 交易即时算出,这里不做任何客户端累加。
 *
 * 只有账本 owner 能新建/编辑/删除欠款(server `_OWNER_ONLY_ROLES`),`canManage`
 * 按 `currentLedger.role === 'owner'` 收窄;还款/收款走一般交易写权限
 * (`_TRANSACTION_WRITE_ROLES`,owner + editor 都可以),不受这个开关限制。
 */
export function DebtsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const [searchParams] = useSearchParams()
  const highlightDebtId = searchParams.get('highlight')

  const bucket = activeLedgerId || '__none__'
  const [debts, setDebts] = usePageCache<ReadDebt[]>(`debts:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(`debts:${bucket}:accounts`, [])
  const [form, setForm] = useState<DebtForm>(debtDefaults())

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setDebts([])
      setAccounts([])
      return
    }
    try {
      const [d, a] = await Promise.all([
        fetchReadDebts(token, activeLedgerId),
        fetchReadAccounts(token, activeLedgerId),
      ])
      setDebts(d)
      setAccounts(a)
    } catch (err) {
      notifyError(err)
    }
    // setDebts / setAccounts 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  // 從交易詳情跳轉過來時(?highlight=<debtId>),定位並高亮對應卡片一次。
  useEffect(() => {
    if (!highlightDebtId || debts.length === 0) return
    const el = document.getElementById(`debt-${highlightDebtId}`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightDebtId, debts.length])

  const canManage = Boolean(activeLedgerId) && currentLedger?.role === 'owner'

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const principalAmount = Number((form.principal_amount || '').toString().trim())
    if (!Number.isFinite(principalAmount) || principalAmount <= 0) {
      toast.error(t('debts.error.amountInvalid'), t('notice.error'))
      return false
    }
    if (!form.counterparty_name.trim()) {
      toast.error(t('debts.error.counterpartyRequired'), t('notice.error'))
      return false
    }
    const dueAtIso = form.due_at.trim() ? new Date(form.due_at).toISOString() : null
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateDebt(token, activeLedgerId, form.editingId!, base, {
            counterparty_name: form.counterparty_name.trim(),
            due_at: dueAtIso,
            note: form.note || null,
          }),
        )
        notifySuccess(t('debts.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createDebt(token, activeLedgerId, base, {
            direction: form.direction,
            counterparty_name: form.counterparty_name.trim(),
            principal_amount: principalAmount,
            due_at: dueAtIso,
            note: form.note || null,
          }),
        )
        notifySuccess(t('debts.notice.created'))
      }
      setForm(debtDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (debt: ReadDebt): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) => deleteDebt(token, activeLedgerId, debt.id, base))
      notifySuccess(t('debts.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onRecordRepayment = async (debt: ReadDebt, payload: RepaymentPayload): Promise<void> => {
    if (!activeLedgerId) return
    const account = payload.account_id ? accounts.find((a) => a.id === payload.account_id) : null
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        createTransaction(token, activeLedgerId, base, {
          tx_type: debt.direction === 'receivable' ? 'income' : 'expense',
          amount: payload.amount,
          happened_at: payload.happened_at,
          note: payload.note,
          account_id: payload.account_id,
          account_name: account?.name || null,
          debt_id: debt.id,
        }),
      )
      notifySuccess(t('debts.repayment.notice.recorded'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onCloseDebt = async (debt: ReadDebt): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateDebt(token, activeLedgerId, debt.id, base, { closed_at: new Date().toISOString() }),
      )
      notifySuccess(t('debts.notice.closed'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onReopenDebt = async (debt: ReadDebt): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateDebt(token, activeLedgerId, debt.id, base, { closed_at: null }),
      )
      notifySuccess(t('debts.notice.reopened'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  // 雙向勾稽(體驗補強):還款記錄點一下,查到完整交易後用全域事件開
  // TransactionDetailDialog(跟 GlobalEntityDialogs.tsx::handleJumpToTx 同一招,
  // 這裡不用經過它本身也能觸發同一個全域 detail dialog)。
  const onJumpToTx = async (txId: string): Promise<void> => {
    try {
      const page = await fetchWorkspaceTransactions(token, { txSyncId: txId, limit: 1 })
      const found = page.items[0]
      if (!found) {
        toast.error(t('transactions.error.jumpTargetNotFound'), t('notice.error'))
        return
      }
      dispatchOpenDetailTx(found)
    } catch (err) {
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.debts')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <DebtsPanel
            debts={debts}
            accounts={accounts}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            onRecordRepayment={onRecordRepayment}
            onJumpToTx={(txId) => void onJumpToTx(txId)}
            onCloseDebt={onCloseDebt}
            onReopenDebt={onReopenDebt}
            highlightDebtId={highlightDebtId}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
