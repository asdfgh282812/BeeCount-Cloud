import { describe, expect, it } from 'vitest'

import {
  buildTxSplitsPayload,
  txDefaults,
  txSplitFormItemsFromRead,
  validateTxSplits,
  type TxForm
} from '@beecount/web-features'

// 拆帳(§2.4 MOZE_FEATURE_GAP_SD.md Phase 2)web UI —— 表单校验/组 payload 纯
// 函数回归测试,跟 server 端 write/_shared.py::_validate_tx_splits 同一套规则。

function formWithSplits(overrides: Partial<TxForm> = {}): TxForm {
  return {
    ...txDefaults(),
    tx_type: 'expense',
    amount: '200',
    split_enabled: true,
    splits: [
      { category_id: 'cat-a', category_name: '餐饮', amount: '150', note: '' },
      { category_id: 'cat-b', category_name: '交通', amount: '50', note: '' }
    ],
    ...overrides
  }
}

describe('txDefaults split fields', () => {
  it('starts with splits disabled and empty', () => {
    const form = txDefaults()
    expect(form.split_enabled).toBe(false)
    expect(form.splits).toEqual([])
  })
})

describe('validateTxSplits', () => {
  it('passes through when split_enabled is false', () => {
    expect(validateTxSplits(txDefaults(), 100)).toBeNull()
  })

  it('accepts a valid two-row split summing to the amount', () => {
    expect(validateTxSplits(formWithSplits(), 200)).toBeNull()
  })

  it('rejects transfer transactions', () => {
    expect(validateTxSplits(formWithSplits({ tx_type: 'transfer' }), 200)).toBe(
      'transactions.error.splitTransferNotAllowed'
    )
  })

  it('rejects fewer than 2 filled rows', () => {
    const form = formWithSplits({
      splits: [{ category_id: 'cat-a', category_name: '餐饮', amount: '200', note: '' }]
    })
    expect(validateTxSplits(form, 200)).toBe('transactions.error.splitNeedsTwo')
  })

  it('ignores rows without a category when counting filled rows', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '200', note: '' },
        { category_id: '', category_name: '', amount: '', note: '' }
      ]
    })
    expect(validateTxSplits(form, 200)).toBe('transactions.error.splitNeedsTwo')
  })

  it('rejects a non-positive split amount', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '0', note: '' },
        { category_id: 'cat-b', category_name: '交通', amount: '200', note: '' }
      ]
    })
    expect(validateTxSplits(form, 200)).toBe('transactions.error.splitAmountInvalid')
  })

  it('rejects a sum that does not match the transaction amount', () => {
    expect(validateTxSplits(formWithSplits(), 300)).toBe('transactions.error.splitSumMismatch')
  })

  it('tolerates sub-cent floating point drift', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '150.005', note: '' },
        { category_id: 'cat-b', category_name: '交通', amount: '50', note: '' }
      ]
    })
    expect(validateTxSplits(form, 200)).toBeNull()
  })
})

describe('buildTxSplitsPayload', () => {
  it('returns an empty array when split_enabled is false', () => {
    expect(buildTxSplitsPayload(txDefaults())).toEqual([])
  })

  it('maps filled rows to TxSplitPayload, dropping empty placeholder rows', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '150', note: '午饭' },
        { category_id: 'cat-b', category_name: '交通', amount: '50', note: '' },
        { category_id: '', category_name: '', amount: '', note: '' }
      ]
    })
    expect(buildTxSplitsPayload(form)).toEqual([
      { category_id: 'cat-a', category_name: '餐饮', amount: 150, note: '午饭' },
      { category_id: 'cat-b', category_name: '交通', amount: 50, note: null }
    ])
  })
})

// 拆帳欠款明細(App v67):支出拆帳的應收 / 收入拆帳的應付明細。
describe('split debt lines', () => {
  const debtRow = (overrides: Partial<NonNullable<TxForm['splits'][number]['debt']>> = {}) => ({
    category_id: 'cat-advance',
    category_name: '代付',
    amount: '50',
    note: '代墊',
    debt: {
      debt_id: null,
      counterparty_name: '小明',
      due_date: '2026-10-31',
      excluded_from_total: false,
      has_repayments: false,
      ...overrides
    }
  })

  it('accepts a category row plus a debt row', () => {
    const form = formWithSplits({
      splits: [{ category_id: 'cat-a', category_name: '餐饮', amount: '150', note: '' }, debtRow()]
    })
    expect(validateTxSplits(form, 200)).toBeNull()
  })

  it('needs at least one category row', () => {
    const form = formWithSplits({
      amount: '100',
      splits: [debtRow(), debtRow({ counterparty_name: '小華' })]
    })
    expect(validateTxSplits(form, 100)).toBe('transactions.error.splitNeedsCategory')
  })

  it('needs a counterparty on every debt row', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '150', note: '' },
        debtRow({ counterparty_name: '  ' })
      ]
    })
    expect(validateTxSplits(form, 200)).toBe('transactions.error.splitDebtNeedsCounterparty')
  })

  it('builds a debt payload without a split category, due date as UTC midnight', () => {
    const form = formWithSplits({
      splits: [
        { category_id: 'cat-a', category_name: '餐饮', amount: '150', note: '' },
        debtRow({ debt_id: 'debt-1' })
      ]
    })
    expect(buildTxSplitsPayload(form)[1]).toEqual({
      amount: 50,
      note: '代墊',
      debt: {
        debt_id: 'debt-1',
        counterparty_name: '小明',
        due_at: '2026-10-31T00:00:00Z',
        excluded_from_total: false,
        category_id: 'cat-advance'
      }
    })
  })

  it('maps read splits back to form rows, duplicate drops the debt id', () => {
    const read = [
      { category_id: 'cat-a', category_name: '餐饮', amount: 150, note: null, sort_order: 0 },
      {
        category_id: null,
        category_name: null,
        amount: 50,
        note: '代墊',
        sort_order: 1,
        debt_id: 'debt-1',
        debt_counterparty_name: '小明',
        debt_due_at: '2026-10-31T00:00:00+00:00',
        debt_category_id: 'cat-advance',
        debt_category_name: '代付',
        debt_has_repayments: true
      }
    ]
    const rows = txSplitFormItemsFromRead(read)
    expect(rows[1]).toMatchObject({
      category_id: 'cat-advance',
      category_name: '代付',
      debt: { debt_id: 'debt-1', counterparty_name: '小明', due_date: '2026-10-31', has_repayments: true }
    })
    expect(rows[0].debt).toBeUndefined()
    const copied = txSplitFormItemsFromRead(read, { duplicate: true })
    expect(copied[1].debt).toMatchObject({ debt_id: null, has_repayments: false })
  })
})
