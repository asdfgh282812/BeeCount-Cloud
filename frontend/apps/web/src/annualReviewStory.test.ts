import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  aggregate,
  categoryQuiz,
  collectFunFacts,
  monthQuiz,
  pickFunFacts,
  rotateHue,
  yearTheme,
  type TransactionLite,
} from '@beecount/web-features'

import {
  annualReviewReminderYear,
  markAnnualReviewSeen,
  readSeenYears,
} from './lib/annualReviewReminder'

let seq = 0
const tx = (at: Date, amount: number, extra: Partial<TransactionLite> = {}): TransactionLite => ({
  id: `t${seq++}`,
  txType: 'expense',
  amount,
  happenedAt: at.toISOString(),
  note: null,
  categoryName: '餐飲',
  categoryKind: 'expense',
  accountName: null,
  tagsList: [],
  ...extra,
})

const ledger = { id: 'L1', name: '日常', currency: 'TWD' }

describe('年度主題(生肖)', () => {
  it('2026 馬年是原始配色,2025 蛇年轉 330°,2020 鼠年', () => {
    expect(yearTheme(2026)).toMatchObject({ zodiac: 'horse', emoji: '🐴', hueShift: 0 })
    expect(yearTheme(2025)).toMatchObject({ zodiac: 'snake', hueShift: 330 })
    expect(yearTheme(2020).zodiac).toBe('rat')
    expect(yearTheme(2032).zodiac).toBe('rat')
    expect(yearTheme(2019).zodiac).toBe('pig')
  })

  it('色相旋轉 0° / 360° 不變,其他角度真的變色', () => {
    expect(rotateHue('#5B21B6', 0)).toBe('#5B21B6')
    expect(rotateHue('#5B21B6', 360)).toBe('#5B21B6')
    expect(rotateHue('#5B21B6', 120)).not.toBe('#5B21B6')
  })
})

describe('冷知識', () => {
  it('凌晨 1:30 比 23:50 更晚;單日 ≥ 5 筆才算記帳巔峰', () => {
    const txs = [
      tx(new Date(2025, 2, 3, 23, 50), 100),
      tx(new Date(2025, 2, 5, 1, 30), 80),
      ...Array.from({ length: 5 }, (_, i) => tx(new Date(2025, 4, 1, 9 + i), 10)),
    ]
    const facts = collectFunFacts(txs, { totalExpense: 0, currency: 'TWD' })
    const late = facts.find((f) => f.kind === 'lateNight')
    expect(new Date(late!.at!).getHours()).toBe(1)
    expect(facts.find((f) => f.kind === 'busiestDay')).toMatchObject({ at: '2025-05-01', value: 5 })
  })

  it('同金額 ≥ 5 次、商家 ≥ 3 次、珍奶換算只支援有定價的幣別', () => {
    const txs = [
      ...Array.from({ length: 5 }, (_, i) => tx(new Date(2025, 0, 2 + i, 12), 55, { merchant: '7-11' })),
    ]
    const twd = collectFunFacts(txs, { totalExpense: 6500, currency: 'TWD' })
    expect(twd.find((f) => f.kind === 'repeatAmount')).toMatchObject({ value: 55, count: 5 })
    expect(twd.find((f) => f.kind === 'topMerchant')).toMatchObject({ label: '7-11', value: 5 })
    expect(twd.find((f) => f.kind === 'bubbleTea')?.value).toBe(100)
    expect(collectFunFacts(txs, { totalExpense: 6500, currency: 'EUR' }).some((f) => f.kind === 'bubbleTea')).toBe(false)
  })

  it('同一年每次抽到的一樣,最多 3 則', () => {
    const all = collectFunFacts(
      Array.from({ length: 40 }, (_, i) => tx(new Date(2025, 0, 1 + i, i % 2 ? 23 : 12, 30), 55, { merchant: 'A' })),
      { totalExpense: 9999, currency: 'TWD' },
    )
    expect(all.length).toBeGreaterThan(3)
    const a = pickFunFacts(all, 2025, 7).map((f) => f.kind)
    expect(a).toHaveLength(3)
    expect(pickFunFacts(all, 2025, 7).map((f) => f.kind)).toEqual(a)
  })
})

