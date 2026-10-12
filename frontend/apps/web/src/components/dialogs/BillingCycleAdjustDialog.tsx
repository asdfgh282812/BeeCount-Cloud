import { useEffect, useState } from 'react'

import type { AccountBillingSummary } from '@beecount/api-client'
import { updateAccount } from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT,
  useToast
} from '@beecount/ui'
import {
  adjustableRange,
  buildAdjustPayload,
  buildResetPayload,
  dateOnly,
  type BillingAdjustScope
} from '@beecount/web-features'

import { localizeError } from '../../i18n/errors'

/**
 * 「調整本期結帳日」(2026-10-12,MOZE 對標,`doc.moze.app/prepare/account/settings#flexible-closing-date`):
 * 銀行這一期的實際結帳日跟帳戶設定的不一樣(連假、臨時調整)時,指定這一期的結帳日。
 * - 僅這一期:寫帳戶的單期覆寫,下一期恢復原本的結帳日;
 * - 每一期都變更:等同把帳戶結帳日改成選到的日子(並清空所有單期覆寫);
 * - 已調整過的期數可以還原預設結帳日。
 *
 * 對應 App `billing_cycle_adjust_sheet.dart`;payload 組裝在 web-features `lib/billingAdjust.ts`。
 * Web 版**不**處理「一併移動延後入帳記錄」(App 有),要移動請在對帳頁逐筆調整。
 */
export function BillingCycleAdjustDialog({
  open,
  onClose,
  summary,
  token,
  ledgerId,
  billingAccountId,
  retryOnConflict,
  onChanged
}: {
  open: boolean
  onClose: () => void
  summary: AccountBillingSummary
  token: string
  ledgerId: string
  /** 實際存放結帳日設定的帳戶(子卡時是它掛靠的主帳戶)。 */
  billingAccountId: string
  retryOnConflict: <T>(ledgerId: string, fn: (base: number) => Promise<T>) => Promise<T>
  onChanged: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [picked, setPicked] = useState('')
  const [scope, setScope] = useState<BillingAdjustScope>('once')
  const [saving, setSaving] = useState(false)

  const monthKey = summary.period_cycle_month_key ?? ''
  const defaultEnd = summary.period_default_cycle_end ? dateOnly(summary.period_default_cycle_end) : ''
  const currentEnd = dateOnly(summary.period_cycle_end)
  const range = adjustableRange(summary)
  const adjusted = !!summary.period_cycle_adjusted

  useEffect(() => {
    if (open) {
      setPicked(currentEnd)
      setScope('once')
    }
  }, [open, currentEnd])

  const outOfRange = !!range && (picked < range.min || picked > range.max)

  const run = async (payload: Parameters<typeof updateAccount>[4], successKey: string) => {
    setSaving(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        updateAccount(token, ledgerId, billingAccountId, base, payload)
      )
      toast.success(t(successKey), t('notice.success'))
      onChanged()
      onClose()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSaving(false)
    }
  }

  const apply = () => {
    if (!picked || !monthKey || !defaultEnd || outOfRange) return
    void run(
      buildAdjustPayload({
        scope,
        picked,
        monthKey,
        defaultEnd,
        overrides: summary.billing_cycle_overrides
      }),
      'cardBilling.adjustCycle.saved'
    )
  }

  const reset = () => {
    if (!monthKey) return
    void run(
      buildResetPayload({ monthKey, overrides: summary.billing_cycle_overrides }),
      'cardBilling.adjustCycle.resetDone'
    )
  }

  return (
    <Dialog open={open} onOpenChange={(v) => (!v ? onClose() : undefined)}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('cardBilling.adjustCycle.title')}</DialogTitle>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">{t('cardBilling.adjustCycle.hint')}</p>
        <div className="grid gap-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">{t('cardBilling.adjustCycle.period')}</span>
            <span className="font-mono">
              {dateOnly(summary.period_cycle_start)} – {currentEnd}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">{t('cardBilling.adjustCycle.current')}</span>
            <span className={`font-mono ${adjusted ? 'text-primary' : ''}`}>{currentEnd}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">{t('cardBilling.adjustCycle.default')}</span>
            <span className="font-mono">{defaultEnd || '-'}</span>
          </div>
        </div>
        <div className="space-y-1">
          <Label>{t('cardBilling.adjustCycle.newDate')}</Label>
          <Input
            type="date"
            value={picked}
            min={range?.min}
            max={range?.max}
            onChange={(e) => setPicked(e.target.value)}
          />
          {outOfRange && range ? (
            <p className="text-xs text-expense">
              {t('cardBilling.adjustCycle.rangeError', { min: range.min, max: range.max })}
            </p>
          ) : null}
        </div>
        <div className="space-y-2">
          {(['once', 'all'] as const).map((value) => (
            <label key={value} className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="radio"
                name="adjust-scope"
                className="mt-1"
                checked={scope === value}
                onChange={() => setScope(value)}
              />
              <span>
                <span className="block">{t(`cardBilling.adjustCycle.scope.${value}`)}</span>
                <span className="block text-xs text-muted-foreground">
                  {value === 'once'
                    ? t('cardBilling.adjustCycle.scope.onceDesc')
                    : t('cardBilling.adjustCycle.scope.allDesc', {
                        day: picked ? Number(picked.slice(8, 10)) : '-'
                      })}
                </span>
              </span>
            </label>
          ))}
        </div>
        <div className="mt-2 flex justify-end gap-2">
          {adjusted ? (
            <button
              type="button"
              disabled={saving}
              className="rounded-md border border-border px-3 py-1.5 text-sm disabled:opacity-50"
              onClick={reset}
            >
              {t('cardBilling.adjustCycle.reset')}
            </button>
          ) : null}
          <button
            type="button"
            disabled={saving || !picked || !monthKey || outOfRange}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={apply}
          >
            {t('cardBilling.adjustCycle.apply')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
