import { describe, expect, it } from 'vitest'

import type { ReadDebt } from '@beecount/api-client'
import {
  allocateRepayment,
  buildDebtEntryPayload,
  counterpartyHistory,
  counterpartySummary,
  debtEntryDefaults,
  debtTabOf,
  debtTypeTag,
  groupDebtsByCounterparty,
  installmentDateAt,
  splitInstallmentAmounts,
  validateDebtEntry,
  type DebtEntryForm
} from '@beecount/web-features'

// 應收應付款項 MOZE 化(App v68)Web 端純邏輯:金額/日期規則跟 App
// `debt_repository.dart`、server `services/debt_schedule.py` 同一套。

function debt(overrides: Partial<ReadDebt> = {}): ReadDebt {
  return {
    id: 'd1',
    direction: 'receivable',
    counterparty_name: 'Alan',
    principal_amount: 100,
    remaining_amount: 100,
    status: 'open',
    repayments: [],
    excluded_from_total: false,
    last_change_id: 1,
    kind: 'new',
    started_at: '2026-10-01T04:00:00Z',
    ...overrides
  }
}

function entry(overrides: Partial<DebtEntryForm> = {}): DebtEntryForm {
  return {
    ...debtEntryDefaults(),
    category_id: 'cat-lend',
    amount: '1000',
    counterparty_name: ' Ken ',
    account_id: 'acc-cash',
    started_at: '2026-10-10T04:00:00.000Z',
    ...overrides
  }
}

describe('installment helpers', () => {
  it('splits amounts like the App (remainder on the last one)', () => {
    expect(splitInstallmentAmounts(1000, 3)).toEqual([333, 333, 334])
    expect(splitInstallmentAmounts(10.01, 3)).toEqual([3.33, 3.33, 3.35])
  })

  it('clamps to month end and keeps the local time', () => {
    const first = new Date(2026, 0, 31, 0, 30)
    const second = installmentDateAt(first, 1)
    expect([second.getFullYear(), second.getMonth(), second.getDate(), second.getHours(), second.getMinutes()])
      .toEqual([2026, 1, 28, 0, 30])
    expect(installmentDateAt(first, 12).getFullYear()).toBe(2027)
  })
})

describe('debt entry form', () => {
  it('requires category, amount, counterparty and an account for new entries', () => {
    expect(validateDebtEntry(entry({ category_id: '' }), 'receivable')).toBe('debtEntry.error.category')
    expect(validateDebtEntry(entry({ amount: '0' }), 'receivable')).toBe('debtEntry.error.amount')
    expect(validateDebtEntry(entry({ counterparty_name: '  ' }), 'receivable')).toBe('debtEntry.error.counterparty')
    expect(validateDebtEntry(entry({ account_id: '' }), 'receivable')).toBe('debtEntry.error.account')
    // 既有款項不動帳戶,不用選帳戶。
    expect(validateDebtEntry(entry({ account_id: '', kind: 'existing' }), 'payable')).toBeNull()
    expect(validateDebtEntry(entry({ installment: true, installment_count: '1' }), 'payable'))
      .toBe('debtEntry.error.installmentCount')
  })

  it('builds a new single payload with the origin account', () => {
    const p = buildDebtEntryPayload(entry({ due_date: '2026-10-31', note: ' 學費 ' }), 'payable')
    expect(p).toMatchObject({
      direction: 'payable',
      counterparty_name: 'Ken',
      principal_amount: 1000,
      kind: 'new',
      account_id: 'acc-cash',
      category_id: 'cat-lend',
      note: '學費',
      due_at: '2026-10-31T00:00:00Z',
      installment: null
    })
  })

  it('drops the account for existing entries and keeps the schedule account', () => {
    const p = buildDebtEntryPayload(
      entry({
        kind: 'existing',
        installment: true,
        installment_count: '18',
        first_at: '2026-11-10T04:00:00.000Z',
        schedule_account_id: 'acc-bank'
      }),
      'payable'
    )
    expect(p.account_id).toBeNull()
    expect(p.kind).toBe('existing')
    expect(p.installment).toEqual({
      count: 18,
      first_at: '2026-11-10T04:00:00.000Z',
      schedule_account_id: 'acc-bank',
      card: false
    })
  })

  it('only allows card installments for new receivables', () => {
    const card = entry({ installment: true, card_installment: true, installment_count: '3' })
    expect(buildDebtEntryPayload(card, 'receivable').installment?.card).toBe(true)
    expect(buildDebtEntryPayload(card, 'payable').installment?.card).toBe(false)
    expect(buildDebtEntryPayload({ ...card, kind: 'existing' }, 'receivable').installment?.card).toBe(false)
  })
})

