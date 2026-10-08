import { motion } from 'framer-motion'
import { useT } from '@beecount/ui'

import { outroInsight, type AnnualReportData, type PersonaId, type PersonaRarity } from '../data'
import { TKEY } from '../i18n'
import { ACCENT, StoryConfetti, useStory, themedPalette } from '../widgets/storyKit'
import { currencySymbol } from '../../../lib/currencies'

export const PERSONA_EMOJI: Record<PersonaId, string> = {
  perfectAttendance: '👑',
  streakKing: '🔥',
  megaSaver: '💰',
  investor: '📈',
  saver: '🏦',
  nightOwl: '🦉',
  weekendSpender: '🎉',
  focused: '🎯',
  frugal: '🌱',
  steady: '🍯',
}

const RARITY_STYLE: Record<PersonaRarity, string> = {
  common: 'border-white/25 bg-white/10 text-white/80',
  rare: 'border-sky-300/50 bg-sky-400/20 text-sky-100',
  legendary: 'border-amber-300/60 bg-amber-400/25 text-amber-100',
}

export type PagePersonaProps = {
  data: AnnualReportData
  onShare?: () => void
  onRestart: () => void
  onClose: () => void
}

/**
 * 壓軸:年度稱號揭曉(徽章彈出 + 稀有度標籤,稀有以上灑彩帶),「為什麼是你」,
 * 最後是致謝與分享 / 再看一次 / 關閉。
 */
export function PagePersona({ data, onShare, onRestart, onClose }: PagePersonaProps) {
  const t = useT()
  const story = useStory()
  const { id, rarity, reasons } = data.persona
  const keys = TKEY.persona[id]
  const insight = outroInsight(data)
  const accent = themedPalette('finale', story?.theme).accent

  return (
    <div className="relative h-full w-full">
      {rarity !== 'common' && (
        <StoryConfetti seed={data.year} colors={[accent, '#FFFFFF', '#F472B6', '#60A5FA', '#34D399']} />
      )}
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-center [justify-content:safe_center] overflow-y-auto px-8 py-20 text-center sm:px-12">
        <div className="mb-3 text-xs uppercase tracking-[0.3em] text-white/55">{t(TKEY.personaTitle)}</div>
        <motion.div
          initial={{ opacity: 0, scale: 0.3, rotate: -16 }}
          animate={{ opacity: 1, scale: 1, rotate: 0 }}
          transition={{ type: 'spring', stiffness: 160, damping: 11, delay: 0.15 }}
          className="mb-4 flex h-28 w-28 items-center justify-center rounded-full border-2 border-[color:var(--story-accent)] bg-white/10 text-6xl shadow-[0_0_60px_-10px_var(--story-accent)] sm:h-32 sm:w-32 sm:text-7xl"
        >
          {PERSONA_EMOJI[id]}
        </motion.div>
        <motion.span
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.55, duration: 0.4 }}
          className={`mb-3 rounded-full border px-3 py-1 text-xs font-bold tracking-widest ${RARITY_STYLE[rarity]}`}
        >
          {t(TKEY.personaRarityLabel, { rarity: t(TKEY.rarity[rarity]) })}
        </motion.span>
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className={`font-serif text-4xl font-black sm:text-6xl ${ACCENT}`}
        >
          {t(keys.title)}
        </motion.h2>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8, duration: 0.6 }}
          className="mt-4 max-w-xl text-lg leading-relaxed text-white/80 sm:text-xl"
        >
          {t(keys.desc)}
        </motion.p>

        {reasons.length > 0 && (
          <div className="mt-8 w-full">
            <div className="mb-3 text-[10px] uppercase tracking-widest text-white/45">{t(TKEY.personaReasonsTitle)}</div>
            <div className="flex flex-wrap justify-center gap-2.5">
              {reasons.map((r, i) => (
                <motion.span
                  key={r.textKey}
                  initial={{ opacity: 0, y: 12, scale: 0.95 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  transition={{ duration: 0.5, delay: 1.1 + i * 0.18, ease: [0.16, 1, 0.3, 1] }}
                  className="rounded-full border border-white/20 bg-white/10 px-4 py-2 text-sm text-white"
                >
                  {t(r.textKey, withCurrencySymbol(r.args))}
                </motion.span>
              ))}
            </div>
          </div>
        )}

        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 1.7 }}
          className="mt-10 max-w-xl text-base text-white/70 sm:text-lg"
        >
          {t(insight.textKey, insight.args)} {t(TKEY.page12Body)}
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 2.0 }}
          className="mt-8 flex flex-col gap-3 sm:flex-row sm:gap-4"
        >
          {onShare && (
            <button
              type="button"
              onClick={onShare}
              className="inline-flex items-center justify-center gap-2 rounded-full bg-[color:var(--story-accent)] px-6 py-3 text-sm font-semibold text-black transition hover:scale-105 sm:px-8 sm:text-base"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="18" cy="5" r="3" />
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
              {t(TKEY.shareButton)}
            </button>
          )}
          <button
            type="button"
            onClick={onRestart}
            className="inline-flex items-center justify-center gap-2 rounded-full border border-white/25 bg-white/10 px-6 py-3 text-sm font-semibold text-white backdrop-blur-sm transition hover:bg-white/20 sm:px-8 sm:text-base"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            {t(TKEY.restartButton)}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center justify-center gap-2 rounded-full px-6 py-3 text-sm font-semibold text-white/70 transition hover:text-white sm:px-8 sm:text-base"
          >
            {t(TKEY.closeButton)}
          </button>
        </motion.div>
      </div>
    </div>
  )
}

/** reason 的 args 若帶 currency 代碼,補一個 symbol 參數給文案使用。 */
function withCurrencySymbol(args?: Record<string, string | number>) {
  if (!args || typeof args.currency !== 'string') return args
  return { ...args, symbol: currencySymbol(args.currency) }
}
