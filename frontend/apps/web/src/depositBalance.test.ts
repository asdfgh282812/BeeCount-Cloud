import { describe, expect, it } from 'vitest'

import type { ReadDepositBalance } from '@beecount/api-client'
import {
  depositBalanceTotal,
  depositDateFromWire,
  depositDateToWire,
  depositFormPatchFromLink,
  depositOriginalKey,
  findDepositBalanceLink,
  isDepositBalanceOverdue,
  isDepositFormActive,
  parseDepositAmount,
  sortCompletedDepositBalances,
  sortPendingDepositBalances,
  txDefaults,
  validateDepositForm
} from '@beecount/web-features'

// 訂金尾款追蹤(doc.moze.app/record/deposit)Web 端純邏輯:純日期編碼、排序、
// 逾期、交易 ⇄ 尾款反查、表單開關規則。

function balance(overrides: Partial<ReadDepositBalance> = {}): ReadDepositBalance {
  return {
    id: 'depbal_1',
    source_tx_id: 'tx_src',
    amount: 300,
    due_at: '2026-10-20T00:00:00Z',
    status: 'pending',
    completed_tx_id: null,
    source_type: 'expense',
    source_amount: 100,
    source_happened_at: '2026-10-01T12:00:00Z',
    source_merchant: '攝影工作室',
    source_note: null,
    source_category_name: '娛樂',
    ...overrides
  }
}

describe('deposit balance date-only encoding', () => {
  it('encodes the picked calendar day as UTC midnight (no timezone shift)', () => {
    expect(depositDateToWire('2026-10-20')).toBe('2026-10-20T00:00:00Z')
    expect(depositDateToWire('')).toBeNull()
    expect(depositDateToWire(null)).toBeNull()
  })

  it('reads the UTC calendar day back, tolerating a missing timezone suffix', () => {
    expect(depositDateFromWire('2026-10-20T00:00:00Z')).toBe('2026-10-20')
    expect(depositDateFromWire('2026-10-20T00:00:00')).toBe('2026-10-20')
    expect(depositDateFromWire('2026-10-20T00:00:00+00:00')).toBe('2026-10-20')
    expect(depositDateFromWire(null)).toBe('')
    expect(depositDateFromWire('garbage')).toBe('')
  })

  it('round-trips regardless of the machine timezone', () => {
    for (const day of ['2026-01-01', '2026-03-29', '2026-12-31']) {
      expect(depositDateFromWire(depositDateToWire(day))).toBe(day)
    }
  })
})

describe('deposit balance amount parsing', () => {
  it('treats empty / invalid / non-positive as undetermined (null)', () => {
    expect(parseDepositAmount('')).toBeNull()
    expect(parseDepositAmount('  ')).toBeNull()
    expect(parseDepositAmount('abc')).toBeNull()
    expect(parseDepositAmount('0')).toBeNull()
    expect(parseDepositAmount('-5')).toBeNull()
    expect(parseDepositAmount('1500.5')).toBe(1500.5)
  })
})

describe('deposit balance overdue + ordering', () => {
  it('only pending balances with a past due date are overdue', () => {
    expect(isDepositBalanceOverdue(balance({ due_at: '2026-10-11T00:00:00Z' }), '2026-10-12')).toBe(true)
    // 到期當天還不算逾期
    expect(isDepositBalanceOverdue(balance({ due_at: '2026-10-12T00:00:00Z' }), '2026-10-12')).toBe(false)
    expect(isDepositBalanceOverdue(balance({ due_at: null }), '2026-10-12')).toBe(false)
    expect(
      isDepositBalanceOverdue(balance({ status: 'completed', due_at: '2026-01-01T00:00:00Z' }), '2026-10-12')
    ).toBe(false)
  })

  it('sorts pending by due date ascending with undated last', () => {
    const sorted = sortPendingDepositBalances([
      balance({ id: 'undated', due_at: null }),
      balance({ id: 'later', due_at: '2026-11-01T00:00:00Z' }),
      balance({ id: 'overdue', due_at: '2026-09-01T00:00:00Z' })
    ])
    expect(sorted.map((b) => b.id)).toEqual(['overdue', 'later', 'undated'])
  })

  it('sorts completed by deposit time, newest first', () => {
    const sorted = sortCompletedDepositBalances([
      balance({ id: 'a', source_happened_at: '2026-09-01T00:00:00Z' }),
      balance({ id: 'b', source_happened_at: '2026-10-01T00:00:00Z' })
    ])
    expect(sorted.map((b) => b.id)).toEqual(['b', 'a'])
  })

  it('totals deposit + balance, treating an undetermined balance as 0', () => {
    expect(depositBalanceTotal(balance())).toBe(400)
    expect(depositBalanceTotal(balance({ amount: null }))).toBe(100)
  })
})

