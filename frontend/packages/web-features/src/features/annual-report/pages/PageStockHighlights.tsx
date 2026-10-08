import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { CurrencyChips, MARKET_FLAG, fmtMoney, fmtSignedMoney, stockTone } from '../widgets/stock'
import type { AnnualReportData, StockCurrencySummary, StockSellHighlight, StockStyle } from '../data'
import { TKEY } from '../i18n'

const STYLE_EMOJI: Record<StockStyle, string> = {
  activeTrader: '⚡',
  dividendHunter: '🍯',
  longTermHolder: '🌳',
  swingTrader: '🌊',
  beginner: '🌱',
}

/**
 * 股票亮點:風格稱號 / 勝率環 / 最賺最賠一筆 / 領息王 / 最常交易 / 每月已實現損益。
 * 每一塊只在有資料時才出現(例如沒賣出就沒有勝率與最賺最賠)。
 */
export function PageStockHighlights({
  data,
  currencyIndex,
  onCurrencyChange,
}: {
  data: AnnualReportData
  currencyIndex: number
  onCurrencyChange: (i: number) => void
}) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-10%' })
  const currencies = data.stock?.currencies ?? []
  const s = currencies[currencyIndex] ?? currencies[0]
  if (!s) return null
  const tone = stockTone()
  const styleKeys = TKEY.stockStyle[s.style]

  return (
    <div className="relative h-full w-full">
      <div
        ref={ref}
        className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 py-20 sm:px-12"
      >
        <h2 className="mb-5 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.stockHighlightsTitle)}
        </h2>
        <CurrencyChips currencies={currencies} index={currencyIndex} onChange={onCurrencyChange} />

        {/* 風格稱號 */}
        <motion.div
          key={`${s.currency}-style`}
          initial={{ opacity: 0, y: 14 }}
          animate={inView ? { opacity: 1, y: 0 } : {}}
          transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
          className="mb-6 flex w-full items-center gap-4 rounded-2xl border border-white/20 bg-white/10 p-4 sm:p-5"
        >
          <span className="text-4xl sm:text-5xl">{STYLE_EMOJI[s.style]}</span>
          <div>
            <div className="text-[10px] uppercase tracking-widest text-[color:var(--story-accent)]">
              {t(TKEY.stockStyleLabel)}
            </div>
            <div className="text-xl font-bold text-white sm:text-2xl">{t(styleKeys.title)}</div>
            <div className="text-sm text-white/65">{t(styleKeys.desc)}</div>
          </div>
        </motion.div>

        <div className="grid w-full grid-cols-1 gap-4 sm:grid-cols-[auto_1fr] sm:gap-6">
          {s.winRate !== null && <WinRing s={s} inView={inView} />}
          <div className="grid grid-cols-1 gap-3">
            {s.bestSell && (
              <SellCard label={t(TKEY.stockBestLabel)} sell={s.bestSell} currency={s.currency} color={tone.gainHex} />
            )}
            {s.worstSell && (
              <SellCard label={t(TKEY.stockWorstLabel)} sell={s.worstSell} currency={s.currency} color={tone.lossHex} />
            )}
            {!s.bestSell && !s.worstSell && (
              <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm text-white/60">
                {t(TKEY.stockNoSellYet)}
              </div>
            )}
          </div>
        </div>

        <div className="mt-4 flex w-full flex-wrap gap-3">
          {s.topDividendSymbol && (
            <Chip
              label={t(TKEY.stockTopDividendLabel)}
              main={s.topDividendSymbol.name}
              sub={fmtMoney(s.topDividendSymbol.amount, s.currency)}
            />
          )}
          {s.topSymbolByTrades && (
            <Chip
              label={t(TKEY.stockTopTradesLabel)}
              main={s.topSymbolByTrades.name}
              sub={`${s.topSymbolByTrades.count} ${t(TKEY.stockTimesSuffix)}`}
            />
          )}
          {s.marketBreakdown.map((m) => (
            <span key={m.market} className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-white/70">
              {MARKET_FLAG[m.market] ?? '🌐'} {m.market} · {m.count}
            </span>
          ))}
        </div>

        {s.monthlyRealizedPnl.some((v) => v !== 0) && (
          <MonthlyPnl s={s} inView={inView} label={t(TKEY.stockMonthlyLabel)} monthFmt={(m) => t(TKEY.stockMonthFmt, { month: m })} />
        )}
      </div>
    </div>
  )
}

