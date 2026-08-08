import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createInstallmentPlan,
  deleteInstallmentPlan,
  earlyRepayInstallmentPrincipal,
  fetchInstallmentPeriods,
  fetchReadAccounts,
  fetchReadInstallmentPlans,
  fetchWorkspaceCategories,
  payoffInstallmentPlan,
  rebalanceInstallmentPlan,
  terminateInstallmentPlanFuture,
  updateInstallmentPeriod,
  updateInstallmentPlan,
  type InstallmentEarlyRepayPayload,
  type InstallmentPeriodUpdatePayload,
  type InstallmentRebalancePayload,
  type ReadAccount,
  type ReadInstallmentPeriod,
  type ReadInstallmentPlan,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import {
  InstallmentPlansPanel,
  installmentPlanDefaults,
  type InstallmentPlanForm,
} from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 分期付款页(MOZE_FEATURE_GAP_SD.md §2.3)—— 结构照抄 BudgetsPage /
 * RecurringRulesPage。建计画时 server 会同事务生成第一期交易,剩余各期由
 * server 定时任务按月自动推进,这里不提供"手动生成下一期"操作。
 *
 * 只有账本 owner 能新建/编辑/删除(server `_OWNER_ONLY_ROLES`)。
 */
export function InstallmentPlansPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const [searchParams, setSearchParams] = useSearchParams()
  // 2026-08-03 使用者反饋 #4:從交易詳情的編輯選擇彈窗跳轉過來時
  // (?highlight=<planId>&action=editThis|editFuture|earlyRepay|payoff
  // &txId=<txId>),高亮 + 自動打開對應的差異化操作表單,見
  // InstallmentPlansPanel 的 autoOpenAction 處理。
  const highlightPlanId = searchParams.get('highlight')
  const actionParam = searchParams.get('action') as
    | 'editThis'
    | 'editFuture'
    | 'earlyRepay'
    | 'payoff'
    | null
  const txIdParam = searchParams.get('txId')
  const autoOpenAction =
    highlightPlanId && actionParam
      ? { planId: highlightPlanId, action: actionParam, txId: txIdParam || undefined }
      : null
  const handleAutoOpenConsumed = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('action')
        next.delete('txId')
        return next
      },
      { replace: true },
    )
  }, [setSearchParams])

  const bucket = activeLedgerId || '__none__'
  const [plans, setPlans] = usePageCache<ReadInstallmentPlan[]>(
    `installmentPlans:${bucket}:rows`,
    [],
  )
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(
    'installmentPlans:categories',
    [],
  )
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(
    `installmentPlans:${bucket}:accounts`,
    [],
  )
  const [form, setForm] = useState<InstallmentPlanForm>(installmentPlanDefaults())
  // §2.12.1:每个计画的期数明细,懒加载(展开卡片才 fetch),不放进
  // usePageCache —— 只是展开态的临时数据,ledger 切换/刷新时清空即可。
  const [periodsByPlanId, setPeriodsByPlanId] = useState<Record<string, ReadInstallmentPeriod[]>>({})

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
      setPlans([])
      setAccounts([])
      return
    }
    try {
      const [p, c, a] = await Promise.all([
        fetchReadInstallmentPlans(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
        fetchReadAccounts(token, activeLedgerId),
      ])
      setPlans(p)
      setCategories(c)
      setAccounts(a)
      // 期数明细可能因为 rebalance/payoff/terminate 等操作变了,清掉缓存,
      // 下次展开重新 fetch,避免展示过期数据。
      setPeriodsByPlanId({})
    } catch (err) {
      notifyError(err)
    }
    // setPlans / setCategories / setAccounts 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  useEffect(() => {
    const ids = categories
      .map((c) => c.icon_cloud_file_id || '')
      .filter((v) => v.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [categories, ensureLoadedMany])

  // 從交易詳情跳轉過來時(?highlight=<planId>),定位並高亮對應卡片一次
  // (跟 DebtsPage 同款寫法)。
  useEffect(() => {
    if (!highlightPlanId || plans.length === 0) return
    const el = document.getElementById(`installment-plan-${highlightPlanId}`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [highlightPlanId, plans.length])

  // 从信用卡帳單卡片的「新增帳單分期」入口跳转过来时(?account=<id>,
  // §2.9 Phase 4)预填帳戶,使用者只需要填總額/期數/起始日。
  useEffect(() => {
    const accountId = searchParams.get('account')
    if (!accountId || accounts.length === 0) return
    const matched = accounts.find((a) => a.id === accountId)
    if (!matched) return
    setForm((prev) => {
      if (prev.editingId || prev.account_id === accountId) return prev
      return { ...prev, account_id: accountId, account_name: matched.name }
    })
  }, [searchParams, accounts])

  const canManage = Boolean(activeLedgerId) && currentLedger?.role === 'owner'

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    try {
      if (form.editingId) {
        // 编辑模式只允许改 note(status 走独立的"提前结清"按钮)。
        await retryOnConflict(activeLedgerId, (base) =>
          updateInstallmentPlan(token, activeLedgerId, form.editingId!, base, {
            note: form.note || null,
          }),
        )
        notifySuccess(t('installmentPlans.notice.updated'))
      } else {
        const totalAmount = Number((form.total_amount || '').toString().trim())
        if (!Number.isFinite(totalAmount) || totalAmount <= 0) {
          toast.error(t('recurringRules.error.amountInvalid'), t('notice.error'))
          return false
        }
        const periods = Math.round(Number(form.periods || '0'))
        if (!Number.isFinite(periods) || periods < 1 || periods > 600) {
          toast.error(t('installmentPlans.error.periodsInvalid'), t('notice.error'))
          return false
        }
        if (!form.first_period_at.trim()) {
          toast.error(t('installmentPlans.error.firstPeriodAtRequired'), t('notice.error'))
          return false
        }
        // 需求 #14(Phase 12):分期付款恆為 expense、無轉帳語意,分類必填,
        // 避免每期生成的交易漏分類。
        if (!form.category_id.trim()) {
          toast.error(t('transactions.error.categoryRequired'), t('notice.error'))
          return false
        }
        await retryOnConflict(activeLedgerId, (base) =>
          createInstallmentPlan(token, activeLedgerId, base, {
            total_amount: totalAmount,
            periods,
            first_period_at: new Date(form.first_period_at).toISOString(),
            account_id: form.account_id || null,
            category_id: form.category_id || null,
            note: form.note || null,
            repayment_method: form.repayment_method,
            interest_period: form.interest_period,
            interest_rate: Number(form.interest_rate || 0),
            round_amounts: form.round_amounts,
            remainder_position: form.remainder_position,
            grace_period_months: Math.max(0, Math.round(Number(form.grace_period_months) || 0)),
          }),
        )
        notifySuccess(t('installmentPlans.notice.created'))
      }
      setForm(installmentPlanDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (plan: ReadInstallmentPlan): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteInstallmentPlan(token, activeLedgerId, plan.id, base),
      )
      notifySuccess(t('installmentPlans.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  // §2.12.1:期数明细懒加载 —— 展开卡片才 fetch,失败时静默(展开区域自己
  // 会显示"暂无期数明细"兜底,不需要额外 toast 打断)。
  const onLoadPeriods = useCallback(
    (plan: ReadInstallmentPlan) => {
      if (!activeLedgerId) return
      void fetchInstallmentPeriods(token, activeLedgerId, plan.id)
        .then((rows) => setPeriodsByPlanId((prev) => ({ ...prev, [plan.id]: rows })))
        .catch(() => setPeriodsByPlanId((prev) => ({ ...prev, [plan.id]: [] })))
    },
    [token, activeLedgerId],
  )

  const onUpdatePeriod = async (
    plan: ReadInstallmentPlan,
    periodNo: number,
    payload: InstallmentPeriodUpdatePayload,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateInstallmentPeriod(token, activeLedgerId, plan.id, periodNo, base, payload),
      )
      notifySuccess(t('notice.success'))
      onLoadPeriods(plan)
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onRebalance = async (
    plan: ReadInstallmentPlan,
    periodNo: number,
    payload: InstallmentRebalancePayload,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        rebalanceInstallmentPlan(token, activeLedgerId, plan.id, periodNo, base, payload),
      )
      notifySuccess(t('notice.success'))
      onLoadPeriods(plan)
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onEarlyRepay = async (
    plan: ReadInstallmentPlan,
    payload: InstallmentEarlyRepayPayload,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        earlyRepayInstallmentPrincipal(token, activeLedgerId, plan.id, base, payload),
      )
      notifySuccess(t('notice.success'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onPayoff = async (plan: ReadInstallmentPlan): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        payoffInstallmentPlan(token, activeLedgerId, plan.id, base),
      )
      notifySuccess(t('installmentPlans.notice.settled'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onTerminateFuture = async (plan: ReadInstallmentPlan): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        terminateInstallmentPlanFuture(token, activeLedgerId, plan.id, base),
      )
      notifySuccess(t('notice.success'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.installmentPlans')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <InstallmentPlansPanel
            plans={plans}
            categories={categories}
            accounts={accounts}
            iconPreviewUrlByFileId={iconPreviewByFileId}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            periodsByPlanId={periodsByPlanId}
            onLoadPeriods={onLoadPeriods}
            onUpdatePeriod={onUpdatePeriod}
            onRebalance={onRebalance}
            onEarlyRepay={onEarlyRepay}
            onPayoff={onPayoff}
            onTerminateFuture={onTerminateFuture}
            canManage={canManage}
            highlightPlanId={highlightPlanId}
            autoOpenAction={autoOpenAction}
            onAutoOpenConsumed={handleAutoOpenConsumed}
          />
        )}
      </CardContent>
    </Card>
  )
}
