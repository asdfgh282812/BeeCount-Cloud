import { useCallback, useEffect, useState } from 'react'

import {
  createRecurringRule,
  deleteRecurringRule,
  fetchReadAccounts,
  fetchReadRecurringRules,
  fetchWorkspaceCategories,
  updateRecurringRule,
  type ReadAccount,
  type ReadRecurringRule,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import { RecurringRulesPanel, recurringRuleDefaults, type RecurringRuleForm } from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 週期性收支页(MOZE_FEATURE_GAP_SD.md §2.2)—— 结构照抄 BudgetsPage:
 * 账本级实体走 fetchReadRecurringRules,categories/accounts 是 user-global
 * 复用现有 fetch。到期后由 server 定时任务(每 15 分钟)自动生成交易,这里
 * 不提供"立即生成"操作。
 *
 * 只有账本 owner 能新建/编辑/删除(server `_OWNER_ONLY_ROLES`),`canManage`
 * 按 `currentLedger.role === 'owner'` 收窄。
 */
export function RecurringRulesPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [rules, setRules] = usePageCache<ReadRecurringRule[]>(`recurringRules:${bucket}:rows`, [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(
    'recurringRules:categories',
    [],
  )
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(`recurringRules:${bucket}:accounts`, [])
  const [form, setForm] = useState<RecurringRuleForm>(recurringRuleDefaults())

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
      setRules([])
      setAccounts([])
      return
    }
    try {
      const [r, c, a] = await Promise.all([
        fetchReadRecurringRules(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
        fetchReadAccounts(token, activeLedgerId),
      ])
      setRules(r)
      setCategories(c)
      setAccounts(a)
    } catch (err) {
      notifyError(err)
    }
    // setRules / setCategories / setAccounts 来自 usePageCache,引用稳定
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
    const amount = Number((form.amount || '').toString().trim())
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error(t('recurringRules.error.amountInvalid'), t('notice.error'))
      return false
    }
    const interval = Math.round(Number(form.interval || '1'))
    if (!Number.isFinite(interval) || interval < 1 || interval > 365) {
      toast.error(t('recurringRules.error.intervalInvalid'), t('notice.error'))
      return false
    }
    if (!form.next_run_at.trim()) {
      toast.error(t('recurringRules.error.nextRunAtRequired'), t('notice.error'))
      return false
    }
    if (form.tx_type === 'transfer' && (!form.from_account_id || !form.to_account_id)) {
      toast.error(t('transactions.error.transferAccountsRequired'), t('notice.error'))
      return false
    }
    const nextRunAtIso = new Date(form.next_run_at).toISOString()
    const endAtIso = form.end_at.trim() ? new Date(form.end_at).toISOString() : null
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateRecurringRule(token, activeLedgerId, form.editingId!, base, {
            tx_type: form.tx_type,
            amount,
            note: form.note || null,
            category_id: form.tx_type === 'transfer' ? null : form.category_id || null,
            account_id: form.tx_type === 'transfer' ? null : form.account_id || null,
            from_account_id: form.tx_type === 'transfer' ? form.from_account_id || null : null,
            to_account_id: form.tx_type === 'transfer' ? form.to_account_id || null : null,
            frequency: form.frequency,
            interval,
            next_run_at: nextRunAtIso,
            end_at: endAtIso,
            enabled: form.enabled,
          }),
        )
        notifySuccess(t('recurringRules.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createRecurringRule(token, activeLedgerId, base, {
            tx_type: form.tx_type,
            amount,
            note: form.note || null,
            category_id: form.tx_type === 'transfer' ? null : form.category_id || null,
            account_id: form.tx_type === 'transfer' ? null : form.account_id || null,
            from_account_id: form.tx_type === 'transfer' ? form.from_account_id || null : null,
            to_account_id: form.tx_type === 'transfer' ? form.to_account_id || null : null,
            frequency: form.frequency,
            interval,
            next_run_at: nextRunAtIso,
            end_at: endAtIso,
            enabled: form.enabled,
          }),
        )
        notifySuccess(t('recurringRules.notice.created'))
      }
      setForm(recurringRuleDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (rule: ReadRecurringRule): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteRecurringRule(token, activeLedgerId, rule.id, base),
      )
      notifySuccess(t('recurringRules.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onToggleEnabled = async (rule: ReadRecurringRule, enabled: boolean): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateRecurringRule(token, activeLedgerId, rule.id, base, { enabled }),
      )
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.recurringRules')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <RecurringRulesPanel
            rules={rules}
            categories={categories}
            accounts={accounts}
            iconPreviewUrlByFileId={iconPreviewByFileId}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            onToggleEnabled={onToggleEnabled}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
