import { describe, expect, it } from 'vitest'

import { buildTxSplitsPayload, txDefaults, validateTxSplits, type TxForm } from '@beecount/web-features'

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
