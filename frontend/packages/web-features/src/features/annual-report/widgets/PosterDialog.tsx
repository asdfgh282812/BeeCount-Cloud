import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { toPng } from 'html-to-image'
import QRCode from 'qrcode'
import { useLocale, useT } from '@beecount/ui'

import type { AnnualReportData } from '../data'
import { yearTheme } from '../data/yearTheme'
import { TKEY } from '../i18n'
import { currencySymbol } from '../../../lib/currencies'
import { fmtSignedMoney, stockTone } from './stock'
import { incomeTone, themedPalette, type PaletteKey, type StoryPalette } from './storyKit'
import { PERSONA_EMOJI } from '../pages/PagePersona'
import { factText } from '../pages/PageFunFacts'

export type PosterDialogProps = {
  data: AnnualReportData
  open: boolean
  onClose: () => void
  /** 海报底部的 url 文案(也用于二维码),默认当前 origin */
  url?: string
}

/**
 * 分享長圖:跟頁面同一套視覺——深色底 + 每章一張漸層卡片,配色依年度生肖旋轉
 * (跟 App `annual_report_poster.dart` 同結構)。封面卡直接放年度稱號。
 *
 * 長圖**不用任何動畫元件**:`html-to-image` 是拍當下的 DOM,動畫會拍到半途。
 */
