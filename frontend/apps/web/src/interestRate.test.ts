import { describe, expect, it } from 'vitest'

import { interestRateToPercentDisplay, percentDisplayToInterestRate } from '@beecount/web-features'

// 需求 #17(2026-08 Phase 12):分期付款年利率 UI 顯示/送出換算。表單狀態/
// 後端一律存小數分數(0.06 = 6%/年),UI 輸入框改成整數百分比(6)。
describe('interest rate percent display conversion', () => {
  it('小数分数转整数百分比显示', () => {
    expect(interestRateToPercentDisplay('0.06')).toBe('6')
    expect(interestRateToPercentDisplay('0.125')).toBe('12.5')
    expect(interestRateToPercentDisplay('0')).toBe('0')
    expect(interestRateToPercentDisplay('')).toBe('')
  })

  it('整数百分比转回小数分数存储', () => {
    expect(percentDisplayToInterestRate('6')).toBe('0.06')
    expect(percentDisplayToInterestRate('12.5')).toBe('0.125')
    expect(percentDisplayToInterestRate('0')).toBe('0')
    expect(percentDisplayToInterestRate('')).toBe('')
  })

  it('来回转换不产生浮点误差噪音(0.06 * 100 不应该是 6.000000000000001)', () => {
    expect(interestRateToPercentDisplay('0.06')).toBe('6')
    expect(interestRateToPercentDisplay(percentDisplayToInterestRate('6'))).toBe('6')
  })

  it('非法输入回退空字符串', () => {
    expect(interestRateToPercentDisplay('abc')).toBe('')
    expect(percentDisplayToInterestRate('abc')).toBe('')
  })
})
