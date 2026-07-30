import { useCallback, useEffect, useState } from 'react'

import {
  createInstallmentPlan,
  deleteInstallmentPlan,
  fetchReadAccounts,
  fetchReadInstallmentPlans,
  fetchWorkspaceCategories,
  updateInstallmentPlan,
  type ReadAccount,
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
        if (!Number.isFinite(periods) || periods < 1 || periods > 120) {
          toast.error(t('installmentPlans.error.periodsInvalid'), t('notice.error'))
          return false
        }
        if (!form.first_period_at.trim()) {
          toast.error(t('installmentPlans.error.firstPeriodAtRequired'), t('notice.error'))
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

  const onSettle = async (plan: ReadInstallmentPlan): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateInstallmentPlan(token, activeLedgerId, plan.id, base, { status: 'settled' }),
      )
      notifySuccess(t('installmentPlans.notice.settled'))
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
            onSettle={onSettle}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
