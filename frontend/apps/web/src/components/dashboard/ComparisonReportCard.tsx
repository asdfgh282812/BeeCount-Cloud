import { useEffect, useState } from 'react'

import type { ComparisonReport, ComparisonReportMetric } from '@beecount/api-client'
import { fetchComparisonReport } from '@beecount/api-client'
import { Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { Amount } from '@beecount/web-features'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'

type Scope = 'month' | 'year'

/**
 * 比較報表(§2.10 Phase 5 MOZE_FEATURE_GAP_SD.md)—— 環比/同比小卡片。
 *
 * 刻意做成自給自足、不接 OverviewPage 既有的 usePageCache/analytics 抓取
 * 管線:那套管线是给"当前期"仪表盘用的多个联动请求,这里只是一个独立的
 * "跟上一期/去年同期比较"小工具,自己 useEffect fetch 风险最低、改动面最小
 * (对齐 AnomalyMonthsAttribution 之类挂件的既有做法——自己是 Card,自己管
 * 自己的 loading/error)。
 */
export function ComparisonReportCard() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()

  const [scope, setScope] = useState<Scope>('month')
  // month scope: 1 = 上個月(環比),12 = 去年同月(同比)。year scope 固定 1。
  const [monthOffset, setMonthOffset] = useState<1 | 12>(1)

  const [report, setReport] = useState<ComparisonReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  const offset = scope === 'month' ? monthOffset : 1

  useEffect(() => {
    if (!token) return
    let cancelled = false
    setLoading(true)
    setError(false)
    fetchComparisonReport(token, {
      scope,
      offset,
      ledgerId: activeLedgerId || undefined,
      tzOffsetMinutes: -new Date().getTimezoneOffset()
    })
      .then((r) => {
        if (!cancelled) setReport(r)
      })
      .catch(() => {
        if (!cancelled) {
          setReport(null)
          setError(true)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [token, activeLedgerId, scope, offset])

  const currency = 'CNY'

  return (
    <Card className="bc-panel overflow-hidden">
      <CardHeader>
        <div className="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{t('comparison.title')}</CardTitle>
          <div className="flex items-center gap-2">
            <ScopeToggle
              value={scope}
              onChange={(next) => {
                setScope(next)
                if (next === 'month') setMonthOffset(1)
              }}
              t={t}
            />
            {scope === 'month' ? (
              <OffsetToggle value={monthOffset} onChange={setMonthOffset} t={t} />
            ) : null}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="flex h-24 items-center justify-center text-xs text-muted-foreground">
            {t('comparison.loading')}
          </div>
        ) : error || !report ? (
          <div className="flex h-24 items-center justify-center text-xs text-muted-foreground">
            {t('comparison.error')}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-2 sm:grid-cols-3">
              <MetricRow label={t('comparison.metric.income')} metric={report.income} currency={currency} positiveWhenUp />
              <MetricRow
                label={t('comparison.metric.expense')}
                metric={report.expense}
                currency={currency}
                positiveWhenUp={false}
              />
              <MetricRow label={t('comparison.metric.balance')} metric={report.balance} currency={currency} positiveWhenUp />
            </div>
            <CategoryBreakdown report={report} currency={currency} t={t} />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ScopeToggle({
  value,
  onChange,
  t
}: {
  value: Scope
  onChange: (v: Scope) => void
  t: (key: string) => string
}) {
  const options: Array<{ key: Scope; label: string }> = [
    { key: 'month', label: t('comparison.scope.month') },
    { key: 'year', label: t('comparison.scope.year') }
  ]
  return (
    <div
      role="tablist"
      className="inline-flex rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs"
    >
      {options.map((opt) => {
        const active = opt.key === value
        return (
          <button
            key={opt.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.key)}
            className={`rounded px-2.5 py-1 transition-colors ${
              active
                ? 'bg-background font-semibold text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function OffsetToggle({
  value,
  onChange,
  t
}: {
  value: 1 | 12
  onChange: (v: 1 | 12) => void
  t: (key: string) => string
}) {
  const options: Array<{ key: 1 | 12; label: string }> = [
    { key: 1, label: t('comparison.offset.lastMonth') },
    { key: 12, label: t('comparison.offset.sameMonthLastYear') }
  ]
  return (
    <div
      role="tablist"
      className="inline-flex rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs"
    >
      {options.map((opt) => {
        const active = opt.key === value
        return (
          <button
            key={opt.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.key)}
            className={`rounded px-2.5 py-1 transition-colors ${
              active
                ? 'bg-background font-semibold text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

/** 单个指标行(收入/支出/结余):目前值 + 上期值 + diff 箭头 + 百分比。
 *  `positiveWhenUp`:收入/结余涨=好(绿),支出涨=不好(红)——跟 HomeMonthMetrics
 *  的既有 income/expense 语义色配色一致。 */
function MetricRow({
  label,
  metric,
  currency,
  positiveWhenUp
}: {
  label: string
  metric: ComparisonReportMetric
  currency: string
  positiveWhenUp: boolean
}) {
  const isFlat = Math.abs(metric.diff) < 0.01
  const trendUp = metric.diff > 0
  const trendGood = isFlat ? null : positiveWhenUp ? trendUp : !trendUp

  return (
    <div className="rounded-lg border border-border/40 bg-muted/20 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <Amount value={metric.current} currency={currency} showCurrency bold size="lg" className="mt-0.5 block" />
      <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
        <span>
          <Amount value={metric.previous} currency={currency} size="xs" tone="muted" className="inline" />
        </span>
        {isFlat ? (
          <span className="inline-flex items-center gap-0.5 text-muted-foreground">
            <Minus className="h-3 w-3" />
          </span>
        ) : (
          <span
            className={`inline-flex items-center gap-0.5 font-semibold tabular-nums ${
              trendGood ? 'text-income' : 'text-expense'
            }`}
          >
            {trendUp ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
            {metric.diff_pct === null ? '—' : `${metric.diff_pct >= 0 ? '+' : ''}${metric.diff_pct.toFixed(1)}%`}
          </span>
        )}
      </div>
    </div>
  )
}

function CategoryBreakdown({
  report,
  currency,
  t
}: {
  report: ComparisonReport
  currency: string
  t: (key: string) => string
}) {
  const rows = report.category_breakdown.slice(0, 8)
  if (rows.length === 0) {
    return <div className="text-xs text-muted-foreground">{t('comparison.categoryBreakdown.empty')}</div>
  }
  return (
    <div className="space-y-1.5">
      <div className="text-[11px] font-semibold text-muted-foreground">{t('comparison.categoryBreakdown.title')}</div>
      <ul className="space-y-1">
        {rows.map((row, idx) => (
          <li
            key={`${row.category_id || 'uncategorized'}-${row.category_kind}-${idx}`}
            className="flex items-center justify-between gap-2 rounded-md border border-border/30 bg-background/40 px-2.5 py-1.5 text-xs"
          >
            <span className="min-w-0 truncate font-medium">{row.category_name}</span>
            <span className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground">
              <Amount value={row.current} currency={currency} size="xs" className="inline" />
              <span className="text-muted-foreground/60">/</span>
              <Amount value={row.previous} currency={currency} size="xs" tone="muted" className="inline" />
              <Amount
                value={row.diff}
                currency={currency}
                sign="always"
                size="xs"
                tone={
                  row.diff === 0
                    ? 'muted'
                    : row.category_kind === 'expense'
                      ? row.diff > 0
                        ? 'negative'
                        : 'positive'
                      : row.diff > 0
                        ? 'positive'
                        : 'negative'
                }
                bold
                className="inline"
              />
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
