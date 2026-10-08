/**
 * 年度稱號:依實際記帳 / 股票內容挑一個最貼切的稱號(規則有優先序,取第一個
 * 符合者),並附最多 3 條「為什麼是你」的理由。純規則、不調 LLM。
 *
 * 傳說級稱號(全勤記帳王 / 鋼鐵意志 / 超級存錢筒)優先序最高,跟 App
 * `annual_persona.dart` 一致;每個稱號有稀有度,揭曉頁稀有以上灑彩帶。
 */
import type { AnnualReportData, Persona, PersonaId, PersonaRarity, PersonaReason } from './types'
import { TKEY } from '../i18n'

type PersonaInput = Omit<AnnualReportData, 'achievements' | 'persona'>

export function computePersona(d: PersonaInput): Persona {
  const stock = d.stock?.currencies[0] ?? null
  const stockTrades = stock ? stock.buyCount + stock.sellCount : 0
  const expenseCount = d.hourBuckets.reduce((s, b) => s + b.count, 0)
  const nightShare = expenseCount > 0 ? (d.hourBuckets[0].count / expenseCount) * 100 : 0
  const topCat = d.topExpenseCategories[0]
  const hasPrev = d.prevYear.totalExpense > 0

  const hasIncome = d.totalIncome > 0
  const id: PersonaId =
    d.elapsedDays >= 60 && d.recordingDays >= d.elapsedDays
      ? 'perfectAttendance'
      : d.maxConsecutiveDays >= 100
        ? 'streakKing'
        : hasIncome && d.savingsRate >= 50
          ? 'megaSaver'
          : stock && stockTrades + stock.dividendCount >= 8
            ? 'investor'
            : hasIncome && d.savingsRate >= 40
              ? 'saver'
              : nightShare >= 30
                ? 'nightOwl'
                : d.weekendBoost >= 1.5
                  ? 'weekendSpender'
                  : topCat && topCat.percent >= 40
                    ? 'focused'
                    : hasPrev && d.yoyExpenseChange <= -10
                      ? 'frugal'
                      : 'steady'

  const reasons: PersonaReason[] = []
  const push = (cond: boolean, textKey: string, args?: PersonaReason['args']) => {
    if (cond && reasons.length < 3) reasons.push({ textKey, args })
  }
  // 理由依「最能代表這個稱號」的順序排,稱號本身的主因永遠排前面。
  const byId: Record<PersonaId, () => void> = {
    perfectAttendance: () => {
      push(true, TKEY.personaReason.records, { records: d.totalRecords, days: d.recordingDays })
      push(true, TKEY.personaReason.streak, { days: d.maxConsecutiveDays })
    },
    megaSaver: () => push(true, TKEY.personaReason.savings, { pct: d.savingsRate.toFixed(0) }),
    streakKing: () => push(true, TKEY.personaReason.streak, { days: d.maxConsecutiveDays }),
    investor: () => {
      if (stock) {
        push(true, TKEY.personaReason.stockTrades, { count: stockTrades })
        push(stock.dividends > 0, TKEY.personaReason.stockDividends, { count: stock.dividendCount })
      }
    },
    saver: () => push(true, TKEY.personaReason.savings, { pct: d.savingsRate.toFixed(0) }),
    nightOwl: () => push(true, TKEY.personaReason.night, { pct: nightShare.toFixed(0) }),
    weekendSpender: () => push(true, TKEY.personaReason.weekend, { times: d.weekendBoost.toFixed(1) }),
    focused: () =>
      push(true, TKEY.personaReason.category, { name: topCat?.name ?? '', pct: (topCat?.percent ?? 0).toFixed(0) }),
    frugal: () => push(true, TKEY.personaReason.frugal, { pct: Math.abs(d.yoyExpenseChange).toFixed(0) }),
    steady: () => {},
  }
  byId[id]()
  push(id !== 'streakKing' && id !== 'perfectAttendance' && d.maxConsecutiveDays >= 30, TKEY.personaReason.streak, { days: d.maxConsecutiveDays })
  push(id !== 'saver' && id !== 'megaSaver' && d.savingsRate >= 20, TKEY.personaReason.savings, { pct: d.savingsRate.toFixed(0) })
  push(
    id !== 'investor' && !!stock && stock.realizedPnl > 0,
    TKEY.personaReason.stockProfit,
    { amount: Math.round(stock?.realizedPnl ?? 0).toLocaleString(), currency: stock?.currency ?? '' },
  )
  push(id !== 'focused' && !!topCat, TKEY.personaReason.category, {
    name: topCat?.name ?? '',
    pct: (topCat?.percent ?? 0).toFixed(0),
  })
  push(id !== 'perfectAttendance', TKEY.personaReason.records, { records: d.totalRecords, days: d.recordingDays })

  return { id, rarity: PERSONA_RARITY[id], reasons }
}

export const PERSONA_RARITY: Record<PersonaId, PersonaRarity> = {
  perfectAttendance: 'legendary',
  streakKing: 'legendary',
  megaSaver: 'legendary',
  investor: 'rare',
  saver: 'rare',
  nightOwl: 'common',
  weekendSpender: 'common',
  focused: 'common',
  frugal: 'common',
  steady: 'common',
}
