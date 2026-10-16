import { describe, expect, it } from 'vitest'

import { resolveWeekStartsMonday, shouldShowWelcome } from './lib/welcomeOnboarding'

const loaded = { profileLoaded: true, ledgersLoaded: true }

describe('shouldShowWelcome', () => {
  it('全新帳號(沒帳本、沒做過)才顯示', () => {
    expect(shouldShowWelcome({ ...loaded, ledgerCount: 0, appearance: null })).toBe(true)
    expect(shouldShowWelcome({ ...loaded, ledgerCount: 0, appearance: {} })).toBe(true)
    expect(
      shouldShowWelcome({ ...loaded, ledgerCount: 0, appearance: { onboarding_done: false } }),
    ).toBe(true)
  })

  it('任一端做過(App 或 Web)就不再問', () => {
    expect(
      shouldShowWelcome({ ...loaded, ledgerCount: 0, appearance: { onboarding_done: true } }),
    ).toBe(false)
  })

  it('舊帳號(已有帳本)不問,哪怕沒有旗標', () => {
    expect(shouldShowWelcome({ ...loaded, ledgerCount: 2, appearance: {} })).toBe(false)
  })

  it('資料沒載完不顯示,避免舊帳號閃一下歡迎畫面', () => {
    expect(
      shouldShowWelcome({ profileLoaded: false, ledgersLoaded: true, ledgerCount: 0, appearance: null }),
    ).toBe(false)
    expect(
      shouldShowWelcome({ profileLoaded: true, ledgersLoaded: false, ledgerCount: 0, appearance: null }),
    ).toBe(false)
  })
})

describe('resolveWeekStartsMonday', () => {
  it('appearance 有設定就照它(跟 App 的 week_starts_monday 同一個 key)', () => {
    expect(resolveWeekStartsMonday({ week_starts_monday: false }, 'zh-TW')).toBe(false)
    expect(resolveWeekStartsMonday({ week_starts_monday: true }, 'en-US')).toBe(true)
  })

  it('沒設定沿用語系推斷:中文週一、其餘週日', () => {
    expect(resolveWeekStartsMonday(null, 'zh-TW')).toBe(true)
    expect(resolveWeekStartsMonday({}, 'ZH-cn')).toBe(true)
    expect(resolveWeekStartsMonday(undefined, 'en-US')).toBe(false)
  })
})