describe('debt list', () => {
  const now = Date.parse('2026-10-10T00:00:00Z')

  it('puts future-started debts in the not-started tab', () => {
    expect(debtTabOf(debt(), now)).toBe('active')
    expect(debtTabOf(debt({ started_at: '2026-11-10T04:00:00Z' }), now)).toBe('notStarted')
    expect(debtTabOf(debt({ status: 'closed' }), now)).toBe('completed')
    // 沒有 started_at 時退回起點交易時間。
    expect(
      debtTabOf(
        debt({ started_at: null, origin_transaction: { id: 't', amount: 1, happened_at: '2026-12-01T00:00:00Z' } }),
        now
      )
    ).toBe('notStarted')
  })

  it('uses the debt category as the tag, then falls back by kind', () => {
    expect(debtTypeTag(debt({ category_name: '代付' }))).toEqual({ text: '代付' })
    expect(debtTypeTag(debt({ kind: 'existing', direction: 'payable' }))).toEqual({ key: 'debts.tag.existingPayable' })
    expect(debtTypeTag(debt({ installment_no: 2 }))).toEqual({ key: 'debts.tag.cardInstallment' })
    expect(debtTypeTag(debt())).toEqual({ key: 'debts.tag.lend' })
  })

  it('allocates oldest first like the App', () => {
    const older = debt({ id: 'a', remaining_amount: 899, started_at: '2026-09-01T00:00:00Z' })
    const newer = debt({ id: 'b', remaining_amount: 165, started_at: '2026-10-01T00:00:00Z' })
    expect(allocateRepayment([newer, older], 1000).map((a) => [a.debt.id, a.amount])).toEqual([
      ['a', 899],
      ['b', 101]
    ])
  })

  it('groups by counterparty with a signed net', () => {
    const groups = groupDebtsByCounterparty([
      debt({ id: 'a', remaining_amount: 300 }),
      debt({ id: 'b', direction: 'payable', remaining_amount: 100 }),
      debt({ id: 'c', status: 'closed', remaining_amount: 50 }),
      debt({ id: 'd', counterparty_name: 'Ken' })
    ])
    const alan = groups.find((g) => g.counterparty === 'Alan')!
    expect(alan.debts).toHaveLength(3)
    expect(alan.net).toBe(200)
  })

  it('builds history newest first with a running net that skips scheduled rows', () => {
    const d = debt({
      principal_amount: 300,
      remaining_amount: 200,
      repaid_amount: 100,
      started_at: '2026-09-01T00:00:00Z',
      origin_transaction: { id: 'o', amount: 300, happened_at: '2026-09-01T00:00:00Z' },
      repayments: [
        { id: 'r2', amount: 100, happened_at: '2026-11-01T00:00:00Z', scheduled: true },
        { id: 'r1', amount: 100, happened_at: '2026-10-01T00:00:00Z' }
      ]
    })
    const history = counterpartyHistory([d])
    expect(history.map((h) => [h.txId, h.runningNet])).toEqual([
      ['r2', 200],
      ['r1', 200],
      ['o', 300]
    ])
    expect(counterpartySummary([d])).toMatchObject({ receivable: 300, collected: 100, receivableRemaining: 200 })
  })
})
