import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  createRecurringRule,
  deleteRecurringOccurrence,
  deleteRecurringRule,
  fetchReadAccounts,
  fetchReadRecurringRules,
  fetchReadTransactions,
  fetchWorkspaceCategories,
  terminateRecurringRuleFuture,
  updateRecurringOccurrence,
  updateRecurringRule,
  updateRecurringRuleFrom,
  type ReadAccount,
  type ReadRecurringRule,
  type ReadTransaction,
  type RecurringAdvancedRule,
  type RecurringOccurrenceUpdatePayload,
  type RecurringUpdateFromPayload,
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
  const [searchParams] = useSearchParams()

  const bucket = activeLedgerId || '__none__'
  const [rules, setRules] = usePageCache<ReadRecurringRule[]>(`recurringRules:${bucket}:rows`, [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>(
    'recurringRules:categories',
    [],
  )
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(`recurringRules:${bucket}:accounts`, [])
  const [form, setForm] = useState<RecurringRuleForm>(recurringRuleDefaults())
  // §2.12.2:每个规则已生成的 occurrence 列表,懒加载(展开卡片才 fetch)。
  const [occurrencesByRuleId, setOccurrencesByRuleId] = useState<Record<string, ReadTransaction[]>>({})

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
      setOccurrencesByRuleId({})
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

  // 从信用卡帳單卡片的「設定自動扣繳」入口跳转过来时(?account=<id>&due_day=<n>,
  // §2.9 Phase 4),预填一条 transfer + monthly_day 规则的草稿,使用者只需要
  // 确认金额跟起始日期。用 `to_account_id === accountId` 挡重复触发,避免
  // 使用者手动改掉之后又被这个 effect 打回去。
  useEffect(() => {
    const accountId = searchParams.get('account')
    if (!accountId || accounts.length === 0) return
    const matched = accounts.find((a) => a.id === accountId)
    if (!matched) return
    setForm((prev) => {
      if (prev.editingId || prev.to_account_id === accountId) return prev
      const dueDay = Math.round(Number(searchParams.get('due_day') || ''))
      const next: RecurringRuleForm = {
        ...prev,
        tx_type: 'transfer',
        to_account_id: accountId,
        to_account_name: matched.name,
      }
      if (Number.isFinite(dueDay) && dueDay >= 1 && dueDay <= 31) {
        next.advanced_mode = 'monthly_day'
        next.monthly_day = String(dueDay)
        next.frequency = 'monthly'
      }
      return next
    })
  }, [searchParams, accounts])

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
    // 需求 #14(Phase 12):非轉帳規則分類必填,避免產生的每期交易漏分類
    // (轉帳沒有分類語意,維持不強制)。
    if (form.tx_type !== 'transfer' && !form.category_id.trim()) {
      toast.error(t('transactions.error.categoryRequired'), t('notice.error'))
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
        // Phase 1.5(§2.12.2)进阶规则,只在新建时生效。
        let advancedRuleJson: RecurringAdvancedRule | null = null
        if (form.advanced_mode === 'weekly_days' && form.weekly_days.length > 0) {
          advancedRuleJson = { type: 'weekly_days', days: form.weekly_days }
        } else if (form.advanced_mode === 'monthly_day') {
          const day = Math.round(Number(form.monthly_day))
          if (Number.isFinite(day) && day >= 1 && day <= 31) {
            advancedRuleJson = { type: 'monthly_day', day }
          }
        }
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
            advanced_rule_json: advancedRuleJson,
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

  // §2.12.2:懒加载某规则已生成的 occurrence —— 用现有 fetchReadTransactions
  // 拉一批该账本的交易,按 recurring_rule_id 客户端过滤。跟旧版退款候选清单
  // 同一种已知限制(只看拉到的这批,不是全量搜索)。
  const onLoadOccurrences = useCallback(
    (rule: ReadRecurringRule) => {
      if (!activeLedgerId) return
      void fetchReadTransactions(token, activeLedgerId, { limit: 500 })
        .then((rows) =>
          setOccurrencesByRuleId((prev) => ({
            ...prev,
            [rule.id]: rows.filter((row) => row.recurring_rule_id === rule.id),
          })),
        )
        .catch(() => setOccurrencesByRuleId((prev) => ({ ...prev, [rule.id]: [] })))
    },
    [token, activeLedgerId],
  )

  const onUpdateOccurrence = async (
    rule: ReadRecurringRule,
    txId: string,
    payload: RecurringOccurrenceUpdatePayload,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateRecurringOccurrence(token, activeLedgerId, rule.id, txId, base, payload),
      )
      notifySuccess(t('notice.success'))
      onLoadOccurrences(rule)
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onDeleteOccurrence = async (rule: ReadRecurringRule, txId: string): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteRecurringOccurrence(token, activeLedgerId, rule.id, txId, base),
      )
      notifySuccess(t('notice.success'))
      onLoadOccurrences(rule)
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onUpdateFrom = async (
    rule: ReadRecurringRule,
    txId: string,
    payload: RecurringUpdateFromPayload,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        updateRecurringRuleFrom(token, activeLedgerId, rule.id, txId, base, payload),
      )
      notifySuccess(t('notice.success'))
      onLoadOccurrences(rule)
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onTerminateFuture = async (rule: ReadRecurringRule): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        terminateRecurringRuleFuture(token, activeLedgerId, rule.id, base),
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
            occurrencesByRuleId={occurrencesByRuleId}
            onLoadOccurrences={onLoadOccurrences}
            onUpdateOccurrence={onUpdateOccurrence}
            onDeleteOccurrence={onDeleteOccurrence}
            onUpdateFrom={onUpdateFrom}
            onTerminateFuture={onTerminateFuture}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