describe('transaction ⇄ deposit balance lookup', () => {
  const pending = balance({ id: 'p', source_tx_id: 'dep1' })
  const done = balance({ id: 'c', source_tx_id: 'dep2', status: 'completed', completed_tx_id: 'bal2' })

  it('finds the balance by source or completed tx id', () => {
    expect(findDepositBalanceLink([pending, done], 'dep1')).toEqual({ role: 'source', balance: pending })
    expect(findDepositBalanceLink([pending, done], 'dep2')).toEqual({ role: 'source', balance: done })
    expect(findDepositBalanceLink([pending, done], 'bal2')).toEqual({ role: 'completed', balance: done })
    expect(findDepositBalanceLink([pending, done], 'other')).toBeNull()
    expect(findDepositBalanceLink([pending, done], null)).toBeNull()
  })

  it('maps a link to tx-form fields', () => {
    expect(depositFormPatchFromLink(null)).toEqual({})
    expect(depositFormPatchFromLink({ role: 'completed', balance: done })).toEqual({ deposit_blocked: true })
    expect(depositFormPatchFromLink({ role: 'source', balance: pending })).toEqual({
      deposit_enabled: true,
      deposit_amount: '300',
      deposit_due_at: '2026-10-20',
      deposit_balance_id: 'p',
      deposit_status: 'pending',
      deposit_original: depositOriginalKey('300', '2026-10-20')
    })
    // 金額/日期未定 → 空字串
    expect(
      depositFormPatchFromLink({ role: 'source', balance: balance({ amount: null, due_at: null }) })
    ).toMatchObject({ deposit_amount: '', deposit_due_at: '' })
  })
})

describe('tx form deposit switch rules', () => {
  const on = { ...txDefaults(), deposit_enabled: true }

  it('is active only for expense/transfer, non-recurring, non-installment, not completed/blocked', () => {
    expect(isDepositFormActive({ ...on, tx_type: 'expense' })).toBe(true)
    expect(isDepositFormActive({ ...on, tx_type: 'transfer' })).toBe(true)
    expect(isDepositFormActive({ ...on, tx_type: 'income' })).toBe(false)
    expect(isDepositFormActive({ ...on, recurring_enabled: true })).toBe(false)
    expect(isDepositFormActive({ ...on, installment_enabled: true })).toBe(false)
    expect(isDepositFormActive({ ...on, deposit_blocked: true })).toBe(false)
    expect(isDepositFormActive({ ...on, deposit_status: 'completed' })).toBe(false)
    expect(isDepositFormActive({ ...txDefaults(), deposit_enabled: false })).toBe(false)
  })

  it('validates: empty amount is fine (TBD), a non-positive amount is not', () => {
    expect(validateDepositForm({ ...on, deposit_amount: '' })).toBeNull()
    expect(validateDepositForm({ ...on, deposit_amount: '500' })).toBeNull()
    expect(validateDepositForm({ ...on, deposit_amount: '0' })).toBe('depositBalance.error.amountInvalid')
    expect(validateDepositForm({ ...on, deposit_amount: 'x' })).toBe('depositBalance.error.amountInvalid')
    // 開關沒開時不驗證
    expect(validateDepositForm({ ...txDefaults(), deposit_amount: 'x' })).toBeNull()
  })
})
