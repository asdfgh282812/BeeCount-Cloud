import { authedGet } from './http'

/**
 * 節日資料(非 sync entity 的唯讀 API,見 docs/HOLIDAYS_SD.md)。
 * 欄位跟 server `HolidayEntryOut` 一致(snake_case)。
 */
export type HolidayEntryWire = {
  date: string // YYYY-MM-DD
  country: string // TW / CN / HK / JP / KR / US
  key: string // 跨國共用的節慶 key,例如 mid_autumn
  kind: 'public' | 'observance' | 'day_off' | string
  name_zh_tw: string
  name_en: string
  name_local: string
  emoji: string
  color: string // #RRGGBB
  priority: number
}

export type HolidaysResponse = {
  version: number
  updated_at: string
  /** 帶 knownVersion 且版本相同時為 true,entries 為空 */
  unchanged: boolean
  years: number[]
  countries: string[]
  entries: HolidayEntryWire[]
}

export async function fetchHolidays(
  token: string,
  opts: { countries?: string[]; years?: number[]; knownVersion?: number } = {},
): Promise<HolidaysResponse> {
  const params = new URLSearchParams()
  if (opts.countries?.length) params.set('countries', opts.countries.join(','))
  if (opts.years?.length) params.set('years', opts.years.join(','))
  if (opts.knownVersion !== undefined) params.set('known_version', String(opts.knownVersion))
  const qs = params.toString()
  return authedGet<HolidaysResponse>(`/read/holidays${qs ? `?${qs}` : ''}`, token)
}
