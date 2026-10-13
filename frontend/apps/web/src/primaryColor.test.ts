import { describe, expect, it } from 'vitest'

import {
  DEFAULT_PRIMARY_COLOR,
  DEFAULT_PRIMARY_COLOR_DARK,
  PRIMARY_COLOR_PRESETS,
  PRIMARY_COLOR_STORAGE_KEY,
  onColorHex,
  primaryForMode
} from '@beecount/ui'

// 4.0 帳好嗎主題色:對齊 App BeeTheme / BeeTokens(docs/changes/2026-10-13-v4-web-rebrand-tokens.md)
describe('primary color (4.0 sprout green)', () => {
  it('defaults to sprout green and keeps the legacy storage key', () => {
    expect(DEFAULT_PRIMARY_COLOR).toBe('#2E9E66')
    expect(PRIMARY_COLOR_STORAGE_KEY).toBe('beecount.primary-color')
    expect(PRIMARY_COLOR_PRESETS[0]).toBe('#2E9E66')
    // 老使用者要選得回來的兩個蜂蜜色
    expect(PRIMARY_COLOR_PRESETS).toContain('#F8C91C')
    expect(PRIMARY_COLOR_PRESETS).toContain('#F59E0B')
  })

  it('only swaps the default green in dark mode (App BeeTheme.primaryFor)', () => {
    expect(primaryForMode('#2E9E66', 'dark')).toBe(DEFAULT_PRIMARY_COLOR_DARK)
    expect(primaryForMode('#2e9e66', 'dark')).toBe('#4CC788')
    expect(primaryForMode('#2E9E66', 'light')).toBe('#2E9E66')
    expect(primaryForMode('#F59E0B', 'dark')).toBe('#F59E0B')
  })

  it('picks white or ink foreground by luminance (App BeeTokens.onColor)', () => {
    expect(onColorHex('#2E9E66')).toBe('#FFFFFF')
    expect(onColorHex('#4CC788')).toBe('#1F2A24')
    expect(onColorHex('#F8C91C')).toBe('#1F2A24')
    expect(onColorHex('#1565C0')).toBe('#FFFFFF')
    expect(onColorHex('#FFCC80')).toBe('#1F2A24')
  })
})
