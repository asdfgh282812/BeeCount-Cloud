import { useEffect, useMemo, useState } from 'react'
import type { MouseEvent } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts'

import {
  COMPARISON_MATRIX_NONE_KEY,
  fetchComparisonMatrix,
  fetchComparisonMatrixCell,
  type ComparisonMatrix,
  type ComparisonMatrixCell,
  type ComparisonMatrixDimension
} from '@beecount/api-client'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  useT
} from '@beecount/ui'
import { Amount } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'

/**
 * 比較報表(對齊 doc.moze.app/analysis/comparison-report)。矩陣需要大螢幕,
 * 所以只在 Web 提供;App 端對應的是「報表」分頁的統計報表。
 *
 * - 左側:月份(依帳本月起始日切)。點月份選取,Shift+點另一個月份選範圍;
 *   底部「合計/平均」與分析圖表都只算選取的月份(沒選 = 全部)。
 * - 上方:比較項目。點欄位標題切換是否計入右側「總計」與月增率。
 * - 點格子:右側抽屜列出該月該項目的交易(`/comparison-matrix/cell`)。
 * - 數字口徑同 `/workspace/comparison`(後端共用 `_stat_legs`:本位幣、拆帳
 *   展開、退款沖銷、排除「不計入統計」)。
 */

const DIMENSIONS: ComparisonMatrixDimension[] = [
  'record_type',
  'expense_category',
  'income_category',
  'expense_subcategory',
  'income_subcategory',
  'project',
  'account_group'
]

const PALETTE = [
  '#5B8FF9', '#5AD8A6', '#F6BD16', '#E86452', '#6DC8EC', '#945FB9',
  '#FF9845', '#1E9493', '#FF99C3', '#269A99', '#BDD2FD', '#A0DC2C'
]

type SortMode = 'amount' | 'average' | 'name'
type AnalysisTab = 'share' | 'stacked' | 'trend'

