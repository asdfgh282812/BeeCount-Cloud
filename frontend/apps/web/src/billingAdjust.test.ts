import { describe, expect, it } from 'vitest'

import {
  adjustableRange,
  buildAdjustPayload,
  buildResetPayload,
  dateOnly,
  shiftDate
} from '@beecount/web-features'

describe('billingAdjust(彈性結帳日 · 調整本期結帳日)', () => {
  it('shiftDate 跨月、跨年、閏年都用純日期運算', () => {
    expect(shiftDate('2026-10-31', 1)).toBe('2026-11-01')
    expect(shiftDate('2026-01-01', -1)).toBe('2025-12-31')
    expect(shiftDate('2028-02-28', 1)).toBe('2028-02-29')
    expect(dateOnly('2026-11-02T00:00:00Z')).toBe('2026-11-02')
  })

  it('adjustableRange:上一期結帳日之後、下一期結帳日之前', () => {
    expect(
      adjustableRange({
        period_cycle_start: '2026-09-30T00:00:00Z',
        period_next_cycle_end: '2026-11-30T00:00:00Z'
      })
    ).toEqual({ min: '2026-10-01', max: '2026-11-29' })
    expect(
      adjustableRange({ period_cycle_start: '2026-09-30T00:00:00Z', period_next_cycle_end: null })
    ).toBeNull()
  })

  it('僅這一期:寫覆寫(key 是原定結帳日所在年月),保留其它期的覆寫', () => {
    expect(
      buildAdjustPayload({
        scope: 'once',
        picked: '2026-11-03',
        monthKey: '2026-10',
        defaultEnd: '2026-11-02T00:00:00Z',
        overrides: { '2026-08': '2026-08-14' }
      })
    ).toEqual({ billing_cycle_overrides: { '2026-08': '2026-08-14', '2026-10': '2026-11-03' } })
  })

  it('僅這一期:選回預設結帳日 = 移除這一期的覆寫', () => {
    expect(
      buildAdjustPayload({
        scope: 'once',
        picked: '2026-11-02',
        monthKey: '2026-10',
        defaultEnd: '2026-11-02T00:00:00Z',
        overrides: { '2026-10': '2026-11-03' }
      })
    ).toEqual({ billing_cycle_overrides: {} })
  })

  it('每一期都變更:改帳戶結帳日並清空所有覆寫', () => {
    expect(
      buildAdjustPayload({
        scope: 'all',
        picked: '2026-10-18',
        monthKey: '2026-10',
        defaultEnd: '2026-10-15',
        overrides: { '2026-10': '2026-10-17', '2026-11': '2026-11-16' }
      })
    ).toEqual({ billing_day: 18, billing_cycle_overrides: {} })
  })

  it('還原預設:只移除這一期的覆寫', () => {
    expect(
      buildResetPayload({
        monthKey: '2026-10',
        overrides: { '2026-10': '2026-10-17', '2026-11': '2026-11-16' }
      })
    ).toEqual({ billing_cycle_overrides: { '2026-11': '2026-11-16' } })
    expect(buildResetPayload({ monthKey: '2026-10', overrides: null })).toEqual({
      billing_cycle_overrides: {}
    })
  })
})
