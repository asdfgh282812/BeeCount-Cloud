import { useCallback, useEffect, useState } from 'react'

import {
  applyTxTemplate,
  createTxTemplate,
  deleteTxTemplate,
  fetchCardRewardRules,
  fetchReadProjects,
  fetchReadTxTemplates,
  fetchWorkspaceAccounts,
  fetchWorkspaceCategories,
  fetchWorkspaceTags,
  updateTxTemplate,
  type ReadAccount,
  type ReadCardRewardRule,
  type ReadProject,
  type ReadTxTemplate,
  type TxTemplateApplyPayload,
  type TxTemplateCreatePayload,
  type WorkspaceCategory,
  type WorkspaceTag,
} from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'
import { TxTemplatesPanel, txTemplateDefaults, type TxTemplateForm } from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { useLedgerWrite } from '../../app/useLedgerWrite'

/**
 * 交易範本页(MOZE_FEATURE_GAP_SD.md §2.7 Phase 3)—— 结构照抄 BudgetsPage:
 * 账本级实体走 fetchReadTxTemplates,categories/accounts 是 user-global
 * 复用现有 fetch。「套用」直接把範本内容套成一笔新交易。
 *
 * 只有账本 owner 能新建/编辑/删除範本(server `_OWNER_ONLY_ROLES`),`canManage`
 * 按 `currentLedger.role === 'owner'` 收窄;套用範本走一般交易写权限
 * (owner + editor 都可以),不受这个开关限制。
 */
export function TxTemplatesPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()

  const bucket = activeLedgerId || '__none__'
  const [templates, setTemplates] = usePageCache<ReadTxTemplate[]>(`txTemplates:${bucket}:rows`, [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>('txTemplates:categories', [])
  const [accounts, setAccounts] = usePageCache<ReadAccount[]>(`txTemplates:${bucket}:accounts`, [])
  const [tags, setTags] = usePageCache<WorkspaceTag[]>('txTemplates:tags', [])
  const [projects, setProjects] = usePageCache<ReadProject[]>(`txTemplates:${bucket}:projects`, [])
  const [rewardRules, setRewardRules] = useState<ReadCardRewardRule[]>([])
  const [form, setForm] = useState<TxTemplateForm>(txTemplateDefaults())

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
      setTemplates([])
      setAccounts([])
      return
    }
    try {
      const [r, c, a, tg, pj] = await Promise.all([
        fetchReadTxTemplates(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
        fetchWorkspaceAccounts(token, { limit: 500 }),
        fetchWorkspaceTags(token, { limit: 500 }),
        fetchReadProjects(token, activeLedgerId),
      ])
      setTemplates(r)
      setCategories(c)
      setAccounts(a)
      setTags(tg)
      setProjects(pj)
    } catch (err) {
      notifyError(err)
    }
    // setTemplates / setCategories / setAccounts / setTags / setProjects 来自 usePageCache,引用稳定
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

  // 表單選到信用卡支出帳戶時,抓該卡名下的回饋規則給「紅利回饋規則」勾選。
  useEffect(() => {
    const account = accounts.find((row) => row.id === form.account_id)
    if (!activeLedgerId || form.tx_type !== 'expense' || account?.account_type !== 'credit_card') {
      setRewardRules([])
      return
    }
    let cancelled = false
    fetchCardRewardRules(token, activeLedgerId, account.id)
      .then((rows) => {
        if (!cancelled) setRewardRules(rows)
      })
      .catch(() => {
        if (!cancelled) setRewardRules([])
      })
    return () => {
      cancelled = true
    }
  }, [token, activeLedgerId, accounts, form.account_id, form.tx_type])

  const canManage = Boolean(activeLedgerId) && currentLedger?.role === 'owner'

  /** 表單 → API payload(create/update 共用;update 靠 null 清空欄位)。 */
  const buildPayload = (): TxTemplateCreatePayload => {
    const isTransfer = form.tx_type === 'transfer'
    const num = (raw: string): number | null => {
      const trimmed = raw.trim()
      if (!trimmed) return null
      const n = Number(trimmed)
      return Number.isFinite(n) ? n : null
    }
    return {
      name: form.name.trim(),
      tx_type: form.tx_type,
      amount: num(form.amount) ?? 0,
      note: form.note || null,
      merchant: form.merchant.trim() || null,
      category_id: isTransfer ? null : form.category_id || null,
      account_id: isTransfer ? null : form.account_id || null,
      from_account_id: isTransfer ? form.from_account_id || null : null,
      to_account_id: isTransfer ? form.to_account_id || null : null,
      tag_ids: form.tag_ids,
      project_id: isTransfer ? null : form.project_id || null,
      currency_code: isTransfer ? null : form.currency_code || null,
      to_amount: isTransfer ? num(form.to_amount) : null,
      fee_amount: num(form.fee_amount),
      fee_label: form.fee_label.trim() || null,
      discount_amount: num(form.discount_amount),
      discount_label: form.discount_label.trim() || null,
      exclude_from_stats: isTransfer ? false : form.exclude_from_stats,
      exclude_from_budget: form.tx_type === 'expense' ? form.exclude_from_budget : false,
      reward_rule_ids: form.tx_type === 'expense' ? form.reward_rule_ids : [],
    }
  }

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const amountRaw = (form.amount || '').toString().trim()
    // 金額可留空(= 套用時再填),但填了就必須是 >= 0 的數字。
    if (amountRaw !== '' && (!Number.isFinite(Number(amountRaw)) || Number(amountRaw) < 0)) {
      toast.error(t('recurringRules.error.amountInvalid'), t('notice.error'))
      return false
    }
    if (!form.name.trim()) {
      toast.error(t('txTemplates.error.nameRequired'), t('notice.error'))
      return false
    }
    if (form.tx_type === 'transfer' && (!form.from_account_id || !form.to_account_id)) {
      toast.error(t('transactions.error.transferAccountsRequired'), t('notice.error'))
      return false
    }
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateTxTemplate(token, activeLedgerId, form.editingId!, base, buildPayload()),
        )
        notifySuccess(t('txTemplates.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createTxTemplate(token, activeLedgerId, base, buildPayload()),
        )
        notifySuccess(t('txTemplates.notice.created'))
      }
      setForm(txTemplateDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (template: ReadTxTemplate): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteTxTemplate(token, activeLedgerId, template.id, base),
      )
      notifySuccess(t('txTemplates.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onApply = async (template: ReadTxTemplate, payload: TxTemplateApplyPayload): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        applyTxTemplate(token, activeLedgerId, template.id, base, payload),
      )
      notifySuccess(t('txTemplates.notice.applied'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('nav.txTemplates')}</CardTitle>
      </CardHeader>
      <CardContent>
        {!activeLedgerId ? (
          <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
        ) : (
          <TxTemplatesPanel
            templates={templates}
            categories={categories}
            accounts={accounts}
            tags={tags}
            projects={projects}
            rewardRules={rewardRules}
            iconPreviewUrlByFileId={iconPreviewByFileId}
            currency={currency}
            form={form}
            onFormChange={setForm}
            onSubmit={onSubmit}
            onDelete={onDelete}
            onApply={onApply}
            canManage={canManage}
          />
        )}
      </CardContent>
    </Card>
  )
}