function WinRing({ s, inView }: { s: StockCurrencySummary; inView: boolean }) {
  const t = useT()
  const R = 44
  const C = 2 * Math.PI * R
  const rate = s.winRate ?? 0
  return (
    <div className="flex items-center gap-4 rounded-2xl border border-white/10 bg-white/5 p-4">
      <svg width="112" height="112" viewBox="0 0 112 112" className="shrink-0">
        <circle cx="56" cy="56" r={R} fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="10" />
        <motion.circle
          key={s.currency}
          cx="56"
          cy="56"
          r={R}
          fill="none"
          stroke="var(--story-accent)"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={C}
          initial={{ strokeDashoffset: C }}
          animate={inView ? { strokeDashoffset: C * (1 - rate / 100) } : {}}
          transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
          transform="rotate(-90 56 56)"
        />
        <text x="56" y="62" textAnchor="middle" fontSize="22" fontWeight="700" fill="#fff">
          {rate.toFixed(0)}%
        </text>
      </svg>
      <div>
        <div className="text-xs uppercase tracking-widest text-white/40">{t(TKEY.stockWinRateLabel)}</div>
        <div className="mt-1 text-sm text-white/70">
          {t(TKEY.stockWinLossDetail, { win: s.winCount, loss: s.lossCount })}
        </div>
      </div>
    </div>
  )
}

function SellCard({
  label,
  sell,
  currency,
  color,
}: {
  label: string
  sell: StockSellHighlight
  currency: string
  color: string
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-4">
      <div className="text-[10px] uppercase tracking-widest text-white/40">{label}</div>
      <div className="mt-1 flex items-baseline justify-between gap-3">
        <span className="truncate text-base font-semibold text-white">
          {sell.name}
          <span className="ml-2 text-xs font-normal text-white/40">{sell.date ?? ''}</span>
        </span>
        <span className="shrink-0 text-lg font-bold tabular-nums" style={{ color }}>
          {fmtSignedMoney(sell.pnl, currency)}
          {sell.returnPercent !== null && (
            <span className="ml-1.5 text-xs font-medium opacity-80">
              {sell.returnPercent > 0 ? '+' : ''}
              {sell.returnPercent.toFixed(1)}%
            </span>
          )}
        </span>
      </div>
    </div>
  )
}

function Chip({ label, main, sub }: { label: string; main: string; sub: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 px-4 py-2.5">
      <div className="text-[10px] uppercase tracking-widest text-white/40">{label}</div>
      <div className="text-sm font-semibold text-white">
        {main} <span className="ml-1 text-xs font-normal text-[color:var(--story-accent)]">{sub}</span>
      </div>
    </div>
  )
}

/** 每月已實現損益:以中線為基準的上下柱狀(賺往上、賠往下,顏色跟股票設定)。 */
function MonthlyPnl({
  s,
  inView,
  label,
  monthFmt,
}: {
  s: StockCurrencySummary
  inView: boolean
  label: string
  monthFmt: (m: number) => string
}) {
  const tone = stockTone()
  const max = Math.max(...s.monthlyRealizedPnl.map((v) => Math.abs(v)), 1)
  return (
    <div className="mt-6 w-full">
      <div className="mb-2 text-[10px] uppercase tracking-widest text-white/40">{label}</div>
      <div className="flex h-28 w-full items-center gap-1.5">
        {s.monthlyRealizedPnl.map((v, i) => {
          const h = (Math.abs(v) / max) * 50
          return (
            <div key={i} title={`${monthFmt(i + 1)} ${fmtSignedMoney(v, s.currency)}`} className="relative flex h-full flex-1 flex-col">
              <div className="flex flex-1 items-end">
                {v > 0 && (
                  <motion.div
                    className="w-full rounded-t-sm"
                    style={{ background: tone.pick(v) }}
                    initial={{ height: 0 }}
                    animate={inView ? { height: `${h * 2}%` } : {}}
                    transition={{ duration: 0.8, delay: i * 0.05 + 0.3, ease: [0.16, 1, 0.3, 1] }}
                  />
                )}
              </div>
              <div className="h-px bg-white/20" />
              <div className="flex flex-1 items-start">
                {v < 0 && (
                  <motion.div
                    className="w-full rounded-b-sm"
                    style={{ background: tone.pick(v) }}
                    initial={{ height: 0 }}
                    animate={inView ? { height: `${h * 2}%` } : {}}
                    transition={{ duration: 0.8, delay: i * 0.05 + 0.3, ease: [0.16, 1, 0.3, 1] }}
                  />
                )}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-1 flex gap-1.5 text-[10px] text-white/30">
        {s.monthlyRealizedPnl.map((_, i) => (
          <span key={i} className="flex-1 text-center">{i + 1}</span>
        ))}
      </div>
    </div>
  )
}