function monthLabel(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function shiftMonth(label: string, delta: number): string {
  const [y, m] = label.split('-').map(Number)
  return monthLabel(new Date(y, m - 1 + delta, 1))
}

/** 今天所在的週期標籤(依帳本月起始日;沒到起始日算上個月)。 */
function currentPeriodLabel(monthStartDay: number): string {
  const now = new Date()
  const d = now.getDate() >= monthStartDay ? now : new Date(now.getFullYear(), now.getMonth() - 1, 1)
  return monthLabel(d)
}

function round2(v: number): number {
  return Math.round(v * 100) / 100
}

export function ComparisonReportPage() {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const monthStartDay = Math.min(28, Math.max(1, currentLedger?.month_start_day || 1))
  const nowLabel = currentPeriodLabel(monthStartDay)

  const [dimension, setDimension] = useState<ComparisonMatrixDimension>('expense_category')
  const [kind, setKind] = useState<'expense' | 'income'>('expense')
  const [start, setStart] = useState(() => shiftMonth(nowLabel, -11))
  const [end, setEnd] = useState(nowLabel)
  const [data, setData] = useState<ComparisonMatrix | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [excluded, setExcluded] = useState<Set<string>>(new Set())
  const [sortMode, setSortMode] = useState<SortMode>('amount')
  const [analysisTab, setAnalysisTab] = useState<AnalysisTab>('share')
  const [cumulative, setCumulative] = useState(false)

  const [cellTarget, setCellTarget] = useState<{ month: string; key: string; label: string } | null>(null)
  const [cell, setCell] = useState<ComparisonMatrixCell | null>(null)
  const [cellLoading, setCellLoading] = useState(false)

  const kindApplies = dimension === 'project' || dimension === 'account_group'
  const tz = -new Date().getTimezoneOffset()

  useEffect(() => {
    if (!token || !activeLedgerId) return
    let cancelled = false
    setLoading(true)
    setError(false)
    fetchComparisonMatrix(token, {
      dimension,
      kind: kindApplies ? kind : undefined,
      start,
      end,
      ledgerId: activeLedgerId,
      tzOffsetMinutes: tz
    })
      .then((r) => {
        if (cancelled) return
        setData(r)
        setSelected(new Set())
        setAnchor(null)
        setExcluded(new Set())
      })
      .catch(() => {
        if (!cancelled) {
          setData(null)
          setError(true)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // tz 在同一個 session 內不變,不放進依賴
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, dimension, kind, kindApplies, start, end])

  useEffect(() => {
    if (!token || !activeLedgerId || !cellTarget) return
    let cancelled = false
    setCellLoading(true)
    setCell(null)
    fetchComparisonMatrixCell(token, {
      dimension,
      kind: kindApplies ? kind : undefined,
      month: cellTarget.month,
      columnKey: cellTarget.key,
      ledgerId: activeLedgerId,
      tzOffsetMinutes: tz
    })
      .then((r) => {
        if (!cancelled) setCell(r)
      })
      .catch(() => {
        if (!cancelled) setCell(null)
      })
      .finally(() => {
        if (!cancelled) setCellLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cellTarget])

  const columnLabel = (key: string, label: string): string => {
    if (dimension === 'record_type') return t(`cmpx.col.${key}`)
    if (key === COMPARISON_MATRIX_NONE_KEY) {
      return dimension === 'account_group' ? t('cmpx.ungrouped') : t('cmpx.none')
    }
    return label || t('cmpx.none')
  }

  const activeRows = useMemo(() => {
    if (!data) return []
    return selected.size === 0 ? data.rows : data.rows.filter((r) => selected.has(r.month))
  }, [data, selected])

  const colStats = useMemo(() => {
    const stats: Record<string, { total: number; average: number }> = {}
    if (!data) return stats
    const n = activeRows.length || 1
    for (const c of data.columns) {
      const total = activeRows.reduce((s, r) => s + (r.values[c.key] || 0), 0)
      stats[c.key] = { total: round2(total), average: round2(total / n) }
    }
    return stats
  }, [data, activeRows])

  const columns = useMemo(() => {
    if (!data) return []
    if (dimension === 'record_type') return data.columns
    const cols = [...data.columns]
    cols.sort((a, b) => {
      const noneA = a.key === COMPARISON_MATRIX_NONE_KEY ? 1 : 0
      const noneB = b.key === COMPARISON_MATRIX_NONE_KEY ? 1 : 0
      if (noneA !== noneB) return noneA - noneB
      if (sortMode === 'name') return columnLabel(a.key, a.label).localeCompare(columnLabel(b.key, b.label))
      const field = sortMode === 'average' ? 'average' : 'total'
      return Math.abs(colStats[b.key]?.[field] || 0) - Math.abs(colStats[a.key]?.[field] || 0)
    })
    return cols
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, dimension, sortMode, colStats])

  /** 右側總計:排除的欄位不計入;記錄類型 = 收入 - 支出。 */
  const rowTotal = (values: Record<string, number>): number => {
    if (dimension === 'record_type') {
      const inc = excluded.has('income') ? 0 : values.income || 0
      const exp = excluded.has('expense') ? 0 : values.expense || 0
      return round2(inc - exp)
    }
    return round2(
      Object.entries(values).reduce((s, [k, v]) => (excluded.has(k) ? s : s + v), 0)
    )
  }

  const rowsWithTotals = useMemo(() => {
    if (!data) return []
    let prev: number | null = null
    return data.rows.map((r) => {
      const total = rowTotal(r.values)
      const mom = prev ? round2(((total - prev) / Math.abs(prev)) * 100) : null
      prev = total
      return { ...r, total, mom }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, excluded, dimension])

  const selectionTotal = round2(
    rowsWithTotals
      .filter((r) => selected.size === 0 || selected.has(r.month))
      .reduce((s, r) => s + r.total, 0)
  )

  const onMonthClick = (month: string, e: MouseEvent) => {
    if (!data) return
    const months = data.rows.map((r) => r.month)
    if (e.shiftKey && anchor) {
      const a = months.indexOf(anchor)
      const b = months.indexOf(month)
      const [lo, hi] = a < b ? [a, b] : [b, a]
      setSelected(new Set(months.slice(lo, hi + 1)))
      return
    }
    setAnchor(month)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(month)) next.delete(month)
      else next.add(month)
      return next
    })
  }

  const toggleColumn = (key: string) =>
    setExcluded((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const applyPreset = (preset: 'last6' | 'last12' | 'thisYear' | 'lastYear') => {
    const year = Number(nowLabel.slice(0, 4))
    if (preset === 'last6') {
      setStart(shiftMonth(nowLabel, -5))
      setEnd(nowLabel)
    } else if (preset === 'last12') {
      setStart(shiftMonth(nowLabel, -11))
      setEnd(nowLabel)
    } else if (preset === 'thisYear') {
      setStart(`${year}-01`)
      setEnd(nowLabel)
    } else {
      setStart(`${year - 1}-01`)
      setEnd(`${year - 1}-12`)
    }
  }

  // 分析圖表只看「可比較」的欄位:記錄類型的結餘不畫進佔比/堆疊。
  const chartColumns = columns.filter(
    (c) => !excluded.has(c.key) && !(dimension === 'record_type' && c.key === 'balance')
  )
  const topChartColumns = chartColumns.slice(0, 8)

  const shareData = chartColumns
    .map((c) => ({ name: columnLabel(c.key, c.label), value: colStats[c.key]?.total || 0 }))
    .filter((d) => d.value > 0)

  const periodData = activeRows.map((r) => {
    const point: Record<string, number | string> = { month: r.month }
    for (const c of topChartColumns) point[c.key] = r.values[c.key] || 0
    return point
  })
  const periodAverage = periodData.length
    ? round2(
        activeRows.reduce((s, r) => s + rowTotal(r.values), 0) / periodData.length
      )
    : 0
  const trendData = (() => {
    if (!cumulative) return periodData
    const running: Record<string, number> = {}
    return periodData.map((p) => {
      const out: Record<string, number | string> = { month: p.month }
      for (const c of topChartColumns) {
        running[c.key] = (running[c.key] || 0) + Number(p[c.key] || 0)
        out[c.key] = round2(running[c.key])
      }
      return out
    })
  })()

  if (!activeLedgerId) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
        {t('cmpx.noLedger')}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardHeader className="space-y-3">
          <div>
            <CardTitle className="text-lg">{t('cmpx.title')}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{t('cmpx.subtitle')}</p>
          </div>
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t('cmpx.dimension')}</span>
              <Select value={dimension} onValueChange={(v) => setDimension(v as ComparisonMatrixDimension)}>
                <SelectTrigger className="w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DIMENSIONS.map((d) => (
                    <SelectItem key={d} value={d}>
                      {t(`cmpx.dimension.${d}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {kindApplies ? (
              <Segmented
                value={kind}
                onChange={(v) => setKind(v as 'expense' | 'income')}
                options={[
                  { key: 'expense', label: t('cmpx.kind.expense') },
                  { key: 'income', label: t('cmpx.kind.income') }
                ]}
              />
            ) : null}
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t('cmpx.range.from')}</span>
              <input
                type="month"
                value={start}
                max={end}
                onChange={(e) => e.target.value && setStart(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t('cmpx.range.to')}</span>
              <input
                type="month"
                value={end}
                min={start}
                onChange={(e) => e.target.value && setEnd(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              />
            </label>
            <div className="flex flex-wrap gap-1">
              {(['last6', 'last12', 'thisYear', 'lastYear'] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className="h-9 rounded-md border border-border/60 px-2.5 text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                >
                  {t(`cmpx.range.${p}`)}
                </button>
              ))}
            </div>
            {dimension !== 'record_type' ? (
              <label className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">{t('cmpx.sort')}</span>
                <Select value={sortMode} onValueChange={(v) => setSortMode(v as SortMode)}>
                  <SelectTrigger className="w-32">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="amount">{t('cmpx.sort.amount')}</SelectItem>
                    <SelectItem value="average">{t('cmpx.sort.average')}</SelectItem>
                    <SelectItem value="name">{t('cmpx.sort.name')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {loading && !data ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
              {t('cmpx.loading')}
            </div>
          ) : error || !data ? (
            <div className="flex h-40 items-center justify-center text-sm text-muted-foreground">
              {t('cmpx.error')}
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{t('cmpx.columnToggleHint')}</span>
                {selected.size > 0 ? (
                  <span className="flex items-center gap-2">
                    <span className="font-medium text-foreground">
                      {t('cmpx.selection', { count: selected.size })}
                    </span>
                    <button
                      type="button"
                      className="underline hover:text-foreground"
                      onClick={() => {
                        setSelected(new Set())
                        setAnchor(null)
                      }}
                    >
                      {t('cmpx.selection.clear')}
                    </button>
                  </span>
                ) : null}
              </div>
              <div className={`relative max-h-[65vh] overflow-auto rounded-md border border-border/60 ${loading ? 'opacity-60' : ''}`}>
                <table className="min-w-full border-separate border-spacing-0 text-sm">
                  <thead>
                    <tr>
                      <th className="sticky left-0 top-0 z-30 min-w-[88px] border-b border-r border-border/60 bg-muted px-3 py-2 text-left text-xs font-medium text-muted-foreground">
                        {t('cmpx.month')}
                      </th>
                      {columns.map((c) => {
                        const off = excluded.has(c.key)
                        return (
                          <th
                            key={c.key}
                            onClick={() => toggleColumn(c.key)}
                            title={c.parent_label || undefined}
                            className={`sticky top-0 z-20 min-w-[96px] cursor-pointer select-none border-b border-border/60 bg-muted px-3 py-2 text-right text-xs font-medium ${
                              off ? 'text-muted-foreground/50 line-through' : 'text-foreground'
                            }`}
                          >
                            {c.parent_label ? (
                              <div className="text-[10px] font-normal text-muted-foreground">{c.parent_label}</div>
                            ) : null}
                            {columnLabel(c.key, c.label)}
                          </th>
                        )
                      })}
                      <th className="sticky right-[72px] top-0 z-20 min-w-[104px] border-b border-l border-border/60 bg-muted px-3 py-2 text-right text-xs font-semibold">
                        {t('cmpx.rowTotal')}
                      </th>
                      <th className="sticky right-0 top-0 z-20 min-w-[72px] border-b border-border/60 bg-muted px-3 py-2 text-right text-xs font-medium text-muted-foreground">
                        {t('cmpx.mom')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rowsWithTotals.map((r) => {
                      const isSel = selected.has(r.month)
                      const rowBg = isSel ? 'bg-primary/10' : 'bg-background'
                      return (
                        <tr key={r.month} className="group">
                          <td
                            onClick={(e) => onMonthClick(r.month, e)}
                            className={`sticky left-0 z-10 cursor-pointer select-none border-b border-r border-border/40 px-3 py-1.5 font-mono text-xs ${rowBg} ${
                              isSel ? 'font-semibold text-primary' : ''
                            } group-hover:bg-muted/60`}
                          >
                            {r.month}
                          </td>
                          {columns.map((c) => {
                            const v = r.values[c.key] || 0
                            return (
                              <td
                                key={c.key}
                                onClick={() =>
                                  v !== 0 && setCellTarget({ month: r.month, key: c.key, label: columnLabel(c.key, c.label) })
                                }
                                className={`border-b border-border/40 px-3 py-1.5 text-right ${rowBg} group-hover:bg-muted/40 ${
                                  v !== 0 ? 'cursor-pointer hover:!bg-primary/15' : 'text-muted-foreground/40'
                                } ${excluded.has(c.key) ? 'opacity-40' : ''}`}
                              >
                                {v === 0 ? '—' : <Amount value={v} currency={currency} compact={false} size="sm" />}
                              </td>
                            )
                          })}
                          <td className={`sticky right-[72px] z-10 border-b border-l border-border/40 px-3 py-1.5 text-right font-semibold ${rowBg} group-hover:bg-muted/60`}>
                            <Amount value={r.total} currency={currency} compact={false} size="sm" bold />
                          </td>
                          <td className={`sticky right-0 z-10 border-b border-border/40 px-3 py-1.5 text-right text-xs ${rowBg} group-hover:bg-muted/60`}>
                            <MomBadge value={r.mom} goodWhenUp={dimension === 'record_type' || kind === 'income' || dimension.startsWith('income')} />
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                  <tfoot>
                    {(['subtotal', 'average'] as const).map((which) => (
                      <tr key={which}>
                        <td className="sticky bottom-0 left-0 z-30 border-r border-t border-border/60 bg-muted px-3 py-2 text-xs font-medium">
                          {t(which === 'subtotal' ? 'cmpx.subtotal' : 'cmpx.average')}
                        </td>
                        {columns.map((c) => (
                          <td
                            key={c.key}
                            className={`sticky bottom-0 z-20 border-t border-border/60 bg-muted px-3 py-2 text-right ${
                              excluded.has(c.key) ? 'opacity-40' : ''
                            }`}
                          >
                            <Amount
                              value={which === 'subtotal' ? colStats[c.key]?.total || 0 : colStats[c.key]?.average || 0}
                              currency={currency}
                              compact={false}
                              size="sm"
                              bold={which === 'subtotal'}
                            />
                          </td>
                        ))}
                        <td className="sticky bottom-0 right-[72px] z-30 border-l border-t border-border/60 bg-muted px-3 py-2 text-right">
                          <Amount
                            value={which === 'subtotal' ? selectionTotal : round2(selectionTotal / (activeRows.length || 1))}
                            currency={currency}
                            compact={false}
                            size="sm"
                            bold
                          />
                        </td>
                        <td className="sticky bottom-0 right-0 z-30 border-t border-border/60 bg-muted px-3 py-2" />
                      </tr>
                    ))}
                  </tfoot>
                </table>
                {data.columns.length === 0 ? (
                  <div className="py-6 text-center text-sm text-muted-foreground">{t('cmpx.empty')}</div>
                ) : null}
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {data && chartColumns.length > 0 ? (
        <Card className="bc-panel">
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">
              {t('cmpx.analysis')}
              {selected.size > 0 ? (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {t('cmpx.selection', { count: selected.size })}
                </span>
              ) : null}
            </CardTitle>
            <div className="flex items-center gap-2">
              <Segmented
                value={analysisTab}
                onChange={(v) => setAnalysisTab(v as AnalysisTab)}
                options={[
                  { key: 'share', label: t('cmpx.analysis.share') },
                  { key: 'stacked', label: t('cmpx.analysis.stacked') },
                  { key: 'trend', label: t('cmpx.analysis.trend') }
                ]}
              />
              {analysisTab === 'trend' ? (
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  <input type="checkbox" checked={cumulative} onChange={(e) => setCumulative(e.target.checked)} />
                  {t('cmpx.analysis.cumulative')}
                </label>
              ) : null}
            </div>
          </CardHeader>
          <CardContent>
            <div className="h-80 w-full">
              <ResponsiveContainer width="100%" height="100%">
                {analysisTab === 'share' ? (
                  <PieChart>
                    <Pie data={shareData} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="75%" paddingAngle={1}>
                      {shareData.map((_, i) => (
                        <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(v) => Number(v).toLocaleString()} />
                    <Legend />
                  </PieChart>
                ) : analysisTab === 'stacked' ? (
                  <BarChart data={periodData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                    <XAxis dataKey="month" stroke="hsl(var(--muted-foreground))" fontSize={11} />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} width={70} />
                    <Tooltip formatter={(v) => Number(v).toLocaleString()} />
                    <Legend />
                    {topChartColumns.map((c, i) => (
                      <Bar key={c.key} dataKey={c.key} name={columnLabel(c.key, c.label)} stackId="a" fill={PALETTE[i % PALETTE.length]} />
                    ))}
                    <ReferenceLine
                      y={periodAverage}
                      stroke="hsl(var(--primary))"
                      strokeDasharray="4 4"
                      label={{ value: t('cmpx.analysis.averageLine'), position: 'right', fontSize: 11 }}
                    />
                  </BarChart>
                ) : (
                  <LineChart data={trendData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                    <XAxis dataKey="month" stroke="hsl(var(--muted-foreground))" fontSize={11} />
                    <YAxis stroke="hsl(var(--muted-foreground))" fontSize={11} width={70} />
                    <Tooltip formatter={(v) => Number(v).toLocaleString()} />
                    <Legend />
                    {topChartColumns.map((c, i) => (
                      <Line
                        key={c.key}
                        type="monotone"
                        dataKey={c.key}
                        name={columnLabel(c.key, c.label)}
                        stroke={PALETTE[i % PALETTE.length]}
                        strokeWidth={2}
                        dot={{ r: 2 }}
                      />
                    ))}
                  </LineChart>
                )}
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Sheet open={cellTarget !== null} onOpenChange={(open) => !open && setCellTarget(null)}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
          <SheetHeader>
            <SheetTitle>
              {cellTarget ? `${cellTarget.month} · ${cellTarget.label}` : ''}
            </SheetTitle>
          </SheetHeader>
          {cellLoading ? (
            <div className="py-10 text-center text-sm text-muted-foreground">{t('cmpx.loading')}</div>
          ) : !cell || cell.items.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">{t('cmpx.cell.empty')}</div>
          ) : (
            <div className="mt-4 space-y-1">
              <div className="flex justify-end pb-2 text-sm">
                <Amount value={cell.total} currency={currency} compact={false} bold />
              </div>
              {cell.items.map((it) => (
                <div
                  key={it.sync_id}
                  className="flex items-center justify-between gap-3 border-b border-border/40 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <div className="truncate">
                      {it.note || it.merchant || it.category_name || '—'}
                      {it.is_refund ? (
                        <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          {t('cmpx.cell.refund')}
                        </span>
                      ) : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {new Date(it.happened_at).toLocaleDateString()}
                      {it.category_name ? ` · ${it.category_name}` : ''}
                      {it.account_name ? ` · ${it.account_name}` : ''}
                    </div>
                  </div>
                  <Amount value={it.amount} currency={currency} compact={false} size="sm" />
                </div>
              ))}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function Segmented({
  value,
  onChange,
  options
}: {
  value: string
  onChange: (v: string) => void
  options: Array<{ key: string; label: string }>
}) {
  return (
    <div role="tablist" className="inline-flex h-9 items-center rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs">
      {options.map((opt) => {
        const active = opt.key === value
        return (
          <button
            key={opt.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(opt.key)}
            className={`h-full rounded px-2.5 transition-colors ${
              active ? 'bg-background font-semibold text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function MomBadge({ value, goodWhenUp }: { value: number | null; goodWhenUp: boolean }) {
  if (value === null || !Number.isFinite(value)) return <span className="text-muted-foreground/50">—</span>
  if (value === 0) return <span className="text-muted-foreground">0%</span>
  const up = value > 0
  const good = up === goodWhenUp
  return (
    <span className={good ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}>
      {up ? '▲' : '▼'} {Math.abs(value).toFixed(1)}%
    </span>
  )
}
