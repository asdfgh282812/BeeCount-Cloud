import { describe, expect, it } from 'vitest'

import { installmentPlanDefaults, recurringRuleDefaults } from '@beecount/web-features'

// MOZE_FEATURE_GAP_SD.md §2.2 / §2.3 web UI — 表单默认值的形状回归测试。
// 这两个 defaults() 工厂没有跟 server 交互，纯数据，适合直接单测。
describe('recurringRuleDefaults', () => {
  it('starts blank/unset with sensible fallbacks', () => {
    const form = recurringRuleDefaults()
    expect(form.editingId).toBeNull()
    expect(form.tx_type).toBe('expense')
    expect(form.amount).toBe('')
    expect(form.frequency).toBe('monthly')
    expect(form.interval).toBe('1')
    expect(form.enabled).toBe(true)
    expect(form.end_at).toBe('')
  })

  it('defaults next_run_at to a datetime-local string in the future', () => {
    const form = recurringRuleDefaults()
    // datetime-local input format: YYYY-MM-DDTHH:mm
    expect(form.next_run_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    const parsed = new Date(form.next_run_at)
    expect(parsed.getTime()).toBeGreaterThan(Date.now())
  })
})

describe('installmentPlanDefaults', () => {
  it('starts blank/unset with sensible fallbacks', () => {
    const form = installmentPlanDefaults()
    expect(form.editingId).toBeNull()
    expect(form.total_amount).toBe('')
    expect(form.periods).toBe('')
    expect(form.status).toBe('active')
    expect(form.account_id).toBe('')
    expect(form.category_id).toBe('')
  })

  it('defaults first_period_at to a valid datetime-local string', () => {
    const form = installmentPlanDefaults()
    expect(form.first_period_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/)
    expect(Number.isNaN(new Date(form.first_period_at).getTime())).toBe(false)
  })
})
