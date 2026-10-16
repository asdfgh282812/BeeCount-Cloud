import { describe, expect, it } from 'vitest'
import { effectiveReminders, updateReminders } from './cardReminderSettings'

describe('card reminder contract', () => {
  it('preserves legacy Cloud behavior until settings are explicitly changed', () => {
    const value = effectiveReminders({ cc_reminder_enabled: false })
    expect(value.cc_reminder_enabled).toBe(true)
    expect(value.cc_reminder_days).toBe(7)
    expect(value.cc_settings_version).toBe(0)
  })
  it('keeps overrides stable across global changes and resets inheritance', () => {
    let data = updateReminders({ reminder_hour: 21 }, { ...effectiveReminders({}), cc_reminder_days: 5 }, 'A')
    data = updateReminders(data, { ...effectiveReminders(data), cc_reminder_days: 1 })
    expect(effectiveReminders(data, 'A').cc_reminder_days).toBe(5)
    expect(effectiveReminders(data, 'B').cc_reminder_days).toBe(1)
    data = updateReminders(data, effectiveReminders(data, 'A'), 'A', true)
    expect(effectiveReminders(data, 'A').cc_reminder_days).toBe(1)
    expect(data.reminder_hour).toBe(21)
  })
  it('normalizes invalid ranges and types', () => {
    const value = effectiveReminders({ cc_settings_version: 1, cc_reminder_hour: 99, cc_reminder_minute: -1, cc_reminder_days: true })
    expect(value.cc_reminder_hour).toBe(10)
    expect(value.cc_reminder_minute).toBe(0)
    expect(value.cc_reminder_days).toBe(3)
  })
})
