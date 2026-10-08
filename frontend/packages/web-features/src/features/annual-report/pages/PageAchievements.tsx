import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { achievementsInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 成就墙:网格展示已解锁的成就。rare 成就有金色光晕脉动。
 */
export function PageAchievements({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = achievementsInsight(data)

  const empty = data.achievements.length === 0

  // achievement id → emoji
  const emojiMap: Record<string, string> = {
    'full-attendance': '👑',
    'frequent-recorder': '🏆',
    'half-year-keeper': '🎖️',
    'streak-100': '🔥',
    'streak-30': '⚡',
    'records-1k': '💎',
    'records-500': '⭐',
    'records-100': '🌟',
    'saver-pro': '💰',
    saver: '🏦',
    'frugal-progress': '🌱',
    'more-attentive': '✍️',
    'income-growth': '📈',
    'stock-explorer': '🧭',
    'stock-profit': '💹',
    'stock-sharpshooter': '🎯',
    'stock-dividend': '🍯',
    'stock-active': '⚡',
    'first-last': '🏁',
    'night-owl': '🌃',
  }

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        <h2 className="mb-10 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page11Title)}
        </h2>
        {empty ? (
          <p className="text-lg text-white/50">{t(TKEY.page11Empty)}</p>
        ) : (
          <div ref={ref} className="grid w-full grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4">
            {data.achievements.map((ach, i) => {
              const emoji = emojiMap[ach.id] || '🎯'
              return (
                <motion.div
                  key={ach.id}
                  initial={{ opacity: 0, y: 16, scale: 0.95 }}
                  animate={inView ? { opacity: 1, y: 0, scale: 1 } : {}}
                  transition={{
                    duration: 0.5,
                    delay: i * 0.08 + 0.2,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className={`relative flex flex-col gap-2 overflow-hidden rounded-2xl border p-4 backdrop-blur-sm sm:p-5 ${
                    ach.hidden
                      ? 'border-dashed border-[color:var(--story-accent)] bg-white/10'
                      : ach.rare
                        ? 'border-[color:var(--story-accent)] bg-white/15'
                        : 'border-white/15 bg-white/10'
                  }`}
                >
                  {ach.rare && (
                    <motion.div
                      className="pointer-events-none absolute -inset-1 rounded-2xl"
                      style={{
                        background:
                          'radial-gradient(circle at 30% 20%, color-mix(in srgb, var(--story-accent) 25%, transparent), transparent 60%)',
                      }}
                      animate={{ opacity: [0.6, 1, 0.6] }}
                      transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}
                    />
                  )}
                  <div className="relative flex items-center gap-2">
                    <span className="text-3xl sm:text-4xl">{emoji}</span>
                    {ach.hidden ? (
                      <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-[color:var(--story-accent)]">
                        🔓 {t(TKEY.achHidden)}
                      </span>
                    ) : ach.rare ? (
                      <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-[color:var(--story-accent)]">
                        {t(TKEY.achRare)}
                      </span>
                    ) : null}
                  </div>
                  <div className="relative text-base font-semibold text-white sm:text-lg">
                    {t(ach.titleKey)}
                  </div>
                  <div className="relative text-xs leading-relaxed text-white/70 sm:text-sm">
                    {t(ach.descKey)}
                  </div>
                </motion.div>
              )
            })}
          </div>
        )}
        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
        </div>
      </div>
    </div>
  )
}
