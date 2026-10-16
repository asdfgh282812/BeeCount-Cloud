/** Wire keys shared with App CardReminderSettings and Cloud card_reminder_settings.py. */
export type ReminderSettings = {
  cc_settings_version: number
  cc_reminder_hour: number
  cc_reminder_minute: number
  cc_reminder_enabled: boolean
  cc_reminder_days: number
  cc_billing_reminder_enabled: boolean
  cc_due_reminder_enabled: boolean
  cc_due_reminder_maxdays: number
}
export function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
export function reminderOverrides(data: Record<string, unknown>) {
  return isRecord(data.cc_card_overrides) ? data.cc_card_overrides : {}
}
export function effectiveReminders(data: Record<string, unknown>, accountId?: string): ReminderSettings {
  const overrides = reminderOverrides(data)
  const custom = accountId ? overrides[accountId] : undefined
  const source = isRecord(custom) ? custom : data
  const legacy = !isRecord(custom) && source.cc_settings_version !== 1
  const n = (key: string, fallback: number, low: number, high: number) => {
    const value = source[key]
    return typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high ? value : fallback
  }
  return {
    cc_settings_version: legacy ? 0 : 1,
    cc_reminder_hour: n('cc_reminder_hour', 10, 0, 23),
    cc_reminder_minute: n('cc_reminder_minute', 0, 0, 59),
    cc_reminder_enabled: legacy || source.cc_reminder_enabled === true,
    cc_reminder_days: legacy ? 7 : n('cc_reminder_days', 3, 1, 7),
    cc_billing_reminder_enabled: legacy || source.cc_billing_reminder_enabled === true,
    cc_due_reminder_enabled: legacy || source.cc_due_reminder_enabled === true,
    cc_due_reminder_maxdays: legacy ? 2 : n('cc_due_reminder_maxdays', 3, 1, 10),
  }
}
export function updateReminders(data: Record<string, unknown>, value: ReminderSettings, accountId?: string, reset = false): Record<string, unknown> {
  if (!accountId) return { ...data, ...value, cc_settings_version: 1 }
  const overrides = { ...reminderOverrides(data) }
  if (reset) delete overrides[accountId]
  else overrides[accountId] = { ...value, cc_settings_version: 1 }
  return { ...data, cc_card_overrides: overrides }
}
