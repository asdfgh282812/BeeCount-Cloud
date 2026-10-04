/**
 * 年度報告的節日:去重 + 節日當天支出統計。
 *
 * 規則跟 App 一致,改這裡要一起改:
 * - 去重:BeeCount-main `lib/services/holidays/holiday_resolver.dart`
 * - 節日支出:BeeCount-main `lib/services/holidays/holiday_report.dart`
 *
 * 1. 某天主要國家有節日(含補假)→ 只顯示主要國家的。
 * 2. 主要國家那天沒有 → 顯示其他已勾選國家的節日,依 key 去重;其他國家的
 *    補假(day_off)不顯示。
 * 每天依 priority 排序(小的在前),第一個是當天的代表節日。
 */
import type { HolidayEntryWire, ProfileAppearance } from '@beecount/api-client'

import type { HolidayLite, HolidaySpend, TransactionLite } from './types'

export const HOLIDAY_COUNTRIES = ['TW', 'CN', 'HK', 'JP', 'KR', 'US'] as const

export const HOLIDAY_FLAGS: Record<string, string> = {
  TW: '🇹🇼',
  CN: '🇨🇳',
  HK: '🇭🇰',
  JP: '🇯🇵',
  KR: '🇰🇷',
  US: '🇺🇸',
}

export type HolidaySettings = {
  enabled: boolean
  regions: string[]
  primary: string
}

/** 沒設定過(App 沒推過 holiday_*)時依 Web 語系推主要國家,跟 App 的預設一致。 */
export function defaultHolidayCountry(locale: string): string {
  if (locale === 'zh-CN') return 'CN'
  if (locale.startsWith('zh')) return 'TW'
  return 'US'
}

export function holidaySettingsFromAppearance(
  appearance: ProfileAppearance | null | undefined,
  locale: string,
): HolidaySettings {
  const known = (c: unknown): c is string =>
    typeof c === 'string' && (HOLIDAY_COUNTRIES as readonly string[]).includes(c)
  const primary = known(appearance?.holiday_primary)
    ? appearance!.holiday_primary!
    : defaultHolidayCountry(locale)
  const set = new Set<string>([...(appearance?.holiday_regions ?? []).filter(known), primary])
  return {
    enabled: appearance?.holiday_enabled ?? true,
    primary,
    regions: HOLIDAY_COUNTRIES.filter((c) => set.has(c)),
  }
}

export function toHolidayLite(e: HolidayEntryWire): HolidayLite {
  return {
    date: e.date,
    country: e.country,
    key: e.key,
    kind: e.kind,
    nameZhTw: e.name_zh_tw,
    nameEn: e.name_en,
    nameLocal: e.name_local,
    emoji: e.emoji,
    color: e.color,
    priority: e.priority,
  }
}

const isDayOff = (e: HolidayLite) => e.kind === 'day_off'

export function resolveHolidays(
  entries: HolidayLite[],
  regions: string[],
  primary: string,
): Record<string, HolidayLite[]> {
  const effective = new Set([...regions, primary])
  const primaryByDay = new Map<string, HolidayLite[]>()
  const othersByDay = new Map<string, HolidayLite[]>()
  for (const e of entries) {
    if (!effective.has(e.country)) continue
    if (e.country === primary) {
      primaryByDay.set(e.date, [...(primaryByDay.get(e.date) ?? []), e])
    } else if (!isDayOff(e)) {
      othersByDay.set(e.date, [...(othersByDay.get(e.date) ?? []), e])
    }
  }
  const byPriority = (a: HolidayLite, b: HolidayLite) =>
    a.priority - b.priority || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)

  const out: Record<string, HolidayLite[]> = {}
  for (const [day, list] of primaryByDay) out[day] = [...list].sort(byPriority)
  for (const [day, list] of othersByDay) {
    if (day in out) continue
    const seen = new Set<string>()
    out[day] = [...list].sort(byPriority).filter((e) => {
      if (seen.has(e.key)) return false
      seen.add(e.key)
      return true
    })
  }
  return out
}

/** 交易發生在使用者本地時區的哪一天(節日是當地日期,不能用 UTC 的 slice)。 */
export function localDayKey(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 當天的代表節日:優先非補假的。 */
export function holidayOfDay(
  holidays: Record<string, HolidayLite[]>,
  dayKey: string,
): HolidayLite | null {
  const list = holidays[dayKey]
  if (!list || list.length === 0) return null
  return list.find((e) => !isDayOff(e)) ?? list[0]
}

/** 依日期加總節日當天的支出,回傳花最多的那天;同額取較早的。 */
export function topHolidaySpend(
  txs: TransactionLite[],
  holidays: Record<string, HolidayLite[]>,
): HolidaySpend | null {
  const totals = new Map<string, number>()
  for (const t of txs) {
    if (t.txType !== 'expense') continue
    const day = localDayKey(t.happenedAt)
    if (!holidays[day]) continue
    totals.set(day, (totals.get(day) ?? 0) + Math.abs(t.amount))
  }
  let best: string | null = null
  for (const day of [...totals.keys()].sort()) {
    const v = totals.get(day)!
    if (v <= 0) continue
    if (best === null || v > totals.get(best)!) best = day
  }
  if (best === null) return null
  return {
    date: best,
    holiday: holidayOfDay(holidays, best)!,
    total: totals.get(best)!,
    holidayDays: [...totals.values()].filter((v) => v > 0).length,
  }
}

/** 「🥮 中秋節」;非主要國家的節日後面加國旗(跟 App 日曆 / 年度報告一致)。 */
export function holidayLabel(e: HolidayLite, locale: string, primary: string | null): string {
  const flag = primary && e.country !== primary ? ` ${HOLIDAY_FLAGS[e.country] ?? e.country}` : ''
  return `${e.emoji} ${holidayName(e, locale)}${flag}`
}

/** 節日名稱:中文介面用繁中名(簡中介面遇到中國節日用在地簡中名),其他用英文。 */
export function holidayName(e: HolidayLite, locale: string): string {
  if (locale === 'zh-CN' && e.country === 'CN' && e.nameLocal) return e.nameLocal
  if (locale.startsWith('zh')) return e.nameZhTw
  return e.nameEn
}
