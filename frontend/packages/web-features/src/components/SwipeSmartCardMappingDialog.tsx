import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT
} from '@beecount/ui'

import type { ReadAccount, SwipeSmartCard } from '@beecount/api-client'

const NONE_SENTINEL = '__none__'

type SwipeSmartCardMappingDialogProps = {
  open: boolean
  onClose: () => void
  /** 所有帳戶(內部只挑 account_type === 'credit_card' 的列出來對照)。 */
  accounts: ReadAccount[]
  cards: SwipeSmartCard[]
  cardsLoading?: boolean
  /** 實際寫入的 API 呼叫由呼叫方實作(需要 ledgerId/token/CAS retry,只有
   *  呼叫方知道怎麼打),cardId 傳 null = 使用者選了「未對照」。 */
  onMap: (accountId: string, cardId: string | null) => Promise<void>
}

/**
 * 卡片對照設定視窗(Phase 14 §3.3.1(b),修訂後):批次列出使用者名下所有信用
 * 卡帳戶,名稱跟 SwipeSmart 卡片目錄相近/相同的欄位已經由後端自動比對好(見
 * `src/services/swipesmart_matching.py`,貼 Key 當下 + 每次打開這個視窗都會
 * 重跑一次),這裡只給使用者手動覆蓋/補上比對不到的剩餘欄位。每列改動立即
 * 自動儲存(不是整批「確認」),跟 Settings 頁其它非同步儲存欄位一致的 UX。
 */
export function SwipeSmartCardMappingDialog({
  open,
  onClose,
  accounts,
  cards,
  cardsLoading = false,
  onMap
}: SwipeSmartCardMappingDialogProps) {
  const t = useT()
  const [savingId, setSavingId] = useState<string | null>(null)
  // 樂觀覆蓋:選擇當下立刻反映在 UI,不用等呼叫方重新拉 accounts。存 API 失敗
  // 時把這一列的覆蓋值刪掉,退回 accounts prop 裡的原值。
  const [overrides, setOverrides] = useState<Record<string, string | null>>({})

  const creditCardAccounts = useMemo(
    () => accounts.filter((a) => a.account_type === 'credit_card').sort((a, b) => a.name.localeCompare(b.name)),
    [accounts]
  )

  const cardLabel = (cardId: string): string => {
    const card = cards.find((c) => c.card_id === cardId)
    return card ? `${card.bank_name} ${card.card_name}` : cardId
  }

  const handleChange = async (accountId: string, value: string) => {
    const cardId = value === NONE_SENTINEL ? null : value
    setOverrides((prev) => ({ ...prev, [accountId]: cardId }))
    setSavingId(accountId)
    try {
      await onMap(accountId, cardId)
    } catch {
      setOverrides((prev) => {
        const next = { ...prev }
        delete next[accountId]
        return next
      })
    } finally {
      setSavingId(null)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[80vh] max-w-lg flex-col gap-3 overflow-hidden">
        <DialogHeader>
          <DialogTitle>{t('swipesmart.mapping.title')}</DialogTitle>
          <DialogDescription>{t('swipesmart.mapping.hint')}</DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
          {creditCardAccounts.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {t('swipesmart.mapping.noCreditCards')}
            </p>
          ) : (
            creditCardAccounts.map((account) => {
              const current =
                account.id in overrides ? overrides[account.id] : account.swipesmart_card_id || null
              const isSaving = savingId === account.id
              return (
                <div
                  key={account.id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-border/60 bg-muted/20 px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{account.name}</p>
                    {current ? (
                      <p className="truncate text-xs text-muted-foreground">{cardLabel(current)}</p>
                    ) : (
                      <p className="text-xs text-muted-foreground">{t('swipesmart.mapping.unmapped')}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {isSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
                    <Select
                      value={current ?? NONE_SENTINEL}
                      onValueChange={(value) => void handleChange(account.id, value)}
                      disabled={isSaving || cardsLoading}
                    >
                      <SelectTrigger className="h-8 w-[190px] text-xs">
                        <SelectValue placeholder={t('swipesmart.mapping.pickCard')} />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={NONE_SENTINEL}>{t('swipesmart.mapping.unmapped')}</SelectItem>
                        {cards.map((card) => (
                          <SelectItem key={card.card_id} value={card.card_id}>
                            {card.bank_name} {card.card_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )
            })
          )}
        </div>
        <Button variant="ghost" onClick={onClose}>
          {t('dialog.close')}
        </Button>
      </DialogContent>
    </Dialog>
  )
}

export type { SwipeSmartCardMappingDialogProps }
