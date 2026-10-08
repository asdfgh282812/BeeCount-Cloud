import { useT } from '@beecount/ui'

import { BigNumber } from '../widgets/BigNumber'
import { InsightLine } from '../widgets/InsightLine'
import { CurrencyChips, signedParts, stockTone } from '../widgets/stock'
import { stockOverviewInsight, type AnnualReportData } from '../data'
import { TKEY } from '../i18n'
import { currencySymbol } from '../../../lib/currencies'

/**
 * 股票總覽:已實現損益(大字,漲跌色跟股票設定)+ 買賣筆數 / 金額 / 股利 / 手續費稅。
 * 只有當年真的有股票活動才會被加進 carousel。
 */
export function PageStockOverview({
  data,
  currencyIndex,
  onCurrencyChange,
}: {
  data: AnnualReportData
  currencyIndex: number
  onCurrencyChange: (i: number) => void
}) {
  const t = useT()
  const currencies = data.stock?.currencies ?? []
  const s = currencies[currencyIndex] ?? currencies[0]
  if (!s) return null
  const tone = stockTone()
  const sym = currencySymbol(s.currency)
  const insight = stockOverviewInsight(s)
  const pnl = signedParts(s.realizedPnl, s.currency)
  const hasSell = s.sellCount > 0

  return (
    <div className="relative h-full w-full">
      <div className="relative z-10 mx-auto flex h-full max-w-4xl flex-col items-start [justify-content:safe_center] overflow-y-auto px-8 py-20 sm:px-12">
        <h2 className="mb-6 font-serif text-3xl font-bold text-white/90 sm:text-5xl">
          {t(TKEY.stockOverviewTitle)}
        </h2>
        <CurrencyChips currencies={currencies} index={currencyIndex} onChange={onCurrencyChange} />

        {hasSell && (
          <div className="mb-10 flex flex-col gap-2">
            <div className="text-xs uppercase tracking-widest text-white/40 sm:text-sm">
              {t(TKEY.stockRealizedLabel)}
            </div>
            <BigNumber
              key={`${s.currency}-pnl`}
              value={pnl.abs}
              format="currency"
              prefix={pnl.prefix}
              size={80}
              className={tone.pickClass(s.realizedPnl)}
            />
          </div>
        )}

        <div className="grid w-full grid-cols-2 gap-x-8 gap-y-8 sm:grid-cols-3 sm:gap-x-12">
          <Stat
            label={t(TKEY.stockBuyLabel)}
            sub={`${s.buyCount} ${t(TKEY.stockTimesSuffix)}`}
            value={<BigNumber key={`${s.currency}-b`} value={s.buyAmount} format="currency" prefix={sym} size={34} className="text-white" />}
          />
          <Stat
            label={t(TKEY.stockSellLabel)}
            sub={`${s.sellCount} ${t(TKEY.stockTimesSuffix)}`}
            value={<BigNumber key={`${s.currency}-s`} value={s.sellAmount} format="currency" prefix={sym} size={34} className="text-white" />}
          />
          <Stat
            label={t(TKEY.stockDividendLabel)}
            sub={`${s.dividendCount} ${t(TKEY.stockTimesSuffix)}`}
            value={<BigNumber key={`${s.currency}-d`} value={s.dividends} format="currency" prefix={sym} size={34} className="text-[color:var(--story-accent)]" />}
          />
          <Stat
            label={t(TKEY.stockCostLabel)}
            value={<BigNumber key={`${s.currency}-c`} value={s.fees + s.taxes} format="currency" prefix={sym} size={34} className="text-white/80" />}
          />
          <Stat
            label={t(TKEY.stockSymbolsLabel)}
            value={
              <BigNumber key={`${s.currency}-n`} value={s.symbolCount} suffix={` ${t(TKEY.stockSymbolsSuffix)}`} size={34} className="text-white" />
            }
          />
        </div>

        {currencies.length > 1 && (
          <p className="mt-6 text-xs text-white/40">{t(TKEY.stockOtherCurrencyHint)}</p>
        )}
        <div className="mt-10 max-w-2xl text-xl leading-relaxed text-white/80 sm:text-2xl">
          <InsightLine
            key={`${s.currency}-${insight.textKey}`}
            text={t(insight.textKey, insight.args)}
            delay={0.6}
          />
        </div>
      </div>
    </div>
  )
}

function Stat({ label, sub, value }: { label: string; sub?: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-xs uppercase tracking-widest text-white/40">{label}</div>
      <div>{value}</div>
      {sub && <div className="text-xs text-white/40">{sub}</div>}
    </div>
  )
}
