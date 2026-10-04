// 年度报告数据层 — 公共导出。UI 层只 import 这个 barrel。

export * from './types'
export { aggregate, MIN_RECORDS_FOR_REPORT } from './aggregate'
export { fetchAnnualReportData } from './fetch'
export {
  HOLIDAY_FLAGS,
  holidayLabel,
  holidayName,
  holidayOfDay,
  holidaySettingsFromAppearance,
  localDayKey,
  resolveHolidays,
  topHolidaySpend,
} from './holidays'
export {
  overviewInsight,
  yoyInsight,
  monthlyInsight,
  categoryInsight,
  hoursInsight,
  weekdayInsight,
  extremesInsight,
  habitsInsight,
  tagsInsight,
  achievementsInsight,
  outroInsight,
  accountsInsight,
  stockOverviewInsight,
  type Insight,
} from './insights'
