// 年度报告数据层 — 公共导出。UI 层只 import 这个 barrel。

export * from './types'
export { aggregate, MIN_RECORDS_FOR_REPORT } from './aggregate'
export { fetchAnnualReportData } from './fetch'
export { yearTheme, ZODIACS, type YearTheme, type Zodiac } from './yearTheme'
export {
  BUBBLE_TEA_PRICE,
  collectFunFacts,
  hashSalt,
  pickFunFacts,
  seededShuffle,
  type FunFact,
  type FunFactKind,
} from './funFacts'
export { categoryQuiz, monthQuiz, type Quiz } from './quiz'
export { computePersona, PERSONA_RARITY } from './persona'
export { computeAchievements } from './achievements'
export {
  HOLIDAY_FLAGS,
  holidayLabel,
  holidayName,
  holidayOfDay,
  holidaySettingsFromAppearance,
  localDayKey,
  resolveHolidays,
  topHolidaySpend,
  toHolidayLite,
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
