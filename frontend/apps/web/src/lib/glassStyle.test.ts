import { describe, expect, it } from 'vitest'
import { resolveGlassStyle } from './glassStyle'

describe('glass_style profile contract', () => {
  it('舊端缺鍵與未知值使用清透 A', () => {
    for (const value of [undefined, null, '', 'future-style', 1, {}]) {
      expect(resolveGlassStyle(value)).toBe('clear')
    }
  })
  it('App 的三個 wire 值均保留', () => {
    for (const value of ['clear', 'balanced', 'tinted']) {
      expect(resolveGlassStyle(value)).toBe(value)
    }
  })
})
