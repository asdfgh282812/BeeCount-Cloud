import { useMemo, useState } from 'react'

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
  DebtDirection,
  ReadAccount,
  ReadCategory,
  ReadDebt,
  WorkspaceCategory
} from '@beecount/api-client'

import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { DatePicker } from '../components/DatePicker'
import { DateTimePicker } from '../components/DateTimePicker'
import {
  debtEntryAmountLabelKey,
  debtEntryIsCardInstallment,
  debtEntryIsExisting,
  splitInstallmentAmounts,
  type DebtEntryForm as DebtEntryFormState
} from '../forms'

type DebtEntryFormProps = {
  direction: DebtDirection
  form: DebtEntryFormState
  onChange: (next: DebtEntryFormState) => void
  accounts: readonly ReadAccount[]
  categories: readonly ReadCategory[]
  /** 對象建議:歷史欠款裡出現過的對象,依次數排序。 */
  debts?: readonly ReadDebt[]
  iconPreviewUrlByFileId?: Record<string, string>
  onCreateCategory?: (
    name: string,
    kind: 'expense' | 'income' | 'receivable' | 'payable'
  ) => Promise<WorkspaceCategory | null>
  disabled?: boolean
}

const fieldButtonClass =
  'flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50'

function isoToLocal(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function localToIso(local: string): string {
  if (!local) return ''
  const d = new Date(local)
  return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

/**
 * 記帳對話框的「應收」「應付」分頁(App v68 MOZE 化,對齊 App
 * `lib/widgets/transaction/debt_entry_form.dart`)。版面跟支出/收入一致:
 * 分類 → 金額 → 對象/備註 → 帳戶/日期 → 到期日 → 進階設定。進階設定彈窗
 * 有單次/分期、款項類型(新借出・既有・代刷分期)、期數、首期日、收還款帳戶、
 * 不納入總餘額。送出由外層對話框的按鈕負責(`buildDebtEntryPayload`)。
 */
export function DebtEntryForm({
  direction,
  form,
  onChange,
  accounts,
  categories,
  debts = [],
  iconPreviewUrlByFileId,
  onCreateCategory,
  disabled
}: DebtEntryFormProps) {
  const t = useT()
  const payable = direction === 'payable'
  const [categoryOpen, setCategoryOpen] = useState(false)
  const [accountOpen, setAccountOpen] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)

  const existing = debtEntryIsExisting(form, direction)
  const card = debtEntryIsCardInstallment(form, direction)
  const selectedCategory = categories.find((c) => c.id === form.category_id)
  const selectedAccount = accounts.find((a) => a.id === form.account_id)

  const counterpartySuggestions = useMemo(() => {
    const count = new Map<string, number>()
    for (const d of debts) {
      const name = d.counterparty_name.trim()
      if (name) count.set(name, (count.get(name) || 0) + 1)
    }
    return [...count.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name)
  }, [debts])

  const amountNum = Number(form.amount)
  const installmentCount = Number(form.installment_count)
  const perInstallment =
    form.installment && amountNum > 0 && Number.isInteger(installmentCount) && installmentCount >= 2
      ? splitInstallmentAmounts(amountNum, installmentCount)[0]
      : null

  const kindLabel = card
    ? t('debtEntry.kind.card')
    : t(`debtEntry.kind.${direction}.${form.kind}`)
  const advancedSummary = [
    form.installment
      ? t('debtEntry.summary.installment').replace('{count}', form.installment_count || '?')
      : t('debtEntry.summary.single'),
    kindLabel,
    form.excluded_from_total ? t('debtEntry.excludedFromTotal') : null
  ]
    .filter(Boolean)
    .join(' · ')

  const handleCreateCategory = onCreateCategory
    ? async (name: string) => {
        const created = await onCreateCategory(name, direction)
        if (created) {
          onChange({ ...form, category_id: created.id })
          setCategoryOpen(false)
        }
      }
    : undefined

  return (
    <>
      <div className="space-y-1 md:col-span-2">
        <Label>{t('transactions.table.category')}</Label>
        <button
          type="button"
          data-testid="debt-entry-category"
          disabled={disabled}
          onClick={() => setCategoryOpen(true)}
          className={fieldButtonClass}
        >
          {selectedCategory ? (
            <>
              <CategoryIcon
                icon={selectedCategory.icon}
                iconType={selectedCategory.icon_type}
                iconCloudFileId={selectedCategory.icon_cloud_file_id}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                size={18}
              />
              <span className="flex-1 truncate">{selectedCategory.name}</span>
            </>
          ) : (
            <span className="flex-1 truncate text-muted-foreground">
              {t('transactions.placeholder.categoryName')}
            </span>
          )}
          <span className="text-xs text-muted-foreground opacity-60">▾</span>
        </button>
      </div>

      <div className="space-y-1 md:col-span-2">
        <Label>{t(debtEntryAmountLabelKey(form, direction))}</Label>
        <AmountInput
          data-testid="debt-entry-amount"
          placeholder="0"
          value={form.amount}
          onChange={(value) => onChange({ ...form, amount: value })}
        />
        {perInstallment !== null ? (
          <p className="text-xs text-muted-foreground">
            {t('debtEntry.perInstallment').replace('{amount}', perInstallment.toLocaleString())}
          </p>
        ) : null}
      </div>

      <div className="space-y-1">
        <Label>{t('debtEntry.counterparty')}</Label>
        <Input
          data-testid="debt-entry-counterparty"
          list="debt-entry-counterparties"
          value={form.counterparty_name}
          placeholder={t('debtEntry.counterpartyPlaceholder') as string}
          onChange={(e) => onChange({ ...form, counterparty_name: e.target.value })}
        />
        <datalist id="debt-entry-counterparties">
          {counterpartySuggestions.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </div>

      <div className="space-y-1">
        <Label>{t('transactions.table.note')}</Label>
        <Input
          value={form.note}
          placeholder={t('transactions.table.note') as string}
          onChange={(e) => onChange({ ...form, note: e.target.value })}
        />
      </div>

      {existing ? null : (
        <div className="space-y-1">
          <Label>{card ? t('debtEntry.cardAccount') : t('transactions.table.account')}</Label>
          <button
            type="button"
            data-testid="debt-entry-account"
            disabled={disabled}
            onClick={() => setAccountOpen(true)}
            className={fieldButtonClass}
          >
            <span className={`flex-1 truncate ${selectedAccount ? '' : 'text-muted-foreground'}`}>
              {selectedAccount?.name || t('transactions.placeholder.accountName')}
            </span>
            <span className="text-xs text-muted-foreground opacity-60">▾</span>
          </button>
        </div>
      )}

      {card ? null : (
        <div className="space-y-1">
          <Label>{existing ? t('debtEntry.registeredAt') : t('transactions.table.time')}</Label>
          <DateTimePicker
            value={isoToLocal(form.started_at)}
            onChange={(next) => onChange({ ...form, started_at: localToIso(next) })}
          />
        </div>
      )}

      <div className="space-y-1">
        <Label>{t('debtEntry.dueDate')}</Label>
        <DatePicker
          value={form.due_date}
          onChange={(next) => onChange({ ...form, due_date: next })}
          clearable
        />
      </div>

      <div className="space-y-1 md:col-span-2">
        <Label>{t('debtEntry.advanced')}</Label>
        <button
          type="button"
          data-testid="debt-entry-advanced"
          onClick={() => setAdvancedOpen(true)}
          className={fieldButtonClass}
        >
          <span className="flex-1 truncate">{advancedSummary}</span>
          <span className="text-xs text-muted-foreground opacity-60">›</span>
        </button>
      </div>

      <p className="text-xs text-muted-foreground md:col-span-2">{t('debtEntry.notCounted')}</p>

      <CategoryPickerDialog
        open={categoryOpen}
        onClose={() => setCategoryOpen(false)}
        kind={direction}
        rows={categories as WorkspaceCategory[]}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || undefined}
        title={t('transactions.placeholder.categoryName')}
        onCreateNew={handleCreateCategory}
        onSelect={(cat) => {
          onChange({ ...form, category_id: cat.id })
          setCategoryOpen(false)
        }}
      />

      <AccountPickerDialog
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        title={card ? (t('debtEntry.cardAccount') as string) : (t('transactions.table.account') as string)}
        accounts={accounts as ReadAccount[]}
        value={selectedAccount?.name}
        onSelect={(row) => {
          onChange({ ...form, account_id: row.id })
          setAccountOpen(false)
        }}
      />

      <DebtAdvancedDialog
        open={advancedOpen}
        onClose={() => setAdvancedOpen(false)}
        direction={direction}
        form={form}
        accounts={accounts}
        onApply={(next) => {
          onChange(next)
          setAdvancedOpen(false)
        }}
        payable={payable}
      />
    </>
  )
}

type DebtAdvancedDialogProps = {
  open: boolean
  onClose: () => void
  direction: DebtDirection
  payable: boolean
  form: DebtEntryFormState
  accounts: readonly ReadAccount[]
  onApply: (next: DebtEntryFormState) => void
}

/** 「進階設定」:編輯一份草稿,按「完成」才寫回,同 App `_DebtAdvancedSheet`。 */
function DebtAdvancedDialog({
  open,
  onClose,
  direction,
  payable,
  form,
  accounts,
  onApply
}: DebtAdvancedDialogProps) {
  const t = useT()
  const [draft, setDraft] = useState(form)
  const [scheduleAccountOpen, setScheduleAccountOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 每次打開都從目前表單重新開始(取消 = 不寫回)。
  const [lastOpen, setLastOpen] = useState(false)
  if (open !== lastOpen) {
    setLastOpen(open)
    if (open) {
      setDraft(form)
      setError(null)
    }
  }

  const card = debtEntryIsCardInstallment(draft, direction)
  const existing = debtEntryIsExisting(draft, direction)
  const scheduleAccount = accounts.find((a) => a.id === draft.schedule_account_id)

  const kinds: Array<{ key: string; active: boolean; apply: () => DebtEntryFormState }> = [
    {
      key: `debtEntry.kind.${direction}.new`,
      active: draft.kind === 'new' && !card,
      apply: () => ({ ...draft, kind: 'new', card_installment: false })
    },
    {
      key: `debtEntry.kind.${direction}.existing`,
      active: draft.kind === 'existing',
      apply: () => ({ ...draft, kind: 'existing', card_installment: false })
    }
  ]
  if (!payable && draft.installment) {
    kinds.push({
      key: 'debtEntry.kind.card',
      active: card,
      apply: () => ({ ...draft, kind: 'new', card_installment: true })
    })
  }

  const hintKey = card
    ? 'debtEntry.hint.card'
    : `debtEntry.hint.${direction}.${draft.kind}`
  const firstAtKey = card
    ? 'debtEntry.firstAt.card'
    : `debtEntry.firstAt.${direction}${existing ? '.existing' : ''}`

  const handleDone = () => {
    if (draft.installment) {
      const count = Number(draft.installment_count)
      if (!Number.isInteger(count) || count < 2 || count > 600) {
        setError(t('debtEntry.error.installmentCount') as string)
        return
      }
    }
    onApply(draft)
  }

  const segmentClass = (active: boolean) =>
    `flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
      active ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
    }`
  const chipClass = (active: boolean) =>
    `rounded-full border px-3 py-1 text-sm transition-colors ${
      active
        ? 'border-primary/60 bg-primary/10 text-primary'
        : 'border-border/60 text-muted-foreground hover:bg-accent/40'
    }`

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t('debtEntry.advanced')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="flex gap-1 rounded-lg bg-muted p-1" role="tablist">
            <button
              type="button"
              role="tab"
              data-testid="debt-advanced-single"
              aria-selected={!draft.installment}
              className={segmentClass(!draft.installment)}
              onClick={() => setDraft({ ...draft, installment: false, card_installment: false })}
            >
              {t('debtEntry.single')}
            </button>
            <button
              type="button"
              role="tab"
              data-testid="debt-advanced-installment"
              aria-selected={draft.installment}
              className={segmentClass(draft.installment)}
              onClick={() => setDraft({ ...draft, installment: true })}
            >
              {t('debtEntry.installment')}
            </button>
          </div>

          <div className="space-y-2">
            <Label>{t('debtEntry.kindLabel')}</Label>
            <div className="flex flex-wrap gap-2">
              {kinds.map((k) => (
                <button
                  key={k.key}
                  type="button"
                  data-testid={`debt-kind-${k.key.split('.').pop()}`}
                  className={chipClass(k.active)}
                  onClick={() => setDraft(k.apply())}
                >
                  {k.active ? '✓ ' : ''}
                  {t(k.key)}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{t(hintKey)}</p>
          </div>

          {draft.installment ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label>
                  {existing ? t('debtEntry.installmentCount.remaining') : t('debtEntry.installmentCount.total')}
                </Label>
                <Input
                  data-testid="debt-advanced-count"
                  type="number"
                  inputMode="numeric"
                  min={2}
                  max={600}
                  value={draft.installment_count}
                  onChange={(e) => setDraft({ ...draft, installment_count: e.target.value })}
                />
              </div>
              <div className="space-y-1">
                <Label>{t(firstAtKey)}</Label>
                <DateTimePicker
                  value={isoToLocal(draft.first_at)}
                  onChange={(next) => setDraft({ ...draft, first_at: localToIso(next) })}
                />
              </div>
              {existing ? (
                <div className="space-y-1 sm:col-span-2">
                  <Label>
                    {payable ? t('debtEntry.scheduleAccount.payable') : t('debtEntry.scheduleAccount.receivable')}
                  </Label>
                  <button
                    type="button"
                    onClick={() => setScheduleAccountOpen(true)}
                    className={fieldButtonClass}
                  >
                    <span className={`flex-1 truncate ${scheduleAccount ? '' : 'text-muted-foreground'}`}>
                      {scheduleAccount?.name || t('common.none')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
            <div>
              <p className="text-sm font-medium">{t('debtEntry.excludedFromTotal')}</p>
              <p className="text-xs text-muted-foreground">{t('debtEntry.excludedFromTotalHint')}</p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={draft.excluded_from_total}
              onClick={() => setDraft({ ...draft, excluded_from_total: !draft.excluded_from_total })}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                draft.excluded_from_total ? 'bg-primary' : 'bg-muted-foreground/30'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                  draft.excluded_from_total ? 'translate-x-[18px]' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button data-testid="debt-advanced-done" onClick={handleDone}>
            {t('debtEntry.done')}
          </Button>
        </DialogFooter>
      </DialogContent>

      <AccountPickerDialog
        open={scheduleAccountOpen}
        onClose={() => setScheduleAccountOpen(false)}
        accounts={accounts as ReadAccount[]}
        value={scheduleAccount?.name}
        allowNone
        noneLabel={t('common.none') as string}
        onSelect={(row) => {
          setDraft({ ...draft, schedule_account_id: row?.id || '' })
          setScheduleAccountOpen(false)
        }}
      />
    </Dialog>
  )
}
