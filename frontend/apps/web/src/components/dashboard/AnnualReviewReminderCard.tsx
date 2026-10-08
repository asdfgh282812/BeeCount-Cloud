import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'

import { fetchWorkspaceAnalytics } from '@beecount/api-client'
import {
  ANNUAL_REPORT_TKEY as TKEY,
  MIN_RECORDS_FOR_REPORT,
  themedPalette,
  yearTheme,
} from '@beecount/web-features'
import { useT } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import {
  ANNUAL_REVIEW_SEEN_EVENT,
  annualReviewReminderYear,
  dispatchOpenAnnualReport,
  markAnnualReviewSeen,
  readSeenYears,
} from '../../lib/annualReviewReminder'

/** (帳本, 年份) → 那一年夠不夠出報告。切回首頁不用每次重查。 */
const eligibleCache = new Map<string, boolean>()

/**
 * 首頁「{year} 年度回顧出爐了」提醒卡(規則見 `lib/annualReviewReminder.ts`)。
 * 外觀跟年度回顧封面同一套:封面漸層 + 那一年的生肖。點整張卡直接打開那一年的
 * 回顧;✕ = 不再提醒這一年。
 *
 * 去年在目前帳本的筆數不到年度報告門檻(`MIN_RECORDS_FOR_REPORT`)就不出現——
 * 不然點進去只會看到「資料太少」。
 */
export function AnnualReviewReminderCard() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [seen, setSeen] = useState<Set<number>>(() => readSeenYears())
  const year = annualReviewReminderYear(new Date(), seen)
  const cacheKey = `${activeLedgerId}:${year}`
  const [eligible, setEligible] = useState<boolean | null>(() => eligibleCache.get(cacheKey) ?? null)

  // 其他地方(打開回顧、別的分頁)記了看過 → 收起來
  useEffect(() => {
    const onSeen = (e: Event) => {
      const y = (e as CustomEvent<{ year: number }>).detail?.year
      setSeen((prev) => new Set([...prev, ...readSeenYears(), ...(typeof y === 'number' ? [y] : [])]))
    }
    const onStorage = () => setSeen(readSeenYears())
    window.addEventListener(ANNUAL_REVIEW_SEEN_EVENT, onSeen)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(ANNUAL_REVIEW_SEEN_EVENT, onSeen)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  useEffect(() => {
    if (year === null || !token || !activeLedgerId) return
    const cached = eligibleCache.get(cacheKey)
    if (cached !== undefined) {
      setEligible(cached)
      return
    }
    setEligible(null)
    let cancelled = false
    fetchWorkspaceAnalytics(token, {
      scope: 'year',
      metric: 'expense',
      period: String(year),
      ledgerId: activeLedgerId,
      tzOffsetMinutes: -new Date().getTimezoneOffset(),
    })
      .then((a) => {
        const ok = (a.summary?.transaction_count ?? 0) >= MIN_RECORDS_FOR_REPORT
        eligibleCache.set(cacheKey, ok)
        if (!cancelled) setEligible(ok)
      })
      .catch(() => {
        // 查不到就先不提醒(下次進首頁再試),不快取
        if (!cancelled) setEligible(false)
      })
    return () => {
      cancelled = true
    }
  }, [year, token, activeLedgerId, cacheKey])

  const theme = useMemo(() => (year === null ? null : yearTheme(year)), [year])
  if (year === null || !theme || !eligible) return null
  const p = themedPalette('cover', theme)

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => dispatchOpenAnnualReport(year)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          dispatchOpenAnnualReport(year)
        }
      }}
      className="relative flex cursor-pointer items-center gap-3 overflow-hidden rounded-2xl px-4 py-3.5 text-white shadow-lg transition hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary md:px-5"
      style={{ background: `linear-gradient(135deg, ${p.from} 0%, ${p.to} 100%)` }}
      data-testid="annual-review-reminder"
    >
      <div
        className="pointer-events-none absolute -right-10 -top-16 h-40 w-40 rounded-full"
        style={{ background: `radial-gradient(circle, ${p.accent}55, transparent 70%)` }}
      />
      <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-white/15 text-2xl">
        {theme.emoji}
      </div>
      <div className="relative min-w-0 flex-1">
        <div className="truncate text-sm font-bold md:text-base">{t(TKEY.reminderTitle, { year })}</div>
        <div className="truncate text-xs text-white/75">
          {t(TKEY.reminderBody, { theme: t(TKEY.theme[theme.zodiac].name) })}
        </div>
      </div>
      <span
        className="relative shrink-0 rounded-full px-3.5 py-1.5 text-xs font-bold text-black"
        style={{ background: p.accent }}
      >
        {t(TKEY.reminderCta)}
      </span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          markAnnualReviewSeen(year)
        }}
        className="relative -mr-1 shrink-0 rounded-full p-1.5 text-white/70 transition hover:bg-white/15 hover:text-white"
        aria-label={t(TKEY.reminderDismiss)}
        title={t(TKEY.reminderDismiss)}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
