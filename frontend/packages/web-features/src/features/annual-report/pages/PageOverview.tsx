import { useT } from '@beecount/ui'

import { BigNumber } from '../widgets/BigNumber'
import { InsightLine } from '../widgets/InsightLine'
import { overviewInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'
import { currencySymbol } from '../../../lib/currencies'
import { incomeTone } from '../widgets/storyKit'

/**
 * 总览屏:4 个核心指标(总笔数 / 记账天数 / 总收入 / 总支出),翻牌大字。
 */
export function PageOverview({ data }: { data: AnnualReportData }) {
  const t = useT()
  const sym = currencySymbol(data.ledgerCurrency)
  const insight = overviewInsight(data)
  const tone = incomeTone()

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 pb-10 pt-16 sm:px-12">
        <h2 className="mb-12 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.page2Title)}
        </h2>
        <div className="grid w-full grid-cols-2 gap-x-8 gap-y-10 sm:gap-x-16">
          <Stat label={t(TKEY.page2RecordsLabel)} value={<BigNumber value={data.totalRecords} size={64} className="text-white" />} />
          <Stat label={t(TKEY.page2DaysLabel)} value={<BigNumber value={data.recordingDays} suffix={`/${data.totalDays}`} size={64} className="text-white" />} />
          <Stat
            label={t(TKEY.page2IncomeLabel)}
            value={<span style={{ color: tone.income }}><BigNumber value={data.totalIncome} format="currency" prefix={sym} size={56} /></span>}
          />
          <Stat
            label={t(TKEY.page2ExpenseLabel)}
            value={<span style={{ color: tone.expense }}><BigNumber value={data.totalExpense} format="currency" prefix={sym} size={56} /></span>}
          />
        </div>
        <div className="mt-14 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine text={t(insight.textKey, insight.args)} delay={0.6} />
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs uppercase tracking-widest text-white/40 sm:text-sm">{label}</div>
      <div>{value}</div>
    </div>
  )
}
