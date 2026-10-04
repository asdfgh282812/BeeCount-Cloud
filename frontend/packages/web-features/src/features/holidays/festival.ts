/**
 * 節日主題(節日 P3,docs/HOLIDAYS_SD.md「節日主題」):主要國家的節日當天,
 * Web 暫時把主題色換成節日色,頁首顯示節日徽章。只改 CSS 變數,不寫
 * localStorage、不回推 server(見 @beecount/ui `PrimaryColorProvider`
 * 的 festivalColor)。
 *
 * 白名單跟 App `lib/services/holidays/holiday_theme.dart::kFestivalThemeSkins`
 * 的 key 一致,改這裡要一起改。Web 沒有頁首皮膚,只用到「哪些節日換主題」。
 */
import type { HolidayLite } from '../annual-report/data/types'

/**
 * 會換主題的節慶 key。莊重的日子(和平紀念日、清明、中元…)、一般國定假日、
 * 購物節、補假與 Cloud 自動產生的 `<country>_<slug>` key 都不在裡面。
 */
export const FESTIVAL_THEME_KEYS: ReadonlySet<string> = new Set([
  'lunar_new_year_eve',
  'lunar_new_year',
  'lantern',
  'mid_autumn',
  'chuseok_holiday',
  'christmas_eve',
  'christmas',
  'halloween',
  'new_year',
  'new_years_eve',
  'valentines',
  'white_day',
  'qixi',
  'tanabata',
  'mothers_day',
  'fathers_day',
  'kr_parents_day',
  'childrens_day',
  'dragon_boat',
  'thanksgiving',
  'tw_national_day',
  'cn_national_day',
  'us_independence_day',
])

/**
 * 今天要套的節日:只看主要國家、非補假、在白名單裡的,依 priority 取第一個。
 * [entries] 不必先去重(主題只看主要國家,主要國家當天的條目一定會顯示)。
 */
export function festivalThemeFor(
  entries: HolidayLite[],
  primary: string,
  dayKey: string,
): HolidayLite | null {
  const candidates = entries
    .filter(
      (e) =>
        e.date === dayKey &&
        e.country === primary &&
        e.kind !== 'day_off' &&
        FESTIVAL_THEME_KEYS.has(e.key),
    )
    .sort((a, b) => a.priority - b.priority || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
  return candidates[0] ?? null
}

/** 距離下一個本地午夜還有幾毫秒(跨日重新判斷用)。 */
export function msUntilNextLocalMidnight(now: Date = new Date()): number {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
  return next.getTime() - now.getTime()
}