describe('稀有稱號 / 隱藏成就', () => {
  it('過去年份每天都有記 → 傳說稱號「全勤記帳王」+ 隱藏成就「從頭記到尾」', () => {
    const txs = Array.from({ length: 365 }, (_, i) => tx(new Date(2025, 0, 1 + i, 12), 100))
    const d = aggregate({ thisYearTxs: txs, prevYearTxs: [], year: 2025, ledger, now: new Date(2026, 5, 1) })
    expect(d.elapsedDays).toBe(365)
    expect(d.persona).toMatchObject({ id: 'perfectAttendance', rarity: 'legendary' })
    expect(d.achievements.find((a) => a.id === 'first-last')?.hidden).toBe(true)
  })

  it('今年到目前為止每天都有記(≥ 60 天)也算全勤;不到 60 天不算', () => {
    const now = new Date(2026, 2, 10) // 3/10 → 69 天
    const txs = Array.from({ length: 69 }, (_, i) => tx(new Date(2026, 0, 1 + i, 12), 100))
    expect(aggregate({ thisYearTxs: txs, prevYearTxs: [], year: 2026, ledger, now }).persona.id).toBe('perfectAttendance')
    const early = new Date(2026, 1, 1) // 32 天
    const few = txs.slice(0, 32)
    expect(aggregate({ thisYearTxs: few, prevYearTxs: [], year: 2026, ledger, now: early }).persona.id).not.toBe(
      'perfectAttendance',
    )
  })

  it('儲蓄率 ≥ 50% → 超級存錢筒;凌晨 0–5 點 ≥ 10 筆支出 → 隱藏成就', () => {
    const txs = [
      tx(new Date(2025, 0, 5, 12), 100000, { txType: 'income' }),
      ...Array.from({ length: 10 }, (_, i) => tx(new Date(2025, 1, 1 + i * 3, 2), 100)),
    ]
    const d = aggregate({ thisYearTxs: txs, prevYearTxs: [], year: 2025, ledger, now: new Date(2026, 0, 1) })
    expect(d.persona).toMatchObject({ id: 'megaSaver', rarity: 'legendary' })
    expect(d.achievements.some((a) => a.id === 'night-owl' && a.hidden)).toBe(true)
    expect(d.achievements.some((a) => a.id === 'first-last')).toBe(false)
  })

  it('台灣 1/1 凌晨的交易算在當年第一天(用本地日期,不是 UTC)', () => {
    const txs = [tx(new Date(2025, 0, 1, 0, 30), 10), tx(new Date(2025, 11, 31, 23, 30), 10)]
    const d = aggregate({ thisYearTxs: txs, prevYearTxs: [], year: 2025, ledger, now: new Date(2026, 0, 2) })
    expect(d.recordedFirstAndLastDay).toBe(true)
  })
})

describe('先猜再揭曉', () => {
  const base = aggregate({
    thisYearTxs: [
      tx(new Date(2025, 0, 3, 12), 500, { categoryName: '餐飲' }),
      tx(new Date(2025, 3, 3, 12), 300, { categoryName: '交通' }),
      tx(new Date(2025, 6, 3, 12), 100, { categoryName: '娛樂' }),
    ],
    prevYearTxs: [],
    year: 2025,
    ledger,
  })

  it('分類題:選項是前三大分類,答案指向第一名,同一年順序固定', () => {
    const q = categoryQuiz(base)!
    expect([...q.options].sort()).toEqual(['交通', '娛樂', '餐飲'])
    expect(q.options[q.answer]).toBe('餐飲')
    expect(categoryQuiz(base)).toEqual(q)
  })

  it('月份題:最高月 + 兩個其他月;資料不夠就不出題', () => {
    const q = monthQuiz(base)!
    expect(q.options).toHaveLength(3)
    expect(q.options[q.answer]).toBe(1)
    expect(categoryQuiz({ ...base, topExpenseCategories: base.topExpenseCategories.slice(0, 2) })).toBeNull()
    expect(monthQuiz({ ...base, monthlyData: base.monthlyData.map((m) => (m.month === 1 ? m : { ...m, expense: 0 })) })).toBeNull()
  })
})

describe('首頁年度回顧提醒', () => {
  afterEach(() => vi.unstubAllGlobals())

  const stubStorage = () => {
    const store = new Map<string, string>()
    const dispatched: string[] = []
    vi.stubGlobal('window', {
      localStorage: {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => void store.set(k, v),
      },
      dispatchEvent: (e: Event) => {
        dispatched.push(e.type)
        return true
      },
    })
    vi.stubGlobal('CustomEvent', class extends Event {
      detail: unknown
      constructor(type: string, init?: { detail?: unknown }) {
        super(type)
        this.detail = init?.detail
      }
    })
    return dispatched
  }

  it('1/1 起提醒去年,沒有期限;看過就不提醒', () => {
    expect(annualReviewReminderYear(new Date(2026, 0, 1), new Set())).toBe(2025)
    expect(annualReviewReminderYear(new Date(2026, 9, 9), new Set())).toBe(2025)
    expect(annualReviewReminderYear(new Date(2026, 9, 9), new Set([2025]))).toBeNull()
  })

  it('看過去年 → 記下並通知;年中看今年不算', () => {
    const events = stubStorage()
    const now = new Date(2026, 9, 9)
    markAnnualReviewSeen(2026, now)
    expect(readSeenYears().has(2026)).toBe(false)
    markAnnualReviewSeen(2025, now)
    expect(readSeenYears().has(2025)).toBe(true)
    expect(events).toEqual(['beecount:annual-review-seen'])
    markAnnualReviewSeen(2025, now)
    expect(events).toHaveLength(1)
  })
})
