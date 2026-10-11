import { useState } from 'react'

import {
  AmountInput,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  useT
} from '@beecount/ui'

import type {
  DepositBalanceCompletePayload,
  ReadAccount,
  ReadDepositBalance
} from '@beecount/api-client'

import { AccountPickerDialog } from './AccountPickerDialog'
import { DatePicker } from './DatePicker'
import { DateTimePicker } from './DateTimePicker'
import { depositDateToWire, parseDepositAmount } from '../lib/depositBalance'

const fieldButtonClass =
  'flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50'

function nowLocalInput(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

type Props = {
  /** 要補尾款的那一筆;null = 關閉。 */
  balance: ReadDepositBalance | null
  accounts: readonly ReadAccount[]
  onClose: () => void
  /** 回傳 true 代表成功(對話框自行關閉)。 */
  onSubmit: (balance: ReadDepositBalance, payload: DepositBalanceCompletePayload) => Promise<boolean>
}

/**
 * 補尾款對話框(待完成交易頁 + 交易詳情共用):金額(尾款未定時必填)、
 * 扣款帳戶(不選 = 沿用訂金交易的帳戶)、入帳時間、延後入帳日、備註。
 * 送出後 server 同一次寫入建立真實交易(類型/類別/專案/商家沿用訂金交易)。
 */
export function DepositBalanceCompleteDialog({ balance, accounts, onClose, onSubmit }: Props) {
  const t = useT()
  const [amount, setAmount] = useState('')
  const [accountId, setAccountId] = useState('')
  const [at, setAt] = useState('')
  const [deferred, setDeferred] = useState('')
  const [note, setNote] = useState('')
  const [accountOpen, setAccountOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 開啟時(balance 由 null → 有值/換一筆)重設表單:用 render-time 比對,
  // 跟 DebtsPanel 的 RepayDialog 同一招,避免 effect 多一輪 render 閃舊值。
  const [lastId, setLastId] = useState<string | null>(null)
  const currentId = balance?.id ?? null
  if (currentId !== lastId) {
    setLastId(currentId)
    if (balance) {
      setAmount(balance.amount != null ? String(balance.amount) : '')
      setAccountId('')
      setAt(nowLocalInput())
      setDeferred('')
      setNote('')
      setError(null)
      setSaving(false)
    }
  }

  const accountName = accounts.find((a) => a.id === accountId)?.name
  const parsedAmount = parseDepositAmount(amount)

  const submit = async () => {
    if (!balance) return
    if (parsedAmount === null) {
      setError(t('depositBalance.error.amountInvalid') as string)
      return
    }
    setError(null)
    setSaving(true)
    try {
      const ok = await onSubmit(balance, {
        amount: parsedAmount,
        account_id: accountId || null,
        happened_at: at ? new Date(at).toISOString() : null,
        deferred_posting_at: depositDateToWire(deferred),
        note: note.trim() || null
      })
      if (ok) onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={balance !== null} onOpenChange={(next) => !next && !saving && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('depositBalance.complete.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('depositBalance.complete.amount')}</Label>
            <AmountInput
              data-testid="deposit-complete-amount"
              value={amount}
              onChange={setAmount}
            />
            {balance && balance.amount == null ? (
              <p className="text-xs text-muted-foreground">{t('depositBalance.complete.amountHint')}</p>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.account')}</Label>
            <button type="button" onClick={() => setAccountOpen(true)} className={fieldButtonClass}>
              <span className={`flex-1 truncate ${accountName ? '' : 'text-muted-foreground'}`}>
                {accountName || t('depositBalance.complete.accountInherit')}
              </span>
              <span className="text-xs text-muted-foreground opacity-60">▾</span>
            </button>
          </div>
          <div className="space-y-1">
            <Label>{t('depositBalance.complete.happenedAt')}</Label>
            <DateTimePicker value={at} onChange={setAt} />
          </div>
          <div className="space-y-1">
            <Label>{t('tx.form.deferredPostingAt.label')}</Label>
            <DatePicker value={deferred} onChange={setDeferred} clearable />
          </div>
          <div className="space-y-1">
            <Label>{t('transactions.table.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={saving} onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
          <Button data-testid="deposit-complete-submit" disabled={saving} onClick={() => void submit()}>
            {t('depositBalance.complete.submit')}
          </Button>
        </DialogFooter>
      </DialogContent>
      <AccountPickerDialog
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        accounts={accounts as ReadAccount[]}
        value={accountName || ''}
        allowNone
        noneLabel={t('depositBalance.complete.accountInherit') as string}
        title={t('transactions.table.account') as string}
        onSelect={(row) => {
          setAccountId(row.id)
          setAccountOpen(false)
        }}
      />
    </Dialog>
  )
}
