/**
 * 年度回顧「冷知識」(純函式),跟 App `lib/services/report/annual_fun_facts.dart`
 * 同一套規則與門檻。
 *
 * `collectFunFacts` 從一年的交易算出**所有成立的**候選;`pickFunFacts` 再用
 * 年份(+帳本)當種子洗牌取幾則——同一年每次打開都一樣,換一年(或換一份
 * 資料)抽到的組合就不同。門檻刻意設高一點,寧可少一則也不要硬湊。
 */
import type { TransactionLite } from './types'
import { localDayKey } from './holidays'

export type FunFactKind =
  | 'lateNight'
  | 'busiestDay'
  | 'topMerchant'
  | 'repeatAmount'
  | 'smallest'
  | 'favoriteWeekday'
  | 'bubbleTea'

export type FunFact = {
  kind: FunFactKind
  /** lateNight / smallest:交易 ISO 時間;busiestDay:'YYYY-MM-DD'(本地日期) */
  at?: string
  /** 金額(lateNight/repeatAmount/smallest)、筆數(busiestDay/topMerchant/favoriteWeekday)、杯數(bubbleTea) */
  value: number
  /** repeatAmount 的次數 */
  count?: number
  /** topMerchant 的商家名 */
  label?: string
  /** favoriteWeekday:1(週一)~ 7(週日),跟 App 的 DateTime.weekday 一致 */
  weekday?: number
}

/** 一杯大杯珍奶的參考價(依帳本幣別);不在表上的幣別不出這則。 */
export const BUBBLE_TEA_PRICE: Record<string, number> = {
  TWD: 65,
  CNY: 18,
  HKD: 28,
  USD: 6.5,
  JPY: 650,
  KRW: 5500,
}

const clean = (s: string | null | undefined) => {
  const v = (s ?? '').trim()
  return v ? v : null
}

export function collectFunFacts(
  txs: TransactionLite[],
  opts: { totalExpense: number; currency?: string },
): FunFact[] {
  const out: FunFact[] = []
  const list = txs.filter((t) => t.txType !== 'transfer')
  if (list.length === 0) return out

  // 最晚的一筆:凌晨 0–5 點視為「前一天的深夜」,所以排在 23 點之後。
  const lateKey = (iso: string) => {
    const d = new Date(iso)
    const h = d.getHours()
    return (h < 5 ? h + 24 : h) * 60 + d.getMinutes()
  }
  let late: TransactionLite | null = null
  for (const t of list) {
    const k = lateKey(t.happenedAt)
    if (k < 23 * 60) continue
    if (!late || k > lateKey(late.happenedAt)) late = t
  }
  if (late) out.push({ kind: 'lateNight', at: late.happenedAt, value: late.amount })

  // 單日最多筆(同筆數取較早的那天)。
  const perDay = new Map<string, number>()
  for (const t of list) {
    const d = localDayKey(t.happenedAt)
    perDay.set(d, (perDay.get(d) ?? 0) + 1)
  }
  let busiest: [string, number] | null = null
  for (const e of perDay) {
    if (!busiest || e[1] > busiest[1] || (e[1] === busiest[1] && e[0] < busiest[0])) busiest = e
  }
  if (busiest && busiest[1] >= 5) out.push({ kind: 'busiestDay', at: busiest[0], value: busiest[1] })

  // 最常光顧的商家。
  const merchants = new Map<string, number>()
  for (const t of list) {
    const m = clean(t.merchant)
    if (m) merchants.set(m, (merchants.get(m) ?? 0) + 1)
  }
  let topMerchant: [string, number] | null = null
  for (const e of merchants) {
    if (!topMerchant || e[1] > topMerchant[1] || (e[1] === topMerchant[1] && e[0] < topMerchant[0])) topMerchant = e
  }
  if (topMerchant && topMerchant[1] >= 3) {
    out.push({ kind: 'topMerchant', label: topMerchant[0], value: topMerchant[1] })
  }

  const expenses = list.filter((t) => t.txType === 'expense' && t.amount > 0)

  // 重複最多次的支出金額(以分為單位比對,避免浮點誤差)。
  const amounts = new Map<number, number>()
  for (const t of expenses) {
    const cents = Math.round(t.amount * 100)
    amounts.set(cents, (amounts.get(cents) ?? 0) + 1)
  }
  let topAmount: [number, number] | null = null
  for (const e of amounts) {
    if (!topAmount || e[1] > topAmount[1] || (e[1] === topAmount[1] && e[0] < topAmount[0])) topAmount = e
  }
  if (topAmount && topAmount[1] >= 5) {
    out.push({ kind: 'repeatAmount', value: topAmount[0] / 100, count: topAmount[1] })
  }

  // 最小的一筆支出。
  if (expenses.length >= 10) {
    const s = expenses.reduce((a, b) => (b.amount < a.amount ? b : a))
    out.push({ kind: 'smallest', at: s.happenedAt, value: s.amount })
  }

  // 最常記帳的星期幾(至少要有一點資料才有意義)。
  if (list.length >= 30) {
    const perWeekday = new Array<number>(8).fill(0)
    for (const t of list) {
      const js = new Date(t.happenedAt).getDay() // 0 = 週日
      perWeekday[js === 0 ? 7 : js]++
    }
    let best = 1
    for (let w = 2; w <= 7; w++) if (perWeekday[w] > perWeekday[best]) best = w
    out.push({ kind: 'favoriteWeekday', weekday: best, value: perWeekday[best] })
  }

  // 換算珍奶。
  const price = BUBBLE_TEA_PRICE[(opts.currency ?? '').toUpperCase()]
  if (price && opts.totalExpense >= price * 20) {
    out.push({ kind: 'bubbleTea', value: Math.floor(opts.totalExpense / price) })
  }
  return out
}

/** 依 `year`(+`salt`,例如帳本 id 的雜湊)洗牌,取前 `count` 則。 */
export function pickFunFacts(all: FunFact[], year: number, salt = 0, count = 3): FunFact[] {
  return seededShuffle(all, year * 7919 + salt).slice(0, count)
}

/** 決定性的洗牌(mulberry32),同一個種子永遠同一個順序。 */
export function seededShuffle<T>(list: readonly T[], seed: number): T[] {
  const out = [...list]
  let s = seed >>> 0
  const rand = () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

/** 字串 → 小整數(帳本 id 當洗牌 salt 用)。 */
export function hashSalt(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
  return Math.abs(h) % 100000
}
