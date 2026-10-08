/**
 * 「先猜再揭曉」的題目(純函式)。跟 App 一樣:
 * - 錢去哪了:前三大支出分類洗牌;
 * - 每月起伏:支出最高的月份 + 兩個其他有支出的月份。
 * 洗牌用年份當種子,同一年每次打開題目都一樣。資料不夠出題時回 null(直接看答案)。
 */
import type { AnnualReportData } from './types'
import { seededShuffle } from './funFacts'

export type Quiz<T> = { options: T[]; answer: number }

export function categoryQuiz(d: Pick<AnnualReportData, 'year' | 'topExpenseCategories'>): Quiz<string> | null {
  const top = d.topExpenseCategories.slice(0, 3).map((c) => c.name)
  if (top.length < 3) return null
  const options = seededShuffle(top, d.year * 31 + 1)
  return { options, answer: options.indexOf(top[0]) }
}

export function monthQuiz(d: Pick<AnnualReportData, 'year' | 'monthlyData' | 'peakMonth'>): Quiz<number> | null {
  const peak = d.peakMonth
  const others = d.monthlyData.filter((m) => m.expense > 0 && m.month !== peak).map((m) => m.month)
  if (others.length < 2 || !d.monthlyData.some((m) => m.month === peak && m.expense > 0)) return null
  const picked = seededShuffle(others, d.year * 31 + 2).slice(0, 2)
  const options = seededShuffle([peak, ...picked], d.year * 31 + 3)
  return { options, answer: options.indexOf(peak) }
}
