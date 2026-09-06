import { useEffect, useState } from 'react'

import type { ReadProject, ReadProjectBreakdown, ReadProjectBreakdownCategory, ReadTransaction, WorkspaceCategory } from '@beecount/api-client'
import { fetchReadProjectBreakdown, fetchReadTransactions } from '@beecount/api-client'
import { Dialog, DialogContent, DialogHeader, DialogTitle, useT, type TranslateParams } from '@beecount/ui'
import { Amount, CategoryIcon, TransactionList } from '@beecount/web-features'
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'

/** `2026-08-05T00:00:00Z` -> `2026/08/05`,對齊 Moze 參考截圖的日期顯示格式。 */
function formatDateSlash(iso: string): string {
  return iso.slice(0, 10).replace(/-/g, '/')
}

/** period_end 是後端算窗口用的「隔天零點」(exclusive),顯示給使用者看要減一天
 *  換成「當期最後一天」(inclusive),比照 Moze 截圖的 `2026/09/05 – 2026/10/04`。 */
function formatPeriodEndInclusive(iso: string): string {
  const d = new Date(iso)
  d.setUTCDate(d.getUTCDate() - 1)
  return formatDateSlash(d.toISOString())
}

/** monthly/yearly 期間切換清單的顯示文字。用瀏覽器本地時間算「現在」,跟後端
 *  `_project_period_range` 同款「年*12+月 往回推算」算法——只影響清單文字,
 *  實際資料一律靠 `period_offset` 打後端拿,client/server 時鐘些微誤差無妨。 */
function periodOffsetLabel(periodType: 'monthly' | 'yearly', offset: number): string {
  const now = new Date()
  if (periodType === 'yearly') return String(now.getFullYear() - offset)
  const totalMonthIndex = now.getFullYear() * 12 + now.getMonth() - offset
  const year = Math.floor(totalMonthIndex / 12)
  const month0 = ((totalMonthIndex % 12) + 12) % 12
  return `${year}/${String(month0 + 1).padStart(2, '0')}`
}

const PERIOD_LIST_LENGTH = 12

interface Props {
  project: ReadProject | null
  onClose: () => void
  categories: readonly WorkspaceCategory[]
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
}

/**
 * 專案詳情頁(docs/2026-09-06-project-category-budget-period-switch-design.md
 * §4,比照 Moze):期間切換 + 出帳/入帳/總計統計條 + 分類花費拆解(已分配/
 * 未分配/未設定三組)。結構比照 `AccountDetailDialog` 的信用卡帳單週期選擇器
 * 樣式,`token`/`activeLedgerId` 同款從 context 拿,不走 props。
 */
