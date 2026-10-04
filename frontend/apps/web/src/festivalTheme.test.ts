import { describe, expect, it } from 'vitest'

import {
  FESTIVAL_THEME_KEYS,
  festivalThemeFor,
  msUntilNextLocalMidnight,
  type HolidayLite,
} from '@beecount/web-features'

const h = (date: string, country: string, key: string, kind = 'public', priority = 10): HolidayLite => ({
  date,
  country,
  key,
  kind,
  nameZhTw: key,
  nameEn: key,
  nameLocal: key,
  emoji: '🎃',
  color: '#FF6F00',
  priority,
})

describe('festivalThemeFor', () => {
  it('picks the primary country festival on that day', () => {
    const entries = [
      h('2026-10-31', 'US', 'us_some_day', 'public', 1),
      h('2026-10-31', 'US', 'halloween', 'observance', 2),
      h('2026-11-01', 'US', 'christmas'),
    ]
    expect(festivalThemeFor(entries, 'US', '2026-10-31')?.key).toBe('halloween')
    expect(festivalThemeFor(entries, 'US', '2026-10-30')).toBeNull()
  })

  it('skips other countries, day_off and solemn days', () => {
    expect(festivalThemeFor([h('2026-10-31', 'US', 'halloween')], 'TW', '2026-10-31')).toBeNull()
    expect(festivalThemeFor([h('2026-10-09', 'TW', 'day_off', 'day_off')], 'TW', '2026-10-09')).toBeNull()
    expect(festivalThemeFor([h('2026-02-28', 'TW', 'tw_peace_memorial')], 'TW', '2026-02-28')).toBeNull()
    expect(FESTIVAL_THEME_KEYS.has('tomb_sweeping')).toBe(false)
    expect(FESTIVAL_THEME_KEYS.has('mid_autumn')).toBe(true)
  })

  it('counts down to the next local midnight', () => {
    expect(msUntilNextLocalMidnight(new Date(2026, 9, 31, 23, 59, 0))).toBe(60_000)
  })
})
