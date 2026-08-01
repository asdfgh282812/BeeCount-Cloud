import { useCallback, useEffect, useState } from 'react'

import {
  applyTxTemplate,
  createTxTemplate,
  deleteTxTemplate,
  fetchReadAccounts,
  fetchReadTxTemplates,
  fetchWorkspaceCategories,
  updateTxTemplate,
  type ReadAccount,
  type ReadTxTemplate,
  type TxTemplateApplyPayload,
  type WorkspaceCategory,
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
      const [r, c, a] = await Promise.all([
        fetchReadTxTemplates(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
        fetchReadAccounts(token, activeLedgerId),
      ])
      setTemplates(r)
      setCategories(c)
      setAccounts(a)
    } catch (err) {
      notifyError(err)
    }
    // setTemplates / setCategories / setAccounts 来自 usePageCache,引用稳定
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
          updateTxTemplate(token, activeLedgerId, form.editingId!, base, {
            name: form.name.trim(),
            tx_type: form.tx_type,
            amount,
            note: form.note || null,
            category_id: form.tx_type === 'transfer' ? null : form.category_id || null,
            account_id: form.tx_type === 'transfer' ? null : form.account_id || null,
            from_account_id: form.tx_type === 'transfer' ? form.from_account_id || null : null,
            to_account_id: form.tx_type === 'transfer' ? form.to_account_id || null : null,
          }),
        )
        notifySuccess(t('txTemplates.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createTxTemplate(token, activeLedgerId, base, {
            name: form.name.trim(),
            tx_type: form.tx_type,
            amount,
            note: form.note || null,
            category_id: form.tx_type === 'transfer' ? null : form.category_id || null,
            account_id: form.tx_type === 'transfer' ? null : form.account_id || null,
            from_account_id: form.tx_type === 'transfer' ? form.from_account_id || null : null,
            to_account_id: form.tx_type === 'transfer' ? form.to_account_id || null : null,
          }),
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