export function ProjectDetailDialog({ project, onClose, categories, currency, iconPreviewUrlByFileId }: Props) {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()

  const [periodOffset, setPeriodOffset] = useState(0)
  const [breakdown, setBreakdown] = useState<ReadProjectBreakdown | null>(null)
  const [loading, setLoading] = useState(false)
  const [periodPickerOpen, setPeriodPickerOpen] = useState(false)
  const [unsetExpanded, setUnsetExpanded] = useState(false)
  const [expandedCategoryId, setExpandedCategoryId] = useState<string | null>(null)
  const [txByCategory, setTxByCategory] = useState<Record<string, ReadTransaction[] | undefined>>({})

  useEffect(() => {
    setPeriodOffset(0)
    setUnsetExpanded(false)
    setExpandedCategoryId(null)
    setTxByCategory({})
  }, [project?.id])

  useEffect(() => {
    if (!project || !activeLedgerId) {
      setBreakdown(null)
      return
    }
    let cancelled = false
    setLoading(true)
    fetchReadProjectBreakdown(token, activeLedgerId, project.id, periodOffset)
      .then((res) => {
        if (!cancelled) setBreakdown(res)
      })
      .catch(() => {
        if (!cancelled) setBreakdown(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [project, activeLedgerId, token, periodOffset])

  const toggleCategory = (categoryId: string) => {
    const next = expandedCategoryId === categoryId ? null : categoryId
    setExpandedCategoryId(next)
    if (next && txByCategory[categoryId] === undefined && activeLedgerId && breakdown) {
      void fetchReadTransactions(token, activeLedgerId, {
        projectId: breakdown.project_id,
        categoryId,
        startAt: breakdown.period_start,
        endAt: breakdown.period_end,
        limit: 200,
      })
        .then((rows) => setTxByCategory((prev) => ({ ...prev, [categoryId]: rows })))
        .catch(() => setTxByCategory((prev) => ({ ...prev, [categoryId]: [] })))
    }
  }

  const open = Boolean(project)
  const periodType = breakdown?.period_type

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="flex flex-row items-center justify-between gap-3 border-b border-border/60 px-6 py-4">
          <DialogTitle className="min-w-0 flex-1 truncate">{project?.name || ''}</DialogTitle>
          {breakdown && periodType && periodType !== 'fixed' ? (
            <div className="relative inline-flex shrink-0 items-center gap-0.5 rounded-md border border-border/60 bg-muted/30 p-0.5 text-xs">
              <button
                type="button"
                disabled={!breakdown.period_has_older}
                aria-label={t('projects.detail.period.prev')}
                onClick={() => setPeriodOffset((v) => v + 1)}
                className="rounded p-1 text-muted-foreground hover:bg-background disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setPeriodPickerOpen((v) => !v)}
                className="whitespace-nowrap px-1 font-mono font-medium tabular-nums text-foreground hover:underline"
              >
                {formatDateSlash(breakdown.period_start)} – {formatPeriodEndInclusive(breakdown.period_end)}
              </button>
              <button
                type="button"
                disabled={!breakdown.period_has_newer}
                aria-label={t('projects.detail.period.next')}
                onClick={() => setPeriodOffset((v) => v - 1)}
                className="rounded p-1 text-muted-foreground hover:bg-background disabled:cursor-not-allowed disabled:opacity-30"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
              {periodPickerOpen ? (
                <div className="absolute right-0 top-full z-20 mt-1 max-h-64 w-32 overflow-y-auto rounded-md border border-border/60 bg-popover p-1 shadow-md">
                  {Array.from({ length: PERIOD_LIST_LENGTH }, (_, i) => i).map((offset) => (
                    <button
                      key={offset}
                      type="button"
                      onClick={() => {
                        setPeriodOffset(offset)
                        setPeriodPickerOpen(false)
                      }}
                      className={[
                        'block w-full rounded px-2 py-1 text-left text-xs tabular-nums',
                        offset === periodOffset ? 'bg-primary/10 font-medium text-primary' : 'hover:bg-accent/40',
                      ].join(' ')}
                    >
                      {periodOffsetLabel(periodType as 'monthly' | 'yearly', offset)}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : breakdown && periodType === 'fixed' ? (
            <span className="shrink-0 whitespace-nowrap px-1 font-mono text-xs tabular-nums text-muted-foreground">
              {formatDateSlash(breakdown.period_start)} – {formatPeriodEndInclusive(breakdown.period_end)}
            </span>
          ) : null}
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {loading && !breakdown ? (
            <p className="py-8 text-center text-sm text-muted-foreground">{t('common.loading')}</p>
          ) : !breakdown ? (
            <p className="py-8 text-center text-sm text-muted-foreground">{t('table.empty')}</p>
          ) : (
            <>
              <StatsBar breakdown={breakdown} currency={currency} t={t} />
              <BudgetCard project={project} breakdown={breakdown} currency={currency} t={t} />
              <CategoryGroup
                title={t('projects.detail.categories.allocated')}
                items={breakdown.allocated_categories}
                categories={categories}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                expandedCategoryId={expandedCategoryId}
                onToggle={toggleCategory}
                txByCategory={txByCategory}
                t={t}
                showProgress
              />
              <CategoryGroup
                title={t('projects.detail.categories.unallocated')}
                items={breakdown.unallocated_categories}
                categories={categories}
                currency={currency}
                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                expandedCategoryId={expandedCategoryId}
                onToggle={toggleCategory}
                txByCategory={txByCategory}
                t={t}
              />
              {breakdown.unset_categories.length > 0 ? (
                <div className="mt-4">
                  <button
                    type="button"
                    onClick={() => setUnsetExpanded((v) => !v)}
                    className="flex w-full items-center justify-between rounded-md px-1 py-1.5 text-left text-xs font-medium text-muted-foreground hover:bg-accent/30"
                  >
                    <span className="flex items-center gap-1.5">
                      {t('projects.detail.categories.unset')}
                      <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px]">
                        {breakdown.unset_categories.length}
                      </span>
                    </span>
                    {unsetExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                  </button>
                  {unsetExpanded ? (
                    <div className="mt-1 space-y-1.5">
                      {breakdown.unset_categories.map((cat) => {
                        const c = categories.find((x) => x.id === cat.category_id)
                        const expanded = expandedCategoryId === cat.category_id
                        const rows = txByCategory[cat.category_id]
                        return (
                          <div key={cat.category_id} className="rounded-md border border-border/40 bg-card">
                            <button
                              type="button"
                              onClick={() => toggleCategory(cat.category_id)}
                              className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
                            >
                              <CategoryIcon
                                icon={c?.icon}
                                iconType={c?.icon_type || 'material'}
                                iconCloudFileId={c?.icon_cloud_file_id}
                                iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                                size={14}
                              />
                              <span className="min-w-0 flex-1 truncate text-xs">{c?.name || cat.category_id}</span>
                              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                                {t('overview.summary.txCount', { count: cat.count })}
                              </span>
                              <span className="shrink-0">
                                <Amount value={cat.spent} currency={currency} size="sm" bold tone="default" />
                              </span>
                            </button>
                            {expanded ? (
                              <div className="border-t border-border/40">
                                {rows === undefined ? (
                                  <p className="px-3 py-2 text-center text-xs text-muted-foreground">{t('common.loading')}</p>
                                ) : (
                                  <TransactionList
                                    items={rows}
                                    categories={categories as WorkspaceCategory[]}
                                    iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                                    variant="compact"
                                    canManage={false}
                                    emptyTitle={t('projects.detail.transactions.empty')}
                                  />
                                )}
                              </div>
                            ) : null}
                          </div>
                        )
                      })}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

function StatsBar({
  breakdown,
  currency,
  t,
}: {
  breakdown: ReadProjectBreakdown
  currency: string
  t: (key: string, params?: TranslateParams) => string
}) {
  const netTotal = breakdown.income_total - breakdown.expense_total
  const netCount = breakdown.income_count + breakdown.expense_count
  const maxAmount = Math.max(breakdown.expense_total, breakdown.income_total, Math.abs(netTotal), 1)

  const rows: Array<{ key: string; label: string; count: number; amount: number; barColor: string }> = [
    { key: 'expense', label: t('projects.detail.stats.expense'), count: breakdown.expense_count, amount: breakdown.expense_total, barColor: 'bg-red-500' },
    { key: 'income', label: t('projects.detail.stats.income'), count: breakdown.income_count, amount: breakdown.income_total, barColor: 'bg-emerald-500' },
    { key: 'net', label: t('projects.detail.stats.net'), count: netCount, amount: netTotal, barColor: 'bg-blue-500' },
  ]

  return (
    <div className="space-y-2 rounded-lg border border-border/50 bg-muted/10 p-3">
      {rows.map((row) => (
        <div key={row.key} className="flex items-center gap-2">
          <span className="w-14 shrink-0 text-xs text-muted-foreground">{row.label}</span>
          <span className="w-6 shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-center text-[10px] text-muted-foreground">
            {row.count}
          </span>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className={`h-full ${row.barColor}`}
              style={{ width: `${Math.max((Math.abs(row.amount) / maxAmount) * 100, row.amount !== 0 ? 2 : 0)}%` }}
            />
          </div>
          <span className="w-20 shrink-0 text-right">
            <Amount value={row.amount} currency={currency} size="sm" bold tone="default" />
          </span>
        </div>
      ))}
    </div>
  )
}

function BudgetCard({
  project,
  breakdown,
  currency,
  t,
}: {
  project: ReadProject | null
  breakdown: ReadProjectBreakdown
  currency: string
  t: (key: string, params?: TranslateParams) => string
}) {
  if (breakdown.effective_budget == null) return null
  const ratio = breakdown.effective_budget > 0 ? Math.min(breakdown.spent / breakdown.effective_budget, 1) : 0
  const barColor =
    breakdown.status === 'over' ? 'bg-red-500' : breakdown.status === 'warning' ? 'bg-orange-500' : 'bg-primary/70'
  const overAllocated = breakdown.unallocated_amount != null && breakdown.unallocated_amount < 0

  const dailyBudgetText = (() => {
    // 「今日可用」只在當期(period_offset===0)才有意義,往回翻期數看歷史時
    // 不該顯示「今日」概念。
    if (!project?.daily_budget_enabled || project.period_type === 'fixed' || breakdown.period_offset !== 0) {
      return null
    }
    const start = new Date(breakdown.period_start)
    const end = new Date(breakdown.period_end)
    const totalDays = Math.max(Math.round((end.getTime() - start.getTime()) / 86400000), 1)
    if (project.daily_budget_mode === 'fixed') {
      return breakdown.effective_budget / totalDays
    }
    const today = new Date()
    today.setUTCHours(0, 0, 0, 0)
    const remainingDays = Math.max(Math.ceil((end.getTime() - today.getTime()) / 86400000), 1)
    return (breakdown.effective_budget - breakdown.spent) / remainingDays
  })()

  return (
    <div className="mt-3 rounded-lg border border-border/50 bg-muted/10 p-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{t('projects.label.budget')}</span>
        <span>
          <Amount value={breakdown.spent} currency={currency} size="sm" bold tone="default" />
          {' / '}
          <Amount value={breakdown.effective_budget} currency={currency} size="sm" tone="muted" />
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
        <div className={`h-full transition-all ${barColor}`} style={{ width: `${ratio * 100}%` }} />
      </div>
      <div className="mt-1.5 flex items-center justify-between text-[11px]">
        <span className={overAllocated ? 'font-medium text-red-600' : 'text-muted-foreground'}>
          {overAllocated
            ? t('projects.categoryBudgets.label.overAllocated', {
                amount: Math.abs(breakdown.unallocated_amount as number).toFixed(2),
              })
            : breakdown.unallocated_amount != null
              ? t('projects.categoryBudgets.label.allocated', {
                  allocated: breakdown.allocated_total.toFixed(2),
                  total: breakdown.effective_budget.toFixed(2),
                })
              : ''}
        </span>
        {dailyBudgetText != null ? (
          <span className="text-muted-foreground">
            {t('projects.detail.budget.dailyBudget')} <Amount value={dailyBudgetText} currency={currency} size="sm" tone="muted" />
          </span>
        ) : null}
      </div>
    </div>
  )
}

function CategoryGroup({
  title,
  items,
  categories,
  currency,
  iconPreviewUrlByFileId,
  expandedCategoryId,
  onToggle,
  txByCategory,
  t,
  showProgress = false,
}: {
  title: string
  items: ReadProjectBreakdownCategory[]
  categories: readonly WorkspaceCategory[]
  currency: string
  iconPreviewUrlByFileId?: Record<string, string>
  expandedCategoryId: string | null
  onToggle: (categoryId: string) => void
  txByCategory: Record<string, ReadTransaction[] | undefined>
  t: (key: string, params?: TranslateParams) => string
  showProgress?: boolean
}) {
  if (items.length === 0) return null
  return (
    <div className="mt-4">
      <p className="px-1 text-xs font-medium text-muted-foreground">{title}</p>
      <div className="mt-1.5 space-y-1.5">
        {items.map((cat) => {
          const c = categories.find((x) => x.id === cat.category_id)
          const expanded = expandedCategoryId === cat.category_id
          const rows = txByCategory[cat.category_id]
          const progressRatio =
            showProgress && cat.budget_target != null && cat.budget_target > 0
              ? Math.min(cat.spent / cat.budget_target, 1)
              : null
          return (
            <div key={cat.category_id} className="rounded-md border border-border/40 bg-card">
              <button
                type="button"
                onClick={() => onToggle(cat.category_id)}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
              >
                <CategoryIcon
                  icon={c?.icon}
                  iconType={c?.icon_type || 'material'}
                  iconCloudFileId={c?.icon_cloud_file_id}
                  iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                  size={16}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs">{c?.name || cat.category_id}</span>
                  {progressRatio != null ? (
                    <span className="mt-1 block h-1 overflow-hidden rounded-full bg-muted">
                      <span
                        className={`block h-full ${cat.spent > (cat.budget_target || 0) ? 'bg-red-500' : 'bg-primary/70'}`}
                        style={{ width: `${progressRatio * 100}%` }}
                      />
                    </span>
                  ) : null}
                </span>
                <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {t('overview.summary.txCount', { count: cat.count })}
                </span>
                {cat.budget_target != null ? (
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    / <Amount value={cat.budget_target} currency={currency} size="xs" tone="muted" />
                  </span>
                ) : null}
                <span className="shrink-0">
                  <Amount value={cat.spent} currency={currency} size="sm" bold tone="default" />
                </span>
              </button>
              {expanded ? (
                <div className="border-t border-border/40">
                  {rows === undefined ? (
                    <p className="px-3 py-2 text-center text-xs text-muted-foreground">{t('common.loading')}</p>
                  ) : (
                    <TransactionList
                      items={rows}
                      categories={categories as WorkspaceCategory[]}
                      iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                      variant="compact"
                      canManage={false}
                      emptyTitle={t('projects.detail.transactions.empty')}
                    />
                  )}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
