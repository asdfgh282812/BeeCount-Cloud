import { useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  completeDepositBalance,
  deleteDepositBalance,
  fetchReadDepositBalances,
  fetchWorkspaceAccounts,
  fetchWorkspaceTransactions,
  updateDepositBalance,
  type DepositBalanceCompletePayload,
  type DepositBalanceUpdatePayload,
  type ReadAccount,
  type ReadDepositBalance,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import { DepositBalancesPanel } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { dispatchOpenDetailTx } from '../../lib/txDialogEvents'

/**
 * 待完成交易頁(訂金尾款追蹤,doc.moze.app/record/deposit)—— 結構照抄 DebtsPage:
 * 帳本級實體走 fetchReadDepositBalances,pending 的尾款不是交易(不進統計/餘額),
 * 補尾款由 server 同一次寫入建立真實交易。
 *
 * 只有帳本 owner 能補/編輯/刪除(server `_OWNER_ONLY_ROLES`),`canManage` 收窄。
 * 建立尾款追蹤在「記一筆/編輯交易」表單與交易詳情裡,這頁不提供新建入口
 * (尾款一定掛在某筆訂金交易上)。
 */
export function DepositBalancesPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const [searchParams] = useSearchParams()
  const highlightId = searchParams.get('highlight')

  const bucket = activeLedgerId || '__none__'
  const [balances, setBalances] = usePageCache<ReadDepositBalance[]>(`depositBalances:${bucket}:rows`, [])
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(`depositBalances:${bucket}:accounts`, [])

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
      setBalances([])
      setAccounts([])
      return
    }
    try {
      const [b, a] = await Promise.all([
        fetchReadDepositBalances(token, activeLedgerId),
        fetchWorkspaceAccounts(token, { limit: 500 }),
      ])
      setBalances(b)
      // 隱藏帳戶不給選(同交易表單);群組容器由 AccountPickerDialog 自己處理。
      setAccounts(a.filter((x) => !x.hidden))
    } catch (err) {
      notifyError(err)
    }
    // setBalances / setAccounts 來自 usePageCache,引用穩定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  // 從通知/連結跳轉過來(?highlight=<id>)時,資料到齊後捲到對應列。
  useEffect(() => {
    if (!highlightId || balances.length === 0) return
    const el = document.getElementById(`deposit-balance-${highlightId}`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlightId, balances.length])

  const canManage = Boolean(activeLedgerId) && currentLedger?.role === 'owner'

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

  const onComplete = (balance: ReadDepositBalance, payload: DepositBalanceCompletePayload) =>
    runWrite(
      (ledgerId) =>
        retryOnConflict(ledgerId, (base) =>
          completeDepositBalance(token, ledgerId, balance.id, base, payload),
        ),
      'depositBalance.notice.completed',
    )

  const onUpdate = (balance: ReadDepositBalance, payload: DepositBalanceUpdatePayload) =>
    runWrite(
      (ledgerId) =>
        retryOnConflict(ledgerId, (base) =>
          updateDepositBalance(token, ledgerId, balance.id, base, payload),
        ),
      'depositBalance.notice.updated',
    )

  const onDelete = async (balance: ReadDepositBalance): Promise<void> => {
    await runWrite(
      (ledgerId) =>
        retryOnConflict(ledgerId, (base) => deleteDepositBalance(token, ledgerId, balance.id, base)),
      'depositBalance.notice.deleted',
    )
  }

  // 點訂金/尾款交易:查到完整交易後用全域事件開 TransactionDetailDialog
  // (同 DebtsPage::onJumpToTx)。
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
        <CardTitle>{t('nav.depositBalances')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <DepositBalancesPanel
            balances={balances}
            accounts={accounts}
            currency={currency}
            onComplete={onComplete}
            onUpdate={onUpdate}
            onDelete={onDelete}
            onJumpToTx={(txId) => void onJumpToTx(txId)}
            highlightId={highlightId}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
