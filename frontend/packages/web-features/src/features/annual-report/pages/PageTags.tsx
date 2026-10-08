import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { tagsInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'

/**
 * 标签词云:Top 6 个标签按 count 大小展示。最大的标签字号最大,带 glow 效果。
 */
export function PageTags({ data }: { data: AnnualReportData }) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = tagsInsight(data)

  const empty = data.topTags.length === 0
  const max = empty ? 1 : Math.max(...data.topTags.map((t) => t.count))

  // 蜂蜜 / 余晖系
  const palette = ['var(--story-accent)', 'rgba(255,255,255,0.85)', 'rgba(255,255,255,0.7)', 'rgba(255,255,255,0.58)', 'rgba(255,255,255,0.46)', 'rgba(255,255,255,0.36)']

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page10Title)}
        </h2>
        {empty ? (
          <p className="text-lg text-white/50">{t(TKEY.page10Empty)}</p>
        ) : (
          <div ref={ref} className="flex w-full flex-wrap items-baseline gap-x-6 gap-y-3 sm:gap-x-8">
            {data.topTags.map((tag, i) => {
              const ratio = tag.count / max
              // 字号 1.0 → 4.0 rem
              const size = 1.2 + ratio * 2.6
              const color = palette[i] || palette[palette.length - 1]
              const opacity = 0.5 + ratio * 0.5
              return (
                <motion.span
                  key={tag.name}
                  initial={{ opacity: 0, y: 18, scale: 0.9 }}
                  animate={inView ? { opacity: 1, y: 0, scale: 1 } : {}}
                  transition={{
                    duration: 0.6,
                    delay: i * 0.12 + 0.2,
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="inline-flex items-baseline gap-1 font-bold tabular-nums"
                  style={{
                    fontSize: `${size}rem`,
                    color,
                    opacity,
                    textShadow: ratio > 0.6 ? `0 0 32px color-mix(in srgb, ${color} 40%, transparent)` : undefined,
                    lineHeight: 1.05,
                  }}
                >
                  {tag.name}
                  <span className="ml-1 text-base font-medium text-white/40 sm:text-lg">
                    {tag.count}
                    {t(TKEY.page10CountSuffix)}
                  </span>
                </motion.span>
              )
            })}
          </div>
        )}
        {!empty && (
          <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
            <InsightLine text={t(insight.textKey, insight.args)} delay={1.2} />
          </div>
        )}
      </div>
    </div>
  )
}
