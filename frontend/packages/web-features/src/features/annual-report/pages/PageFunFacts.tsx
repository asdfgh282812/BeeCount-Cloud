import { motion } from 'framer-motion'
import { useLocale, useT } from '@beecount/ui'

import type { AnnualReportData } from '../data'
import type { FunFact, FunFactKind } from '../data/funFacts'
import { TKEY } from '../i18n'
import { ACCENT } from '../widgets/storyKit'
import { currencySymbol } from '../../../lib/currencies'

const EMOJI: Record<FunFactKind, string> = {
  lateNight: '🌙',
  busiestDay: '📅',
  topMerchant: '🏪',
  repeatAmount: '🔁',
  smallest: '🪙',
  favoriteWeekday: '🗓️',
  bubbleTea: '🧋',
}

/**
 * 冷知識:這一年資料裡挑出來的小發現(最多 3 則)。抽哪幾則用年份 + 帳本當
 * 種子決定(`data/funFacts.ts`),同一年每次打開都一樣,換一年就不同。
 */
export function PageFunFacts({ data }: { data: AnnualReportData }) {
  const t = useT()
  const { locale } = useLocale()
  const text = (f: FunFact) => factText(f, data.ledgerCurrency, locale, t)

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        <div className={`mb-3 text-xs font-bold uppercase tracking-[0.3em] ${ACCENT}`}>{t(TKEY.factsKicker)}</div>
        <h2 className="mb-10 font-serif text-3xl font-bold text-white/95 sm:text-5xl">{t(TKEY.factsTitle)}</h2>
        <div className="flex w-full flex-col gap-4">
          {data.funFacts.map((f, i) => (
            <motion.div
              key={f.kind}
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.5, delay: 0.25 + i * 0.35, ease: [0.16, 1, 0.3, 1] }}
              className="flex items-center gap-4 rounded-2xl border border-white/15 bg-white/10 p-4 backdrop-blur-sm sm:p-5"
            >
              <span className="text-3xl sm:text-4xl">{EMOJI[f.kind]}</span>
              <span className="text-base leading-relaxed text-white sm:text-xl">{text(f)}</span>
            </motion.div>
          ))}
        </div>
      </div>
    </div>
  )
}

type TFn = (key: string, params?: Record<string, string | number>) => string

/** 把一則冷知識轉成在地化文字(頁面與海報共用)。 */
export function factText(f: FunFact, currency: string, locale: string, t: TFn): string {
  const sym = currencySymbol(currency)
  const money = (v: number) => `${sym}${v.toLocaleString(locale, { maximumFractionDigits: 2 })}`
  const date = (d: Date) => d.toLocaleDateString(locale, { month: 'long', day: 'numeric' })
  const parseDay = (key: string) => {
    const [y, m, d] = key.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  switch (f.kind) {
    case 'lateNight': {
      const at = new Date(f.at ?? '')
      const time = at.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', hour12: false })
      return t(TKEY.fact.lateNight, { time, amount: money(f.value) })
    }
    case 'busiestDay':
      return t(TKEY.fact.busiestDay, { date: date(parseDay(f.at ?? '')), count: f.value })
    case 'topMerchant':
      return t(TKEY.fact.topMerchant, { name: f.label ?? '', count: f.value })
    case 'repeatAmount':
      return t(TKEY.fact.repeatAmount, { amount: money(f.value), count: f.count ?? 0 })
    case 'smallest':
      return t(TKEY.fact.smallest, { amount: money(f.value), date: date(new Date(f.at ?? '')) })
    case 'favoriteWeekday': {
      // 2024-01-01 是週一;weekday 1..7 = 週一..週日
      const day = new Date(2024, 0, f.weekday ?? 1).toLocaleDateString(locale, { weekday: 'long' })
      return t(TKEY.fact.favoriteWeekday, { day })
    }
    case 'bubbleTea':
      return t(TKEY.fact.bubbleTea, { cups: f.value.toLocaleString(locale) })
  }
}
