import { describe, expect, it } from 'vitest'

import {
  CURRENCY_CODES,
  currencyDisplayName,
  currencySymbol,
  transferConversionDisplay,
} from '@beecount/web-features'

describe('currencies', () => {
  it('CURRENCY_CODES 覆盖全部 + 含 issue#273 请求的 KES/XAF/XOF', () => {
    expect(CURRENCY_CODES.length).toBe(151)
    expect(CURRENCY_CODES).toEqual(
      expect.arrayContaining(['KES', 'XAF', 'XOF', 'CNY', 'USD', 'EUR', 'JPY']),
    )
  })

  it('code 无重复', () => {
    expect(new Set(CURRENCY_CODES).size).toBe(CURRENCY_CODES.length)
  })

  it('currencyDisplayName 用 Intl 本地化,大小写不敏感,未知 code 回退自身', () => {
    expect(currencyDisplayName('USD', 'en')).toBe('US Dollar')
    expect(currencyDisplayName('KES', 'en')).toBe('Kenyan Shilling')
    expect(currencyDisplayName('usd', 'en')).toBe('US Dollar')
    expect(currencyDisplayName('ZZZ', 'en')).toBe('ZZZ')
  })

  it('中文 locale 返回本地化名', () => {
    expect(currencyDisplayName('USD', 'zh-CN')).toBe('美元')
  })

  // 需求 #12(2026-08 Phase 12):CNY/JPY 拿掉 ¥ 符号前缀,只显示数字;
  // 其它已知币别符号维持现况;全站唯一来源(见 lib/currencies.ts)。
  it('currencySymbol: CNY/JPY 不带符号,其它已知币别维持现况', () => {
    expect(currencySymbol('CNY')).toBe('')
    expect(currencySymbol('JPY')).toBe('')
    expect(currencySymbol('cny')).toBe('') // 大小写不敏感
    expect(currencySymbol('USD')).toBe('$')
    expect(currencySymbol('EUR')).toBe('€')
    expect(currencySymbol('HKD')).toBe('HK$')
    expect(currencySymbol('GBP')).toBe('£')
  })

  it('currencySymbol: 未知币别回退空字符串', () => {
    expect(currencySymbol('TWD')).toBe('')
    expect(currencySymbol('ZZZ')).toBe('')
  })

  // 2026-09-18 使用者反馈:转帐「≈折算金额」跟实际转入金额对不上——根因是
  // native_amount 专供信用卡账单计算(折的是转出方金额),显示层改用
  // to_amount(转入帐户自身币别的精确值,不查即时匯率)。后续测试又发现
  // 「本位币转外币」方向完全没有换算提示,改成双向都显示(见函数注释)。
  describe('transferConversionDisplay', () => {
    const accountCurrencyByName = new Map([
      ['台幣帳戶', 'TWD'],
      ['日幣帳戶', 'JPY'],
      ['美金帳戶', 'USD'],
    ])

    it('转入帐户币别 = 本位币(外币转回本位币)→ 用 to_amount,标记为本位币', () => {
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 9016,
            to_amount: 1828,
            from_account_name: '日幣帳戶',
            to_account_name: '台幣帳戶',
          },
          accountCurrencyByName,
          'TWD',
        ),
      ).toEqual({ amount: 1828, currencyCode: 'TWD', isBaseCurrency: true })
    })

    it('转出帐户币别 = 本位币(本位币转外币)→ 用 to_amount,标记为非本位币', () => {
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 600,
            to_amount: 3000,
            from_account_name: '台幣帳戶',
            to_account_name: '日幣帳戶',
          },
          accountCurrencyByName,
          'TWD',
        ),
      ).toEqual({ amount: 3000, currencyCode: 'JPY', isBaseCurrency: false })
    })

    it('两边都不是本位币(三币情境)→ to_amount 仍是精确值,照常显示', () => {
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 100,
            to_amount: 15000,
            from_account_name: '美金帳戶',
            to_account_name: '日幣帳戶',
          },
          accountCurrencyByName,
          'TWD',
        ),
      ).toEqual({ amount: 15000, currencyCode: 'JPY', isBaseCurrency: false })
    })

    it('同币种转帐 → 不显示「≈」', () => {
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 500,
            to_amount: 500,
            from_account_name: '台幣帳戶',
            to_account_name: '台幣帳戶',
          },
          accountCurrencyByName,
          'TWD',
        ),
      ).toBeNull()
    })

    it('非转帐交易 / 缺帐户币别字典 / 帐户查不到币别 → 不显示', () => {
      expect(
        transferConversionDisplay(
          { tx_type: 'expense', amount: 100, to_amount: null, from_account_name: null, to_account_name: null },
          accountCurrencyByName,
          'TWD',
        ),
      ).toBeNull()
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 9016,
            to_amount: 1828,
            from_account_name: '日幣帳戶',
            to_account_name: '台幣帳戶',
          },
          undefined,
          'TWD',
        ),
      ).toBeNull()
      expect(
        transferConversionDisplay(
          {
            tx_type: 'transfer',
            amount: 9016,
            to_amount: 1828,
            from_account_name: '查無此帳戶',
            to_account_name: '台幣帳戶',
          },
          accountCurrencyByName,
          'TWD',
        ),
      ).toBeNull()
    })
  })
})