export function PosterDialog({ data, open, onClose, url }: PosterDialogProps) {
  const t = useT()
  const { locale } = useLocale()
  const posterRef = useRef<HTMLDivElement>(null)
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const sym = currencySymbol(data.ledgerCurrency)
  const theme = useMemo(() => yearTheme(data.year), [data.year])
  const pal = (k: PaletteKey) => themedPalette(k, theme)

  const link = url || (typeof window !== 'undefined' ? window.location.origin : 'https://beecount.app')

  useEffect(() => {
    if (!open) return
    QRCode.toDataURL(link, {
      width: 240,
      margin: 0,
      color: { dark: '#111111', light: '#FFFFFF' },
    })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(null))
  }, [open, link])

  const handleDownload = async () => {
    if (!posterRef.current) return
    setDownloading(true)
    try {
      const dataUrl = await toPng(posterRef.current, {
        cacheBust: true,
        pixelRatio: 2,
        backgroundColor: POSTER_BG,
      })
      const a = document.createElement('a')
      a.href = dataUrl
      a.download = `beecount-${data.year}-${data.ledgerName.replace(/\s+/g, '-')}.png`
      a.click()
    } catch (e) {
      console.error('[poster] download failed', e)
    } finally {
      setDownloading(false)
    }
  }

  const money = (v: number) => `${sym}${Math.round(v).toLocaleString()}`
  const tone = incomeTone()
  const stock = data.stock?.currencies[0] ?? null
  const sTone = stockTone()
  const topCats = data.topExpenseCategories.slice(0, 3)
  const maxMonth = Math.max(...data.monthlyData.map((m) => m.expense), 1)
  const personaKeys = TKEY.persona[data.persona.id]

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/80 p-4 backdrop-blur-md"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 24, scale: 0.96 }}
            transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
            className="flex max-h-full flex-col items-center gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            {/* 長圖預覽(可捲動;下載時拍的是整張,不受捲動影響) */}
            <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl">
              <div
                ref={posterRef}
                className="flex w-[360px] max-w-full flex-col gap-3 p-4 text-white"
                style={{ background: POSTER_BG }}
              >
                {/* 封面 + 年度稱號 */}
                <Card p={pal('cover')}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xl">🐝</span>
                      <div className="flex flex-col leading-tight">
                        <span className="text-[10px] font-semibold uppercase tracking-widest text-white/70">
                          BeeCount Year in Review
                        </span>
                        <span className="text-xs text-white/60">{data.ledgerName}</span>
                      </div>
                    </div>
                    <span className="rounded-full bg-white/15 px-2.5 py-1 text-[11px] font-semibold">
                      {theme.emoji} {t(TKEY.theme[theme.zodiac].name)}
                    </span>
                  </div>
                  <div
                    className="mt-3 font-serif text-[5rem] font-black leading-none tracking-tight"
                    style={{ color: pal('cover').accent }}
                  >
                    {data.year}
                  </div>
                  <div className="mt-1 text-xs text-white/70">{t(TKEY.theme[theme.zodiac].tagline)}</div>
                  <div className="mt-4 flex items-center gap-3 rounded-xl bg-white/10 px-3 py-2.5">
                    <span className="text-3xl">{PERSONA_EMOJI[data.persona.id]}</span>
                    <div className="flex min-w-0 flex-col">
                      <span className="text-[10px] uppercase tracking-widest text-white/60">
                        {t(TKEY.personaRarityLabel, { rarity: t(TKEY.rarity[data.persona.rarity]) })}
                      </span>
                      <span className="truncate text-lg font-black" style={{ color: pal('cover').accent }}>
                        {t(personaKeys.title)}
                      </span>
                    </div>
                  </div>
                </Card>

                {/* 記了幾筆 */}
                <Card p={pal('records')} kicker={t(TKEY.page2Title)}>
                  <div className="grid grid-cols-3 gap-2">
                    <Stat label={t(TKEY.page2RecordsLabel)} value={data.totalRecords.toLocaleString()} accent={pal('records').accent} big />
                    <Stat label={t(TKEY.page2DaysLabel)} value={`${data.recordingDays}`} sub={`/${data.totalDays}`} />
                    <Stat label={t(TKEY.page9StreakLabel)} value={`${data.maxConsecutiveDays}`} sub={t(TKEY.page9DaysSuffix)} />
                  </div>
                </Card>

                {/* 收支 */}
                <Card p={pal('money')}>
                  <div className="grid grid-cols-3 gap-2">
                    <Stat label={t(TKEY.page2IncomeLabel)} value={money(data.totalIncome)} color={tone.income} />
                    <Stat label={t(TKEY.page2ExpenseLabel)} value={money(data.totalExpense)} color={tone.expense} />
                    <Stat label={t(TKEY.page3SavingsLabel)} value={money(data.netSavings)} accent={pal('money').accent} />
                  </div>
                </Card>

                {/* 錢去哪了 */}
                {topCats.length > 0 && (
                  <Card p={pal('categories')} kicker={t(TKEY.page5Title)}>
                    <div className="flex flex-col gap-2">
                      {topCats.map((c, i) => (
                        <div key={c.name} className="flex flex-col gap-1">
                          <div className="flex items-baseline justify-between text-sm">
                            <span className="font-semibold">{c.name}</span>
                            <span className="tabular-nums text-white/75">{c.percent.toFixed(0)}%</span>
                          </div>
                          <div className="h-2 overflow-hidden rounded-full bg-white/15">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${Math.max(3, c.percent)}%`,
                                background: i === 0 ? pal('categories').accent : 'rgba(255,255,255,0.6)',
                              }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  </Card>
                )}

                {/* 每月起伏 */}
                <Card p={pal('months')} kicker={t(TKEY.page4Title)}>
                  <div className="flex h-20 items-end gap-1">
                    {data.monthlyData.map((m) => (
                      <div key={m.month} className="flex flex-1 flex-col items-center gap-1">
                        <div
                          className="w-full rounded-t-sm"
                          style={{
                            height: `${Math.max(2, (m.expense / maxMonth) * 64)}px`,
                            background: m.month === data.peakMonth ? pal('months').accent : 'rgba(255,255,255,0.35)',
                          }}
                        />
                        <span className="text-[9px] text-white/60">{m.month}</span>
                      </div>
                    ))}
                  </div>
                </Card>

                {/* 冷知識 */}
                {data.funFacts.length > 0 && (
                  <Card p={pal('funFacts')} kicker={t(TKEY.factsKicker)}>
                    <ul className="flex flex-col gap-1.5 text-[13px] leading-snug">
                      {data.funFacts.map((f) => (
                        <li key={f.kind} className="flex gap-1.5">
                          <span style={{ color: pal('funFacts').accent }}>•</span>
                          <span>{factText(f, data.ledgerCurrency, locale, t)}</span>
                        </li>
                      ))}
                    </ul>
                  </Card>
                )}

                {/* 股票(有股票活動才出現) */}
                {stock && (
                  <Card p={pal('stockOverview')} kicker={t(TKEY.stockOverviewTitle)}>
                    <div className="flex items-baseline justify-between">
                      <span className="text-sm font-bold">{t(TKEY.stockStyle[stock.style].title)}</span>
                      {stock.sellCount > 0 ? (
                        <span className="text-lg font-black tabular-nums" style={{ color: sTone.pick(stock.realizedPnl) }}>
                          {fmtSignedMoney(stock.realizedPnl, stock.currency)}
                        </span>
                      ) : (
                        <span className="text-xs text-white/70">
                          {stock.buyCount + stock.sellCount} {t(TKEY.stockTimesSuffix)}
                        </span>
                      )}
                    </div>
                  </Card>
                )}

                {/* 成就 */}
                {data.achievements.length > 0 && (
                  <Card p={pal('achievements')} kicker={t(TKEY.page11Title)}>
                    <div className="flex flex-wrap gap-1.5">
                      {data.achievements.slice(0, 8).map((a) => (
                        <span
                          key={a.id}
                          className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                          style={
                            a.rare || a.hidden
                              ? { background: pal('achievements').accent, color: '#1F1300' }
                              : { background: 'rgba(255,255,255,0.15)', color: '#FFFFFF' }
                          }
                        >
                          {a.hidden ? '🔓 ' : ''}
                          {t(a.titleKey)}
                        </span>
                      ))}
                    </div>
                  </Card>
                )}

                {/* 底部 QR */}
                <div className="flex items-end justify-between px-1 pt-1">
                  <div className="flex flex-col">
                    <span className="text-[10px] font-medium uppercase tracking-widest text-white/45">Powered by</span>
                    <span className="text-sm font-bold" style={{ color: pal('finale').accent }}>
                      BeeCount
                    </span>
                    <span className="mt-0.5 text-[10px] text-white/45">{link.replace(/^https?:\/\//, '')}</span>
                  </div>
                  {qrDataUrl && (
                    <div className="rounded-md bg-white p-1">
                      <img src={qrDataUrl} alt="QR" className="block h-14 w-14" />
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* 操作按钮 */}
            <div className="flex shrink-0 gap-3">
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading}
                className="inline-flex items-center gap-2 rounded-full px-6 py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:opacity-60"
                style={{ background: pal('finale').accent }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                {downloading ? '...' : t(TKEY.posterDownload)}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/5 px-6 py-2.5 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/10"
              >
                {t(TKEY.closeButton)}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

const POSTER_BG = '#0B0B12'

function Card({ p, kicker, children }: { p: StoryPalette; kicker?: string; children: ReactNode }) {
  return (
    <div
      className="relative overflow-hidden rounded-2xl p-4"
      style={{ background: `linear-gradient(135deg, ${p.from} 0%, ${p.to} 100%)` }}
    >
      <div
        className="pointer-events-none absolute -right-12 -top-12 h-40 w-40 rounded-full"
        style={{ background: `radial-gradient(circle, ${p.accent}40, transparent 70%)` }}
      />
      <div className="relative">
        {kicker ? (
          <div className="mb-2.5 text-[10px] font-bold uppercase tracking-[0.2em]" style={{ color: p.accent }}>
            {kicker}
          </div>
        ) : null}
        {children}
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  sub,
  color,
  accent,
  big,
}: {
  label: string
  value: string
  sub?: string
  color?: string
  accent?: string
  big?: boolean
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-[10px] uppercase tracking-wider text-white/60">{label}</span>
      <span
        className={`truncate font-black tabular-nums ${big ? 'text-2xl' : 'text-base'}`}
        style={{ color: color ?? accent ?? '#FFFFFF' }}
      >
        {value}
        {sub ? <span className="ml-0.5 text-[10px] font-medium text-white/60">{sub}</span> : null}
      </span>
    </div>
  )
}
