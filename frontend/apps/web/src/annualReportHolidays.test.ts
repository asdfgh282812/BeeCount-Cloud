import { describe, expect, it } from 'vitest'

import {
  aggregate,
  holidayLabel,
  holidaySettingsFromAppearance,
  resolveHolidays,
  topHolidaySpend,
  type HolidayLite,
  type TransactionLite,
} from '@beecount/web-features'

const h = (date: string, country: string, key: string, kind = 'public', priority = 10): HolidayLite => ({
  date,
  country,
  key,
  kind,
  nameZhTw: key,
  nameEn: key,
  nameLocal: key,
  emoji: '🎌',
  color: '#FFB300',
  priority,
})

const tx = (happenedAt: string, amount: number, txType: TransactionLite['txType'] = 'expense'): TransactionLite => ({
  id: happenedAt + amount,
  txType,
  amount,
  happenedAt,
  note: null,
  categoryName: null,
  categoryKind: null,
  accountName: null,
  tagsList: [],
})

// 跟 App test/services/holidays/holiday_resolver_test.dart 同規則
describe('resolveHolidays', () => {
  it('primary country wins on a shared day', () => {
    const out = resolveHolidays(
      [h('2026-10-10', 'TW', 'tw_national_day'), h('2026-10-10', 'CN', 'cn_golden_week')],
      ['TW', 'CN'],
      'TW',
    )
    expect(out['2026-10-10'].map((e) => e.key)).toEqual(['tw_national_day'])
  })

  it('non-primary festivals dedupe by key and drop their day-offs', () => {
    const out = resolveHolidays(
      [
        h('2026-09-25', 'CN', 'mid_autumn'),
        h('2026-09-25', 'HK', 'mid_autumn'),
        h('2026-10-08', 'CN', 'day_off', 'day_off'),
      ],
      ['TW', 'CN', 'HK'],
      'TW',
    )
    expect(out['2026-09-25'].map((e) => `${e.country}:${e.key}`)).toEqual(['CN:mid_autumn'])
    expect(out['2026-10-08']).toBeUndefined()
  })

  it('primary day-off is kept and days sort by priority', () => {
    const out = resolveHolidays(
      [h('2026-10-09', 'TW', 'day_off', 'day_off', 90), h('2026-10-09', 'TW', 'x', 'observance', 5)],
      ['TW'],
      'TW',
    )
    expect(out['2026-10-09'].map((e) => e.key)).toEqual(['x', 'day_off'])
  })

  it('unselected countries are ignored', () => {
    expect(resolveHolidays([h('2026-04-29', 'JP', 'jp_showa_day')], ['TW'], 'TW')).toEqual({})
  })
})

describe('holidaySettingsFromAppearance', () => {
  it('reads App-synced fields and always includes primary', () => {
    expect(
      holidaySettingsFromAppearance({ holiday_primary: 'JP', holiday_regions: ['TW', 'XX'] }, 'zh-TW'),
    ).toEqual({ enabled: true, primary: 'JP', regions: ['TW', 'JP'] })
  })

  it('falls back to the locale default', () => {
    expect(holidaySettingsFromAppearance(null, 'zh-CN').primary).toBe('CN')
    expect(holidaySettingsFromAppearance(undefined, 'en').primary).toBe('US')
    expect(holidaySettingsFromAppearance({ holiday_enabled: false }, 'zh-TW').enabled).toBe(false)
  })
})

describe('topHolidaySpend', () => {
  const holidays = {
    '2026-09-25': [h('2026-09-25', 'TW', 'mid_autumn')],
    '2026-12-25': [h('2026-12-25', 'TW', 'christmas')],
  }

  it('sums expenses per holiday day using the local date', () => {
    const top = topHolidaySpend(
      [
        tx(new Date(2026, 8, 25, 12).toISOString(), 300),
        tx(new Date(2026, 8, 25, 23, 30).toISOString(), 900),
        tx(new Date(2026, 11, 25, 20).toISOString(), 1000),
        tx(new Date(2026, 8, 25, 13).toISOString(), 5000, 'income'),
        tx(new Date(2026, 8, 26, 1).toISOString(), 99999),
      ],
      holidays,
    )!
    expect(top.date).toBe('2026-09-25')
    expect(top.holiday.key).toBe('mid_autumn')
    expect(top.total).toBe(1200)
    expect(top.holidayDays).toBe(2)
  })

  it('returns null without holiday spending', () => {
    expect(topHolidaySpend([tx(new Date(2026, 8, 26).toISOString(), 10)], holidays)).toBeNull()
  })

  it('aggregate keeps only the report year and exposes holidaySpend', () => {
    const d = aggregate({
      thisYearTxs: [tx(new Date(2026, 8, 25, 12).toISOString(), 300)],
      prevYearTxs: [],
      year: 2026,
      ledger: { id: 'l', name: 'L', currency: 'TWD' },
      holidays: { ...holidays, '2027-01-01': [h('2027-01-01', 'TW', 'new_year')] },
    })
    expect(Object.keys(d.holidays).sort()).toEqual(['2026-09-25', '2026-12-25'])
    expect(d.holidaySpend?.total).toBe(300)
  })
})

describe('holidayLabel', () => {
  it('adds the flag only for non-primary holidays', () => {
    expect(holidayLabel(h('2026-07-07', 'JP', 'tanabata'), 'zh-TW', 'TW')).toBe('🎌 tanabata 🇯🇵')
    expect(holidayLabel(h('2026-08-19', 'TW', 'qixi'), 'zh-TW', 'TW')).toBe('🎌 qixi')
    expect(holidayLabel(h('2026-08-19', 'TW', 'qixi'), 'en', null)).toBe('🎌 qixi')
  })
})
