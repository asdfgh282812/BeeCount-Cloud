import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { AnimatedBars } from '../widgets/AnimatedBar'
import { categoryInsight, type AnnualReportData } from '../data'
import { categoryQuiz } from '../data/quiz'
import { TKEY } from '../i18n'
import { QuizVerdict, StoryQuiz, useStory } from '../widgets/storyKit'

/**
 * 錢去哪了:先猜「錢最多花在哪個分類」,選完才揭曉 Top 5 支出分類。
 */
export function PageCategories({ data }: { data: AnnualReportData }) {
  const t = useT()
  const story = useStory()
  const insight = categoryInsight(data)
  const quiz = categoryQuiz(data)
  const answered = story?.quizAnswers.category

  if (quiz && story && answered === undefined) {
    return (
      <div className="relative h-full w-full">
        <div className="relative z-10 flex h-full items-center justify-center px-8">
          <StoryQuiz
            kicker={t(TKEY.quizKicker)}
            question={t(TKEY.quizCategory)}
            hint={t(TKEY.quizSkipHint)}
            options={quiz.options.map((name) => ({ label: name, emoji: emojiForCategory(name) }))}
            onAnswer={(i) => story.answerQuiz('category', i)}
          />
        </div>
      </div>
    )
  }

  // 第一名用本章重點色,其餘白色漸淡
  const palette = ['var(--story-accent)', 'rgba(255,255,255,0.8)', 'rgba(255,255,255,0.6)', 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.32)']

  const items = data.topExpenseCategories.map((c, i) => ({
    label: c.name,
    value: c.total,
    percent: c.percent,
    color: palette[i] || palette[palette.length - 1],
    emoji: emojiForCategory(c.name),
  }))

  const empty = items.length === 0

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        {quiz && answered !== undefined && (
          <QuizVerdict
            right={answered === quiz.answer}
            text={
              answered === quiz.answer
                ? t(TKEY.quizRight)
                : t(TKEY.quizWrong, { answer: quiz.options[quiz.answer] })
            }
          />
        )}
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page5Title)}
        </h2>
        {empty ? (
          <p className="text-lg text-white/50">{t(TKEY.page5Empty)}</p>
        ) : (
          <div className="w-full">
            <AnimatedBars items={items} formatValue={(v) => Math.round(v).toLocaleString()} />
          </div>
        )}
        {!empty && (
          <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
            <InsightLine text={t(insight.textKey, insight.args)} delay={1.4} />
          </div>
        )}
      </div>
    </div>
  )
}

function emojiForCategory(name: string): string {
  const map: Record<string, string> = {
    餐饮: '🍔', 食物: '🍔', 早餐: '☕', 咖啡: '☕',
    交通: '🚗', '打车': '🚕', 公交: '🚌',
    购物: '🛍️', 服装: '👗', 电子: '💻',
    娱乐: '🎮', 电影: '🎬', 旅行: '✈️',
    住房: '🏠', '房租': '🏠', 水电: '💡',
    医疗: '🏥', 健康: '💊', 健身: '🏋️',
    教育: '📚', 学习: '📚',
    礼物: '🎁', 红包: '🧧',
    宠物: '🐾',
    // 繁體(台灣使用者的分類名)
    餐飲: '🍔', 飲料: '🧋', 計程車: '🚕', 公車: '🚌', 捷運: '🚇',
    購物: '🛍️', 服飾: '👗', 電子: '💻', 娛樂: '🎮', 電影: '🎬', 旅遊: '✈️',
    水電: '💡', 醫療: '🏥',
    學習: '📚', 禮物: '🎁', 紅包: '🧧', 寵物: '🐾',
  }
  for (const k of Object.keys(map)) {
    if (name.includes(k)) return map[k]
  }
  return '💰'
}
