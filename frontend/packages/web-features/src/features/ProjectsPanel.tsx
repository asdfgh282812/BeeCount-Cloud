import { useMemo, useState } from 'react'

import { BarChart3 } from 'lucide-react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  useT,
} from '@beecount/ui'

import type {
  ProjectCategoryBudgetMode,
  ReadProject,
  ReadProjectCategoryBudget,
  WorkspaceCategory,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { DatePicker } from '../components/DatePicker'
import type { DailyBudgetMode, ProjectCategoryBudgetForm, ProjectForm, ProjectPeriodType } from '../forms'
import { projectCategoryBudgetDefaults, projectDefaults } from '../forms'

type ProjectsPanelProps = {
  projects: readonly ReadProject[]
  currency: string
  form: ProjectForm
  onFormChange: (next: ProjectForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (project: ReadProject) => Promise<void> | void
  /** 账本 owner 才能新建/编辑/删除专案(server `_OWNER_ONLY_ROLES`)。 */
  canManage: boolean
  /** 全量分类(workspace),用来给分类子预算选一级分类 + 反查名称/图标。 */
  categories: readonly WorkspaceCategory[]
  /** projectId → 该专案下的分类子预算列表。未定义 = 尚未加载(懒加载,展开
   *  卡片时才 fetch,同 `InstallmentPlansPanel` 的 periodsByPlanId 模式)。 */
  categoryBudgetsByProjectId: Readonly<Record<string, ReadProjectCategoryBudget[] | undefined>>
  onLoadCategoryBudgets: (project: ReadProject) => void
  categoryBudgetForm: ProjectCategoryBudgetForm
  onCategoryBudgetFormChange: (next: ProjectCategoryBudgetForm) => void
  onSubmitCategoryBudget: (project: ReadProject) => Promise<boolean> | boolean
  onDeleteCategoryBudget: (project: ReadProject, budget: ReadProjectCategoryBudget) => Promise<void> | void
  iconPreviewUrlByFileId?: Record<string, string>
  /** 專案詳情頁(docs/2026-09-06-project-category-budget-period-switch-
   *  design.md §4):期間切換 + 出帳/入帳/總計統計 + 分類花費拆解,獨立 Dialog
   *  由呼叫端(`ProjectsPage`)渲染,這裡只負責觸發入口。 */
  onOpenDetail?: (project: ReadProject) => void
}

const PERIOD_TYPES: ProjectPeriodType[] = ['monthly', 'yearly', 'fixed']
const DAILY_BUDGET_MODES: DailyBudgetMode[] = ['proportional', 'fixed']
const CATEGORY_BUDGET_MODES: ProjectCategoryBudgetMode[] = ['fixed', 'percentage']

/**
 * 專案面板(Phase 13,docs/PH13_PROJECT_SD.md)—— 結構比照 `DebtsPanel`:
 * 列表卡片(icon/名稱/當期花費/預算進度/狀態指標)+ CRUD dialog。
 *
 * `spent`/`remaining`/`progress_pct`/`status` 由 server 從反查交易依
 * period_type 即時算出,這裡不做任何客戶端累加。刪除有交易掛著的專案時,
 * server 會自動軟刪除(`enabled=false`)而非物理刪除——列表仍會顯示該專案
 * 但帶「已停用」標記,編輯表單可以手動切回 `enabled` 重新啟用。
 */
export function ProjectsPanel({
  projects,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  canManage,
  categories,
  categoryBudgetsByProjectId,
  onLoadCategoryBudgets,
  categoryBudgetForm,
  onCategoryBudgetFormChange,
  onSubmitCategoryBudget,
  onDeleteCategoryBudget,
  iconPreviewUrlByFileId,
  onOpenDetail,
}: ProjectsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadProject | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [expandedProjectId, setExpandedProjectId] = useState<string | null>(null)

  const handleOpenCreate = () => {
    onFormChange(projectDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (project: ReadProject) => {
    onFormChange({
      editingId: project.id,
      name: project.name,
      icon: project.icon || '',
      budget_amount: project.budget_amount != null ? String(project.budget_amount) : '',
      period_type: project.period_type,
      period_start: project.period_start ? project.period_start.slice(0, 10) : '',
      period_end: project.period_end ? project.period_end.slice(0, 10) : '',
      carryover_enabled: project.carryover_enabled,
      visible_on_home: project.visible_on_home,
      enabled: project.enabled,
      income_included_in_budget: project.income_included_in_budget,
      daily_budget_enabled: project.daily_budget_enabled,
      daily_budget_mode: project.daily_budget_mode || 'proportional',
      reminder_threshold_percent:
        project.reminder_threshold_percent != null ? String(project.reminder_threshold_percent) : '',
    })
    setDialogOpen(true)
  }

  const toggleExpand = (project: ReadProject) => {
    if (expandedProjectId === project.id) {
      setExpandedProjectId(null)
      return
    }
    setExpandedProjectId(project.id)
    if (!categoryBudgetsByProjectId[project.id]) onLoadCategoryBudgets(project)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const canSubmit =
    Boolean(form.name.trim()) &&
    (form.period_type !== 'fixed' || (Boolean(form.period_start) && Boolean(form.period_end)))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t('projects.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('projects.button.create')}
        </Button>
      </div>

      {projects.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
            </svg>
          }
          title={t('projects.empty')}
          description={t('projects.emptyDesc')}
        />
      ) : (
        <div className="space-y-3">
          {projects.map((project) => (
            <ProjectCard
              key={project.id}
              project={project}
              currency={currency}
              canManage={canManage}
              onEdit={() => handleOpenEdit(project)}
              onDelete={() => setPendingDelete(project)}
              categories={categories}
              expanded={expandedProjectId === project.id}
              categoryBudgets={categoryBudgetsByProjectId[project.id]}
              onToggleExpand={() => toggleExpand(project)}
              categoryBudgetForm={categoryBudgetForm}
              onCategoryBudgetFormChange={onCategoryBudgetFormChange}
              onSubmitCategoryBudget={() => onSubmitCategoryBudget(project)}
              onDeleteCategoryBudget={(budget) => onDeleteCategoryBudget(project, budget)}
              iconPreviewUrlByFileId={iconPreviewUrlByFileId}
              onOpenDetail={onOpenDetail ? () => onOpenDetail(project) : undefined}
            />
          ))}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('projects.button.update') : t('projects.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-[1fr_5rem] gap-2">
              <div className="space-y-1">
                <Label>{t('projects.field.name')}</Label>
                <Input
                  value={form.name}
                  onChange={(e) => onFormChange({ ...form, name: e.target.value })}
                  placeholder={t('projects.placeholder.name')}
                />
              </div>
              <div className="space-y-1">
                <Label>{t('projects.field.icon')}</Label>
                <Input
                  value={form.icon}
                  maxLength={8}
                  onChange={(e) => onFormChange({ ...form, icon: e.target.value })}
                  placeholder="🏠"
                />
              </div>
            </div>

            <div className="space-y-1">
              <Label>{t('projects.field.budgetAmount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                placeholder={t('projects.placeholder.noBudget')}
                value={form.budget_amount}
                onChange={(e) => onFormChange({ ...form, budget_amount: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>{t('projects.field.periodType')}</Label>
              <div className="grid grid-cols-3 gap-2">
                {PERIOD_TYPES.map((pt) => (
                  <button
                    key={pt}
                    type="button"
                    onClick={() => onFormChange({ ...form, period_type: pt })}
                    className={[
                      'rounded-md border px-3 py-2 text-sm transition-colors',
                      form.period_type === pt
                        ? 'border-primary/60 bg-primary/10 text-primary'
                        : 'border-border/60 hover:bg-accent/40',
                    ].join(' ')}
                  >
                    {t(`projects.periodType.${pt}`)}
                  </button>
                ))}
              </div>
            </div>

            {form.period_type === 'fixed' ? (
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label>{t('projects.field.periodStart')}</Label>
                  <DatePicker
                    value={form.period_start}
                    onChange={(next) => onFormChange({ ...form, period_start: next })}
                    clearable
                  />
                </div>
                <div className="space-y-1">
                  <Label>{t('projects.field.periodEnd')}</Label>
                  <DatePicker
                    value={form.period_end}
                    onChange={(next) => onFormChange({ ...form, period_end: next })}
                    clearable
                  />
                </div>
              </div>
            ) : (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.carryover_enabled}
                  onChange={(e) => onFormChange({ ...form, carryover_enabled: e.target.checked })}
                />
                {t('projects.field.carryoverEnabled')}
              </label>
            )}

            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.visible_on_home}
                onChange={(e) => onFormChange({ ...form, visible_on_home: e.target.checked })}
              />
              {t('projects.field.visibleOnHome')}
            </label>

            <div className="space-y-2 rounded-lg border border-border/50 p-3">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.income_included_in_budget}
                  disabled={!form.budget_amount.trim()}
                  onChange={(e) => onFormChange({ ...form, income_included_in_budget: e.target.checked })}
                />
                {t('projects.field.incomeIncludedInBudget')}
              </label>

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.daily_budget_enabled}
                  disabled={form.period_type === 'fixed' || !form.budget_amount.trim()}
                  onChange={(e) => onFormChange({ ...form, daily_budget_enabled: e.target.checked })}
                />
                {t('projects.field.dailyBudgetEnabled')}
              </label>
              {form.daily_budget_enabled ? (
                <div className="grid grid-cols-2 gap-2 pl-6">
                  {DAILY_BUDGET_MODES.map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => onFormChange({ ...form, daily_budget_mode: mode })}
                      className={[
                        'rounded-md border px-3 py-1.5 text-xs transition-colors',
                        form.daily_budget_mode === mode
                          ? 'border-primary/60 bg-primary/10 text-primary'
                          : 'border-border/60 hover:bg-accent/40',
                      ].join(' ')}
                    >
                      {t(`projects.dailyBudgetMode.${mode}`)}
                    </button>
                  ))}
                </div>
              ) : null}

              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={Boolean(form.reminder_threshold_percent.trim())}
                  onChange={(e) =>
                    onFormChange({
                      ...form,
                      reminder_threshold_percent: e.target.checked ? '80' : '',
                    })
                  }
                />
                {t('projects.field.reminderEnabled')}
              </label>
              {form.reminder_threshold_percent.trim() ? (
                <div className="pl-6">
                  <Label>{t('projects.field.reminderThresholdPercent')}</Label>
                  <Input
                    type="number"
                    inputMode="numeric"
                    min="1"
                    max="200"
                    step="1"
                    value={form.reminder_threshold_percent}
                    onChange={(e) => onFormChange({ ...form, reminder_threshold_percent: e.target.value })}
                  />
                </div>
              ) : null}
            </div>

            {form.editingId ? (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => onFormChange({ ...form, enabled: e.target.checked })}
                />
                {t('projects.field.enabled')}
              </label>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId ? t('projects.button.update') : t('projects.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('projects.delete.title')}
        description={t('projects.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function ProjectCard({
  project,
  currency,
  canManage,
  onEdit,
  onDelete,
  categories,
  expanded,
  categoryBudgets,
  onToggleExpand,
  categoryBudgetForm,
  onCategoryBudgetFormChange,
  onSubmitCategoryBudget,
  onDeleteCategoryBudget,
  iconPreviewUrlByFileId,
  onOpenDetail,
}: {
  project: ReadProject
  currency: string
  canManage: boolean
  onEdit: () => void
  onDelete: () => void
  categories: readonly WorkspaceCategory[]
  expanded: boolean
  categoryBudgets: ReadProjectCategoryBudget[] | undefined
  onToggleExpand: () => void
  categoryBudgetForm: ProjectCategoryBudgetForm
  onCategoryBudgetFormChange: (next: ProjectCategoryBudgetForm) => void
  onSubmitCategoryBudget: () => Promise<boolean> | boolean
  onDeleteCategoryBudget: (budget: ReadProjectCategoryBudget) => Promise<void> | void
  iconPreviewUrlByFileId?: Record<string, string>
  onOpenDetail?: () => void
}) {
  const t = useT()
  const hasBudget = project.budget_amount != null && project.budget_amount > 0
  const ratio = hasBudget ? Math.min(project.spent / (project.budget_amount as number), 1) : 0
  const barColor =
    project.status === 'over' ? 'bg-red-500' : project.status === 'warning' ? 'bg-orange-500' : 'bg-primary/70'

  return (
    <div
      className={[
        'rounded-xl border bg-card p-4 transition hover:border-primary/40 hover:shadow-sm',
        project.enabled ? 'border-border/60' : 'border-border/40 opacity-60',
      ].join(' ')}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xl"
        >
          {project.icon || '📁'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-semibold">{project.name}</span>
            <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
              {t(`projects.periodType.${project.period_type}`)}
            </span>
            {hasBudget ? (
              <span
                className={[
                  'shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                  project.status === 'over'
                    ? 'bg-red-500/15 text-red-600'
                    : project.status === 'warning'
                      ? 'bg-orange-500/15 text-orange-600'
                      : 'bg-muted text-muted-foreground',
                ].join(' ')}
              >
                {t(`projects.status.${project.status}`)}
              </span>
            ) : null}
            {!project.enabled ? (
              <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {t('projects.label.disabled')}
              </span>
            ) : null}
          </div>
          {project.period_type === 'fixed' && project.period_start && project.period_end ? (
            <div className="mt-0.5 text-[11px] text-muted-foreground">
              {project.period_start.slice(0, 10)} ~ {project.period_end.slice(0, 10)}
            </div>
          ) : null}
        </div>
        <div className="shrink-0 text-right">
          <Amount value={project.spent} currency={currency} size="md" bold tone="default" />
          {hasBudget ? (
            <div className="text-[11px] text-muted-foreground">
              {t('projects.label.budget')}{' '}
              <Amount value={project.budget_amount as number} currency={currency} size="sm" tone="muted" />
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">{t('projects.label.noBudget')}</div>
          )}
        </div>
      </div>

      {hasBudget ? (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted">
          <div className={`h-full transition-all ${barColor}`} style={{ width: `${ratio * 100}%` }} />
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        {onOpenDetail ? (
          <Button size="sm" variant="ghost" onClick={onOpenDetail}>
            <BarChart3 className="mr-1 h-3.5 w-3.5" />
            {t('projects.detail.button')}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" onClick={onToggleExpand}>
          {expanded ? t('projects.categoryBudgets.toggle.collapse') : t('projects.categoryBudgets.toggle.expand')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
          {t('common.delete')}
        </Button>
      </div>

      {expanded ? (
        <ProjectCategoryBudgetsSection
          project={project}
          currency={currency}
          canManage={canManage}
          categories={categories}
          categoryBudgets={categoryBudgets}
          form={categoryBudgetForm}
          onFormChange={onCategoryBudgetFormChange}
          onSubmit={onSubmitCategoryBudget}
          onDelete={onDeleteCategoryBudget}
          iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        />
      ) : null}
    </div>
  )
}

function ProjectCategoryBudgetsSection({
  project,
  currency,
  canManage,
  categories,
  categoryBudgets,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  iconPreviewUrlByFileId,
}: {
  project: ReadProject
  currency: string
  canManage: boolean
  categories: readonly WorkspaceCategory[]
  categoryBudgets: ReadProjectCategoryBudget[] | undefined
  form: ProjectCategoryBudgetForm
  onFormChange: (next: ProjectCategoryBudgetForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (budget: ReadProjectCategoryBudget) => Promise<void> | void
  iconPreviewUrlByFileId?: Record<string, string>
}) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadProjectCategoryBudget | null>(null)
  const [deleting, setDeleting] = useState(false)

  const budgetAmount = project.budget_amount != null && project.budget_amount > 0 ? project.budget_amount : null

  // 已分配总额:fixed 直接用 fixed_amount,percentage 即时按目前的
  // project.budget_amount 解析成金额(不落地,跟 server 的展示口径一致)。
  const allocatedTotal = useMemo(() => {
    if (!categoryBudgets) return 0
    return categoryBudgets.reduce((sum, b) => {
      if (b.mode === 'fixed') return sum + (b.fixed_amount || 0)
      if (budgetAmount != null && b.percentage != null) return sum + (budgetAmount * b.percentage) / 100
      return sum
    }, 0)
  }, [categoryBudgets, budgetAmount])

  const usedCategoryIds = useMemo(() => {
    const set = new Set<string>()
    for (const b of categoryBudgets || []) set.add(b.category_id)
    return set
  }, [categoryBudgets])

  const categoryPickerRows = useMemo(() => {
    return categories.filter((c) => {
      if (Number(c.level) !== 1) return false
      if (form.editingId && form.category_id === c.id) return true
      if (usedCategoryIds.has(c.id)) return false
      return true
    })
  }, [categories, usedCategoryIds, form.editingId, form.category_id])

  const handleOpenCreate = () => {
    onFormChange(projectCategoryBudgetDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (budget: ReadProjectCategoryBudget) => {
    const cat = categories.find((c) => c.id === budget.category_id)
    onFormChange({
      editingId: budget.id,
      category_id: budget.category_id,
      category_name: cat?.name || '',
      mode: budget.mode,
      fixed_amount: budget.fixed_amount != null ? String(budget.fixed_amount) : '',
      percentage: budget.percentage != null ? String(budget.percentage) : '',
      carryover_enabled: budget.carryover_enabled,
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const canSubmit =
    Boolean(form.category_id) &&
    (form.mode === 'fixed' ? Boolean(form.fixed_amount.trim()) : Boolean(form.percentage.trim()))

  return (
    <div className="mt-3 space-y-2 rounded-lg border border-border/50 bg-muted/10 p-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">{t('projects.categoryBudgets.title')}</p>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={handleOpenCreate}>
          {t('projects.categoryBudgets.button.add')}
        </Button>
      </div>

      {budgetAmount != null ? (
        <p className={`text-xs ${allocatedTotal > budgetAmount ? 'font-medium text-red-600' : 'text-muted-foreground'}`}>
          {allocatedTotal > budgetAmount
            ? t('projects.categoryBudgets.label.overAllocated', {
                amount: (allocatedTotal - budgetAmount).toFixed(2),
              })
            : t('projects.categoryBudgets.label.allocated', {
                allocated: allocatedTotal.toFixed(2),
                total: budgetAmount.toFixed(2),
              })}
        </p>
      ) : null}

      {!categoryBudgets ? (
        <p className="px-1 py-2 text-center text-xs text-muted-foreground">{t('common.loading')}</p>
      ) : categoryBudgets.length === 0 ? (
        <p className="px-1 py-2 text-center text-xs text-muted-foreground">{t('projects.categoryBudgets.empty')}</p>
      ) : (
        <div className="space-y-1.5">
          {categoryBudgets.map((budget) => {
            const cat = categories.find((c) => c.id === budget.category_id)
            const resolvedAmount =
              budget.mode === 'fixed'
                ? budget.fixed_amount || 0
                : budgetAmount != null && budget.percentage != null
                  ? (budgetAmount * budget.percentage) / 100
                  : null
            return (
              <div
                key={budget.id}
                className="flex items-center gap-2 rounded-md border border-border/40 bg-card px-2 py-1.5"
              >
                <CategoryIcon
                  icon={cat?.icon}
                  iconType={cat?.icon_type || 'material'}
                  iconCloudFileId={cat?.icon_cloud_file_id}
                  iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                  size={16}
                />
                <span className="min-w-0 flex-1 truncate text-xs">{cat?.name || budget.category_id}</span>
                <span className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {t(`projects.categoryBudgets.mode.${budget.mode}`)}
                </span>
                <span className="shrink-0 text-xs tabular-nums">
                  {budget.mode === 'percentage'
                    ? `${budget.percentage}%`
                    : resolvedAmount != null
                      ? <Amount value={resolvedAmount} currency={currency} size="sm" tone="default" />
                      : '—'}
                </span>
                <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenEdit(budget)}>
                  {t('common.edit')}
                </Button>
                <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => setPendingDelete(budget)}>
                  {t('common.delete')}
                </Button>
              </div>
            )
          })}
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>
              {form.editingId
                ? t('projects.categoryBudgets.button.update')
                : t('projects.categoryBudgets.button.add')}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('projects.categoryBudgets.field.category')}</Label>
              <button
                type="button"
                disabled={!!form.editingId}
                onClick={() => setCategoryPickerOpen(true)}
                className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className={`truncate ${form.category_name ? '' : 'text-muted-foreground'}`}>
                  {form.category_name || t('budgets.placeholder.category')}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>

            <div className="space-y-1">
              <Label>{t('projects.categoryBudgets.field.mode')}</Label>
              <div className="grid grid-cols-2 gap-2">
                {CATEGORY_BUDGET_MODES.map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    disabled={mode === 'percentage' && budgetAmount == null}
                    onClick={() => onFormChange({ ...form, mode })}
                    className={[
                      'rounded-md border px-3 py-2 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                      form.mode === mode
                        ? 'border-primary/60 bg-primary/10 text-primary'
                        : 'border-border/60 hover:bg-accent/40',
                    ].join(' ')}
                  >
                    {t(`projects.categoryBudgets.mode.${mode}`)}
                  </button>
                ))}
              </div>
            </div>

            {form.mode === 'fixed' ? (
              <div className="space-y-1">
                <Label>{t('projects.categoryBudgets.field.fixedAmount')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  value={form.fixed_amount}
                  onChange={(e) => onFormChange({ ...form, fixed_amount: e.target.value })}
                />
              </div>
            ) : (
              <div className="space-y-1">
                <Label>{t('projects.categoryBudgets.field.percentage')}</Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="1"
                  min="0"
                  max="100"
                  value={form.percentage}
                  onChange={(e) => onFormChange({ ...form, percentage: e.target.value })}
                />
                {budgetAmount != null && form.percentage.trim() ? (
                  <p className="text-xs text-muted-foreground">
                    = <Amount value={(budgetAmount * Number(form.percentage)) / 100} currency={currency} size="sm" tone="muted" />
                  </p>
                ) : null}
              </div>
            )}

            {project.period_type !== 'fixed' ? (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.carryover_enabled}
                  onChange={(e) => onFormChange({ ...form, carryover_enabled: e.target.checked })}
                />
                {t('projects.categoryBudgets.field.carryoverEnabled')}
              </label>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId
                ? t('projects.categoryBudgets.button.update')
                : t('projects.categoryBudgets.button.add')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind="expense"
        rows={categoryPickerRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || null}
        title={t('projects.categoryBudgets.field.category')}
        onSelect={(cat) => {
          onFormChange({ ...form, category_id: cat.id, category_name: cat.name })
          setCategoryPickerOpen(false)
        }}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('projects.categoryBudgets.delete.title')}
        description={t('projects.categoryBudgets.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}
