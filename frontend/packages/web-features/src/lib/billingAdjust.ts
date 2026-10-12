/**
 * 彈性結帳日(2026-10-12,MOZE 對標,`doc.moze.app/prepare/account/settings#flexible-closing-date`)
 * 「調整本期結帳日」的純函式——對應 App `billing_cycle_adjust_sheet.dart` 與 server
 * `services/billing_schedule.py`:
 *
 * - 單期覆寫存在帳戶的 `billing_cycle_overrides`(`{yyyy-MM: yyyy-MM-dd}`),key 是「該期原定
 *   結帳日所在年月」(`summary.period_cycle_month_key`),不是調整後的日期;
 * - 「僅這一期」→ 寫覆寫(選到的日子跟預設結帳日一樣就是移除覆寫);
 * - 「每一期都變更」→ 直接改帳戶 `billing_day` 並清空所有覆寫;
 * - 還原預設 → 移除這一期的覆寫。
 */
import type { AccountBillingSummary, AccountPayload } from '@beecount/api-client'

/** 跟 server `HOLIDAY_REGIONS`、App `kHolidayCountries` 一致。 */
export const BILLING_HOLIDAY_REGIONS = ['TW', 'CN', 'HK', 'JP', 'KR', 'US'] as const

export type BillingAdjustScope = 'once' | 'all'

/** `yyyy-MM-dd`(或 ISO 時間字串)→ 純日期字串。 */
export function dateOnly(iso: string): string {
  return iso.slice(0, 10)
}

/** 往前/往後平移 `days` 天(純日期,UTC 運算,不受裝置時區影響)。 */
export function shiftDate(date: string, days: number): string {
  const [y, m, d] = date.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return dt.toISOString().slice(0, 10)
}

/**
 * 新結帳日可選的範圍(含):上一期結帳日之後、下一期結帳日之前,期間順序才不會亂掉。
 * `period_cycle_start` 是上一期結帳日(exclusive 起點),`period_next_cycle_end` 是下一期實際結帳日。
 */
export function adjustableRange(
  summary: Pick<AccountBillingSummary, 'period_cycle_start' | 'period_next_cycle_end'>,
): { min: string; max: string } | null {
  if (!summary.period_next_cycle_end) return null
  return {
    min: shiftDate(dateOnly(summary.period_cycle_start), 1),
    max: shiftDate(dateOnly(summary.period_next_cycle_end), -1),
  }
}

/** 組出送給 `updateAccount` 的 payload。`picked` 是 `yyyy-MM-dd`。 */
export function buildAdjustPayload(args: {
  scope: BillingAdjustScope
  picked: string
  monthKey: string
  defaultEnd: string
  overrides: Record<string, string> | null | undefined
}): Partial<AccountPayload> {
  const { scope, picked, monthKey, defaultEnd } = args
  if (scope === 'all') {
    // 每一期都變更 = 改帳戶結帳日;舊的單期覆寫在新結帳日下沒有意義,一併清空。
    return { billing_day: Number(picked.slice(8, 10)), billing_cycle_overrides: {} }
  }
  const next: Record<string, string> = { ...(args.overrides ?? {}) }
  if (picked === dateOnly(defaultEnd)) {
    delete next[monthKey]
  } else {
    next[monthKey] = picked
  }
  return { billing_cycle_overrides: next }
}

/** 還原預設結帳日:移除這一期的覆寫。 */
export function buildResetPayload(args: {
  monthKey: string
  overrides: Record<string, string> | null | undefined
}): Partial<AccountPayload> {
  const next: Record<string, string> = { ...(args.overrides ?? {}) }
  delete next[args.monthKey]
  return { billing_cycle_overrides: next }
}
