import { useT } from '@beecount/ui'

import { InsightLine } from '../widgets/InsightLine'
import { AnimatedBars } from '../widgets/AnimatedBar'
import { accountsInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'
import { currencySymbol } from '../../../lib/currencies'

/** 帳戶排行:哪幾個帳戶 / 信用卡是你一年下來的主力(依支出筆數)。 */
export function PageAccounts({ data }: { data: AnnualReportData }) {
  const t = useT()
  const sym = currencySymbol(data.ledgerCurrency)
  const insight = accountsInsight(data)
  const palette = ['#34D399', '#2DD4BF', '#38BDF8', '#818CF8']
  const items = data.topAccounts.map((a, i) => ({
    label: `${a.name} · ${a.count}${t(TKEY.accountsCountSuffix)}`,
    value: a.total,
    percent: data.totalExpense > 0 ? (a.total / data.totalExpense) * 100 : 0,
    color: palette[i] ?? palette[palette.length - 1],
    emoji: ['🥇', '🥈', '🥉', '🏅'][i],
  }))

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-3xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.accountsTitle)}
        </h2>
        <div className="w-full">
          <AnimatedBars items={items} formatValue={(v) => `${sym}${Math.round(v).toLocaleString()}`} />
        </div>
        <div className="mt-12 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={1.2} />
        </div>
      </div>
    </div>
  )
}
