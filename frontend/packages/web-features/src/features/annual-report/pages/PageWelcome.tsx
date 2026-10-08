import { motion } from 'framer-motion'
import { useT } from '@beecount/ui'

import type { AnnualReportData } from '../data'
import { yearTheme } from '../data/yearTheme'
import { TKEY } from '../i18n'
import { ACCENT } from '../widgets/storyKit'

/**
 * 封面:年度主題標籤(「🐍 蛇年 · 靈動」)+ 年份大字 + 主題標語,右下角放
 * 大大的生肖圖騰。每年換一套(見 `data/yearTheme.ts`)。
 */
export function PageWelcome({ data }: { data: AnnualReportData }) {
  const t = useT()
  const theme = yearTheme(data.year)
  const themeKeys = TKEY.theme[theme.zodiac]
  return (
    <div className="relative h-full w-full overflow-hidden">
      {/* 生肖圖騰 */}
      <motion.div
        initial={{ opacity: 0, scale: 0.6, rotate: -18 }}
        animate={{ opacity: 0.22, scale: 1, rotate: -8 }}
        transition={{ duration: 1.2, delay: 0.3, ease: [0.16, 1, 0.3, 1] }}
        className="pointer-events-none absolute -bottom-[6vmin] -right-[4vmin] select-none text-[46vmin] leading-none"
        aria-hidden
      >
        {theme.emoji}
      </motion.div>

      <div className="relative z-10 flex h-full flex-col items-center justify-center px-8 text-center">
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
          className="mb-6 inline-flex items-center gap-2 rounded-full border border-white/25 bg-white/10 px-4 py-1.5 text-sm font-semibold text-white backdrop-blur-sm"
        >
          <span className="text-lg">{theme.emoji}</span>
          {t(themeKeys.name)}
        </motion.div>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, ease: 'easeOut' }}
          className="mb-2 text-xs uppercase tracking-[0.4em] text-white/55"
        >
          BeeCount Year in Review
        </motion.div>
        <motion.div
          initial={{ opacity: 0, scale: 0.85 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.9, delay: 0.2, ease: [0.16, 1, 0.3, 1] }}
          className={`mb-2 font-serif text-[9rem] font-black leading-none tracking-tight tabular-nums sm:text-[14rem] ${ACCENT}`}
        >
          {data.year}
        </motion.div>
        <motion.h1
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.6 }}
          className="mb-3 font-serif text-3xl font-bold sm:text-5xl"
        >
          {t(TKEY.page1Title)}
        </motion.h1>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 0.9 }}
          className="max-w-md text-base text-white/80 sm:text-lg"
        >
          {t(themeKeys.tagline)}
        </motion.p>
        <motion.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 1.1 }}
          className="mt-2 text-sm text-white/50"
        >
          {data.ledgerName}
        </motion.p>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1, y: [0, -8, 0] }}
          transition={{ delay: 1.4, opacity: { duration: 0.6 }, y: { duration: 1.6, repeat: Infinity, ease: 'easeInOut' } }}
          className="absolute bottom-12 text-xs text-white/50 sm:bottom-16"
        >
          {t(TKEY.page1Hint)}
        </motion.div>
      </div>
    </div>
  )
}
