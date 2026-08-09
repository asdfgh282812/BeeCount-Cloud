import { useCallback, useEffect, useState } from 'react'
import { Eye, EyeOff, Loader2 } from 'lucide-react'

import { Button, Card, CardContent, CardHeader, CardTitle, Input, Label, useT, useToast } from '@beecount/ui'
import {
  deleteSwipeSmartKey,
  fetchReadAccounts,
  fetchSwipeSmartCards,
  fetchSwipeSmartKeyStatus,
  saveSwipeSmartKey,
  updateAccount,
  type ReadAccount,
  type SwipeSmartCard,
  type SwipeSmartKeyStatus
} from '@beecount/api-client'
import { SwipeSmartCardMappingDialog } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { localizeError } from '../../i18n/errors'

/**
 * 設定 - SwipeSmart 信用卡刷卡建議整合(Phase 14,
 * docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md §3.3.1)。
 *
 * - Personal API Key:遮罩顯示既有 Key(有的話),提供貼上/顯示切換/儲存/
 *   刪除。明文只在使用者主動點「顯示」時短暫可見,不像 PAT 那樣有「只顯示
 *   一次」的建立流程 —— 這把 Key 是使用者從 SwipeSmart 自己的網頁複製過來的。
 * - 卡片對照:貼過 Key 後才能開啟,批次列出信用卡帳戶 <-> SwipeSmart 卡片。
 */
export function SettingsSwipeSmartSection() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()

  const [status, setStatus] = useState<SwipeSmartKeyStatus | null>(null)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [showDraft, setShowDraft] = useState(false)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const [mappingOpen, setMappingOpen] = useState(false)
  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [cards, setCards] = useState<SwipeSmartCard[]>([])
  const [cardsLoading, setCardsLoading] = useState(false)

  const loadStatus = useCallback(async () => {
    setLoadingStatus(true)
    try {
      setStatus(await fetchSwipeSmartKeyStatus(token))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setLoadingStatus(false)
    }
  }, [token, toast, t])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  const handleSaveKey = async () => {
    const value = draft.trim()
    if (!value) return
    setSaving(true)
    try {
      const next = await saveSwipeSmartKey(token, value)
      setStatus(next)
      setEditing(false)
      setDraft('')
      setShowDraft(false)
      toast.success(t('swipesmart.key.saved'))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteKey = async () => {
    setDeleting(true)
    try {
      await deleteSwipeSmartKey(token)
      setStatus({ has_key: false, masked: null })
      toast.success(t('swipesmart.key.deleted'))
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setDeleting(false)
    }
  }

  const openMapping = async () => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return
    }
    setMappingOpen(true)
    setCardsLoading(true)
    try {
      const [accountRows, cardRows] = await Promise.all([
        fetchReadAccounts(token, activeLedgerId),
        fetchSwipeSmartCards(token)
      ])
      setAccounts(accountRows)
      setCards(cardRows)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setCardsLoading(false)
    }
  }

  const handleMap = async (accountId: string, cardId: string | null) => {
    if (!activeLedgerId) return
    await retryOnConflict(activeLedgerId, (base) =>
      updateAccount(token, activeLedgerId, accountId, base, { swipesmart_card_id: cardId })
    )
  }

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle>{t('swipesmart.title')}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">{t('swipesmart.hint')}</p>

        <div className="rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
          {loadingStatus ? (
            <div className="flex items-center justify-center py-2">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : editing ? (
            <div className="space-y-2">
              <Label>{t('swipesmart.key.label')}</Label>
              <div className="flex items-center gap-2">
                <Input
                  autoFocus
                  type={showDraft ? 'text' : 'password'}
                  placeholder="ssm_..."
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  className="font-mono text-xs"
                  autoComplete="off"
                  spellCheck={false}
                  disabled={saving}
                />
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0"
                  onClick={() => setShowDraft((v) => !v)}
                  disabled={saving}
                  aria-label={showDraft ? (t('swipesmart.key.hide') as string) : (t('swipesmart.key.show') as string)}
                >
                  {showDraft ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </Button>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => void handleSaveKey()} disabled={saving || !draft.trim()}>
                  {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                  {t('common.save')}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setEditing(false)
                    setDraft('')
                    setShowDraft(false)
                  }}
                  disabled={saving}
                >
                  {t('common.cancel')}
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium">{t('swipesmart.key.label')}</p>
                <p className="font-mono text-xs text-muted-foreground">
                  {status?.has_key ? status.masked : t('swipesmart.key.unset')}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => setEditing(true)}>
                  {status?.has_key ? t('swipesmart.key.change') : t('swipesmart.key.add')}
                </Button>
                {status?.has_key ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    onClick={() => void handleDeleteKey()}
                    disabled={deleting}
                  >
                    {deleting ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                    {t('common.delete')}
                  </Button>
                ) : null}
              </div>
            </div>
          )}
        </div>

        {status?.has_key ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-medium">{t('swipesmart.mapping.title')}</p>
              <p className="text-xs text-muted-foreground">{t('swipesmart.mapping.hint')}</p>
            </div>
            <Button size="sm" variant="outline" className="h-7 shrink-0 text-xs" onClick={() => void openMapping()}>
              {t('swipesmart.mapping.open')}
            </Button>
          </div>
        ) : null}
      </CardContent>

      <SwipeSmartCardMappingDialog
        open={mappingOpen}
        onClose={() => setMappingOpen(false)}
        accounts={accounts}
        cards={cards}
        cardsLoading={cardsLoading}
        onMap={handleMap}
      />
    </Card>
  )
}
