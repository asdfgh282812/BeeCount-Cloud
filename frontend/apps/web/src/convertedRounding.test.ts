import type { InvestmentSettings } from '@beecount/api-client'
import {
  applyConvertedRounding,
  defaultConvertedRounding,
  isStockAccount,
  mergeGroupsToBase,
  reinvestFor,
  reinvestKey,
  resolveConvertedRounding,
  type AssetGroup,
} from '@beecount/web-features'
import { describe, expect, it } from 'vitest'

/** 同 App test/services/currency/converted_rounding_test.dart 與 investment_settings_test.dart 的契約。 */
describe('applyConvertedRounding', () => {
  it('floor 絕對值往 0 捨去,round 四捨五入,none 原樣', () => {
    expect(applyConvertedRounding(1234.99, 'floor')).toBe(1234)
    expect(applyConvertedRounding(-1234.99, 'floor')).toBe(-1234)
    expect(applyConvertedRounding(1234.5, 'round')).toBe(1235)
    expect(applyConvertedRounding(1234.567, 'none')).toBe(1234.567)
  })
  it('先清浮點殘渣', () => {
    expect(applyConvertedRounding(30.9999999999999, 'floor')).toBe(31)
  })
})

describe('resolveConvertedRounding', () => {
  it('預設:TWD/JPY/KRW 捨去,其它不取整', () => {
    expect(defaultConvertedRounding('TWD')).toBe('floor')
    expect(defaultConvertedRounding('usd')).toBe('none')
  })
  it('使用者設定優先,不認得的值退回預設', () => {
    expect(resolveConvertedRounding({ L1: 'round' }, 'L1', 'TWD')).toBe('round')
    expect(resolveConvertedRounding({ L1: 'bogus' }, 'L1', 'TWD')).toBe('floor')
    expect(resolveConvertedRounding(undefined, 'L1', 'USD')).toBe('none')
  })
})

describe('mergeGroupsToBase rounding', () => {
  const groups: AssetGroup[] = [
    {
      type: 'cash',
      label: 'cash',
      color: '#000',
      isLiability: false,
      rows: [],
      subtotals: [{ currency: 'USD', value: 3767.66 }],
    },
  ]
  const auto = { base: 'TWD', rate_date: '2026-10-09', rates: { USD: 1 / 31.5 } } as never
  it('預設不取整(舊行為),floor 時每個分組各自捨去', () => {
    const raw = mergeGroupsToBase([{ currency: 'USD', groups }], 'TWD', auto, [])
    expect(raw[0].subtotals[0].value).toBeCloseTo(118681.29, 2)
    const floored = mergeGroupsToBase([{ currency: 'USD', groups }], 'TWD', auto, [], 'floor')
    expect(floored[0].subtotals[0].value).toBe(118681)
  })
})

describe('持股開關與各檔再投入', () => {
  it('stockEnabled 缺 = 啟用,只有明確 false 才是原始投資理財帳戶', () => {
    expect(isStockAccount({ account_type: 'investment' })).toBe(true)
    expect(isStockAccount({ account_type: 'investment', investment_settings: { stockEnabled: false } })).toBe(false)
    expect(isStockAccount({ account_type: 'cash' })).toBe(false)
  })
  it('各檔設定優先,沒設才看舊的帳戶層級值', () => {
    const s: InvestmentSettings = {
      reinvestDividends: true,
      reinvestBySymbol: { 'TW:0050': true, 'TW:2330': false },
    }
    expect(reinvestFor(s, 'tw', '0050')).toBe(true)
    expect(reinvestFor(s, 'TW', '2330')).toBe(false)
    expect(reinvestFor(s, 'US', 'AAPL')).toBe(true)
    expect(reinvestFor(undefined, 'US', 'AAPL')).toBe(false)
    expect(reinvestKey('tw', '0050b')).toBe('TW:0050B')
  })
})
