import { useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { monthlyInsight, type AnnualReportData } from '../data'
import { monthQuiz } from '../data/quiz'
import { TKEY } from '../i18n'
import { QuizVerdict, StoryQuiz, useStory } from '../widgets/storyKit'

/**
 * 月度趋势:12 个月柱状图,高峰月 / 低谷月高亮 + 月份名飘字。
 */
export function PageMonthlyTrend({ data }: { data: AnnualReportData }) {
  const t = useT()
  const story = useStory()
  const quiz = monthQuiz(data)
  const answered = story?.quizAnswers.month
  const monthLabel = (month: number) => t(TKEY.page4MonthFmt, { month })

  if (quiz && story && answered === undefined) {
    return (
      <div className="relative h-full w-full">
        <div className="relative z-10 flex h-full items-center justify-center px-8">
          <StoryQuiz
            kicker={t(TKEY.quizKicker)}
            question={t(TKEY.quizMonth)}
            hint={t(TKEY.quizSkipHint)}
            options={quiz.options.map((m) => ({ label: monthLabel(m) }))}
            onAnswer={(i) => story.answerQuiz('month', i)}
          />
        </div>
      </div>
    )
  }

  return <MonthlyResult data={data} verdict={quiz && answered !== undefined ? { quiz, answered } : null} />
}

/** 答案頁(獨立元件:猜完才掛載,柱狀圖的進場動畫才會重新觸發)。 */
function MonthlyResult({
  data,
  verdict,
}: {
  data: AnnualReportData
  verdict: { quiz: { options: number[]; answer: number }; answered: number } | null
}) {
  const t = useT()
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { once: true, margin: '-15%' })
  const insight = monthlyInsight(data)
  const monthLabel = (month: number) => t(TKEY.page4MonthFmt, { month })
  const max = Math.max(...data.monthlyData.map((m) => m.expense), 1)

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-5xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        {verdict && (
          <QuizVerdict
            right={verdict.answered === verdict.quiz.answer}
            text={
              verdict.answered === verdict.quiz.answer
                ? t(TKEY.quizRight)
                : t(TKEY.quizWrong, { answer: monthLabel(verdict.quiz.options[verdict.quiz.answer]) })
            }
          />
        )}
        <h2 className="mb-3 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page4Title)}
        </h2>
        <div className="mb-10 flex gap-6 text-sm text-white/50">
          <span>
            {t(TKEY.page4Peak)}{' '}
            <span className="font-semibold text-[color:var(--story-accent)]">
              {t(TKEY.page4MonthFmt, { month: data.peakMonth })}
            </span>
          </span>
          <span>
            {t(TKEY.page4Trough)}{' '}
            <span className="font-semibold text-sky-300">
              {t(TKEY.page4MonthFmt, { month: data.troughMonth })}
            </span>
          </span>
        </div>

        <div ref={ref} className="flex h-64 w-full items-end justify-between gap-2 sm:gap-3">
          {data.monthlyData.map((m, i) => {
            const heightPct = (m.expense / max) * 100
            const isPeak = m.month === data.peakMonth
            const isTrough = m.month === data.troughMonth && m.expense > 0
            const color = isPeak ? 'var(--story-accent)' : isTrough ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.28)'
            return (
              <div key={m.month} className="flex h-full flex-1 flex-col items-center gap-2">
                <div className="relative flex min-h-0 w-full flex-1 items-end">
                  <motion.div
                    className="w-full rounded-t-md"
                    style={{ background: color }}
                    initial={{ height: 0 }}
                    animate={inView ? { height: `${heightPct}%` } : {}}
                    transition={{ duration: 1.0, delay: i * 0.06 + 0.2, ease: [0.16, 1, 0.3, 1] }}
                  />
                  {isPeak && m.expense > 0 && (
                    <motion.div
                      initial={{ opacity: 0, y: -4 }}
                      animate={inView ? { opacity: 1, y: 0 } : {}}
                      transition={{ delay: 1.4 }}
                      className="absolute -top-7 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs font-semibold text-[color:var(--story-accent)]"
                    >
                      ↑ {Math.round(m.expense).toLocaleString()}
                    </motion.div>
                  )}
                </div>
                <div
                  className={`text-xs ${
                    isPeak || isTrough ? 'font-semibold text-white' : 'text-white/40'
                  }`}
                >
                  {m.month}
                </div>
              </div>
            )
          })}
        </div>

        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.0} />
        </div>
      </div>
    </div>
  )
}
