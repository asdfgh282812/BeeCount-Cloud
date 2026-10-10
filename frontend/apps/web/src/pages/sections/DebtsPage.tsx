import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createCategory,
  createDebt,
  deleteDebt,
  fetchReadDebts,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTransactions,
  renameDebtCounterparty,
  repayDebts,
  stopTrackingDebt,
  updateDebt,
  writeOffDebt,
  type DebtCreatePayload,
  type DebtRepayPayload,
  type DebtWriteOffPayload,
  type ReadAccount,
  type ReadDebt,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import { DebtsPanel, debtDefaults, type DebtForm } from '@beecount/web-features'

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
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(`debts:${bucket}:categories`, [])
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
      const [d, a, c] = await Promise.all([
        fetchReadDebts(token, activeLedgerId),
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceCategories(token, { limit: 1000 }),
      ])
      setDebts(d)
      setAccounts(a)
      setCategories(c)
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
        const editingName = form.counterparty_name.trim()
        const original = debts.find((d) => d.id === form.editingId)
        // 對象改名是全域批次操作(對齐 Moze「改名連動該對象所有記錄」),不是
        // 這一筆單獨的欄位更新——名稱變了先呼叫 renameDebtCounterparty,
        // updateDebt 不再帶 counterparty_name。
        if (original && original.counterparty_name !== editingName) {
          await retryOnConflict(activeLedgerId, (base) =>
            renameDebtCounterparty(token, activeLedgerId, {
              old_counterparty_name: original.counterparty_name,
              new_counterparty_name: editingName,
              base_change_id: base,
            }),
          )
        }
        await retryOnConflict(activeLedgerId, (base) =>
          updateDebt(token, activeLedgerId, form.editingId!, base, {
            due_at: dueAtIso,
            note: form.note || null,
            excluded_from_total: form.excluded_from_total,
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
            excluded_from_total: form.excluded_from_total,
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

  /** 包一層:寫入 → 成功提示 → 刷新;衝突時也刷新。回傳是否成功。 */
  const runWrite = async (
    write: (ledgerId: string) => Promise<unknown>,
    successKey: string,
  ): Promise<boolean> => {
    if (!activeLedgerId) return false
    try {
      await write(activeLedgerId)
      notifySuccess(t(successKey))
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  // App v68:新增款項(同記帳對話框的應收/應付分頁)。
  const onCreateEntry = (payload: DebtCreatePayload) =>
    runWrite(
      (ledgerId) => retryOnConflict(ledgerId, (base) => createDebt(token, ledgerId, base, payload)),
      'debts.notice.created',
    )

  // App v68:多筆收還款,每筆欠款各一筆收還款交易(不計收支)。
  const onRepay = (payload: DebtRepayPayload) =>
    runWrite(
      (ledgerId) => retryOnConflict(ledgerId, (base) => repayDebts(token, ledgerId, base, payload)),
      'debts.repayment.notice.recorded',
    )

  const onStopTracking = (debt: ReadDebt) =>
    runWrite(
      (ledgerId) => retryOnConflict(ledgerId, (base) => stopTrackingDebt(token, ledgerId, debt.id, base)),
      'debts.notice.stopped',
    )

  const onWriteOff = (debt: ReadDebt, payload: DebtWriteOffPayload) =>
    runWrite(
      (ledgerId) => retryOnConflict(ledgerId, (base) => writeOffDebt(token, ledgerId, debt.id, base, payload)),
      'debts.notice.writtenOff',
    )

  const onToggleExcluded = async (debt: ReadDebt): Promise<void> => {
    await runWrite(
      (ledgerId) =>
        retryOnConflict(ledgerId, (base) =>
          updateDebt(token, ledgerId, debt.id, base, { excluded_from_total: !debt.excluded_from_total }),
        ),
      debt.excluded_from_total ? 'debts.notice.included' : 'debts.notice.excluded',
    )
  }

  const onCreateCategory = async (
    name: string,
    kind: 'expense' | 'income' | 'receivable' | 'payable',
  ): Promise<WorkspaceCategory | null> => {
    if (!activeLedgerId) return null
    try {
      const res = await retryOnConflict(activeLedgerId, (base) =>
        createCategory(token, activeLedgerId, base, {
          name,
          kind,
          level: 1,
          sort_order: null,
          icon: null,
          icon_type: null,
          custom_icon_path: null,
          icon_cloud_file_id: null,
          icon_cloud_sha256: null,
          parent_name: null,
        }),
      )
      const created: WorkspaceCategory = {
        id: res.entity_id || '',
        name,
        kind,
        level: 1,
        sort_order: null,
        icon: null,
        icon_type: null,
        parent_name: null,
        last_change_id: res.new_change_id,
        ledger_id: activeLedgerId,
        ledger_name: null,
        created_by_user_id: null,
        created_by_email: null,
      }
      setCategories([...categories, created])
      return created
    } catch (err) {
      notifyError(err)
      return null
    }
  }

  const onReopenDebt = async (debt: ReadDebt): Promise<void> => {
    await runWrite(
      (ledgerId) =>
        retryOnConflict(ledgerId, (base) => updateDebt(token, ledgerId, debt.id, base, { closed_at: null })),
      'debts.notice.resumed',
    )
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
            categories={categories}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onCreateEntry={onCreateEntry}
            onCreateCategory={canManage ? onCreateCategory : undefined}
            onDelete={onDelete}
            onRepay={onRepay}
            onStopTracking={onStopTracking}
            onWriteOff={onWriteOff}
            onReopenDebt={onReopenDebt}
            onToggleExcluded={onToggleExcluded}
            onJumpToTx={(txId) => void onJumpToTx(txId)}
            highlightDebtId={highlightDebtId}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
