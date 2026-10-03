import { motion } from 'framer-motion'
import { useT } from '@beecount/ui'

import { HoneyBg } from '../widgets/HoneyBg'
import type { AnnualReportData, PersonaId } from '../data'
import { TKEY } from '../i18n'
import { currencySymbol } from '../../../lib/currencies'

const PERSONA_STYLE: Record<PersonaId, { emoji: string; hue: number }> = {
  streakKing: { emoji: '🔥', hue: 14 },
  investor: { emoji: '📈', hue: 350 },
  saver: { emoji: '🏦', hue: 150 },
  nightOwl: { emoji: '🦉', hue: 250 },
  weekendSpender: { emoji: '🎉', hue: 300 },
  focused: { emoji: '🎯', hue: 28 },
  frugal: { emoji: '🌱', hue: 120 },
  steady: { emoji: '🍯', hue: 42 },
}

/** 年度稱號:依這一年的實際內容挑一個稱號,並列出「為什麼是你」。 */
export function PagePersona({ data }: { data: AnnualReportData }) {
  const t = useT()
  const { id, reasons } = data.persona
  const style = PERSONA_STYLE[id]
  const keys = TKEY.persona[id]

  return (
    <div className="relative h-full w-full">
      <HoneyBg hue={style.hue} particleCount={26} />
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-center justify-center overflow-y-auto px-8 py-20 text-center sm:px-12">
        <div className="mb-4 text-xs uppercase tracking-[0.3em] text-white/45">
          {t(TKEY.personaTitle)}
        </div>
        <motion.div
          initial={{ opacity: 0, scale: 0.4, rotate: -12 }}
          animate={{ opacity: 1, scale: 1, rotate: 0 }}
          transition={{ type: 'spring', stiffness: 160, damping: 12, delay: 0.15 }}
          className="mb-4 text-7xl sm:text-8xl"
        >
          {style.emoji}
        </motion.div>
        <motion.h2
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="font-serif text-4xl font-black text-white sm:text-6xl"
          style={{
            background: 'linear-gradient(135deg, #FDE68A, #F59E0B 60%, #F97316)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}
        >
          {t(keys.title)}
        </motion.h2>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8, duration: 0.6 }}
          className="mt-4 max-w-xl text-lg leading-relaxed text-white/75 sm:text-xl"
        >
          {t(keys.desc)}
        </motion.p>

        <div className="mt-10 w-full">
          <div className="mb-3 text-[10px] uppercase tracking-widest text-white/35">
            {t(TKEY.personaReasonsTitle)}
          </div>
          <div className="flex flex-wrap justify-center gap-2.5">
            {reasons.map((r, i) => (
              <motion.span
                key={r.textKey}
                initial={{ opacity: 0, y: 12, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.5, delay: 1.1 + i * 0.18, ease: [0.16, 1, 0.3, 1] }}
                className="rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-sm text-amber-100"
              >
                {t(r.textKey, withCurrencySymbol(r.args))}
              </motion.span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/** reason 的 args 若帶 currency 代碼,補一個 symbol 參數給文案使用。 */
function withCurrencySymbol(args?: Record<string, string | number>) {
  if (!args || typeof args.currency !== 'string') return args
  return { ...args, symbol: currencySymbol(args.currency) }
}
