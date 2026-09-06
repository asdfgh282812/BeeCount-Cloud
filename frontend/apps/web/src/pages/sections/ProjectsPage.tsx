import { useCallback, useEffect, useState } from 'react'

import {
  createProject,
  createProjectCategoryBudget,
  deleteProject,
  deleteProjectCategoryBudget,
  fetchReadProjectCategoryBudgets,
  fetchReadProjects,
  fetchWorkspaceCategories,
  updateProject,
  updateProjectCategoryBudget,
  type ReadProject,
  type ReadProjectCategoryBudget,
  type WorkspaceCategory,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import {
  ProjectsPanel,
  projectCategoryBudgetDefaults,
  projectDefaults,
  type ProjectCategoryBudgetForm,
  type ProjectForm,
} from '@beecount/web-features'

import { useAttachmentCache } from '../../context/AttachmentCacheContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { ProjectDetailDialog } from '../../components/dialogs/ProjectDetailDialog'
import { localizeError } from '../../i18n/errors'

/**
 * 专案管理页(Phase 13,docs/PH13_PROJECT_SD.md)—— 原本设计放在「标签」
 * 分页底下的子分页,使用者后来要求分开成独立入口,紧邻标签右侧
 * (见 `@beecount/web-features` 的 `nav.ts` NAV_GROUPS,`projects` 排在
 * `tags` 后面)。资料模型上专案本来就跟标签互相独立(帐本维度、
 * `ReadProject`,PK 带 ledger_id),这里只是把 UI 挂载点从 TagsPage 的
 * Tabs 里搬到独立路由,逻辑跟原本子分页版本一致。
 */
export function ProjectsPage() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currency, currentLedger } = useLedgers()
  const { retryOnConflict, isWriteConflict } = useLedgerWrite()
  const { previewMap: iconPreviewByFileId, ensureLoadedMany } = useAttachmentCache()

  const projectBucket = activeLedgerId || '__none__'
  const [projects, setProjects] = usePageCache<ReadProject[]>(`projects:${projectBucket}:rows`, [])
  const [categories, setCategories] = usePageCache<WorkspaceCategory[]>('projects:categories', [])
  const [categoryBudgetsByProjectId, setCategoryBudgetsByProjectId] = useState<
    Record<string, ReadProjectCategoryBudget[]>
  >({})
  const [form, setForm] = useState<ProjectForm>(projectDefaults())
  const [categoryBudgetForm, setCategoryBudgetForm] = useState<ProjectCategoryBudgetForm>(
    projectCategoryBudgetDefaults(),
  )
  const [detailProject, setDetailProject] = useState<ReadProject | null>(null)
  const canManage = Boolean(activeLedgerId) && currentLedger?.role === 'owner'

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t]
  )
  const notifySuccess = useCallback(
    (msg: string) => toast.success(msg, t('notice.success')),
    [toast, t]
  )

  const refresh = useCallback(async () => {
    if (!activeLedgerId) {
      setProjects([])
      return
    }
    try {
      const [p, c] = await Promise.all([
        fetchReadProjects(token, activeLedgerId),
        fetchWorkspaceCategories(token, {}),
      ])
      setProjects(p)
      setCategories(c)
    } catch (err) {
      notifyError(err)
    }
    // setProjects/setCategories 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

  useEffect(() => {
    const ids = categories
      .map((c) => c.icon_cloud_file_id || '')
      .filter((v) => v.trim().length > 0)
    if (ids.length > 0) ensureLoadedMany(ids)
  }, [categories, ensureLoadedMany])

  const onLoadCategoryBudgets = useCallback(
    (project: ReadProject) => {
      if (!activeLedgerId) return
      void fetchReadProjectCategoryBudgets(token, activeLedgerId, project.id)
        .then((rows) => setCategoryBudgetsByProjectId((prev) => ({ ...prev, [project.id]: rows })))
        .catch(() => setCategoryBudgetsByProjectId((prev) => ({ ...prev, [project.id]: [] })))
    },
    [token, activeLedgerId],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  useSyncRefresh(() => {
    void refresh()
  })

  const onSubmit = async (): Promise<boolean> => {
    if (!activeLedgerId) {
      toast.error(t('shell.selectLedgerFirst'), t('notice.error'))
      return false
    }
    const budgetAmount = form.budget_amount.trim() ? Number(form.budget_amount) : null
    const periodStart = form.period_type === 'fixed' && form.period_start
      ? new Date(form.period_start).toISOString()
      : null
    const periodEnd = form.period_type === 'fixed' && form.period_end
      ? new Date(form.period_end).toISOString()
      : null
    const reminderThresholdPercent = form.reminder_threshold_percent.trim()
      ? Number(form.reminder_threshold_percent)
      : null
    try {
      if (form.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateProject(token, activeLedgerId, form.editingId!, base, {
            name: form.name.trim(),
            icon: form.icon || null,
            budget_amount: budgetAmount,
            period_type: form.period_type,
            period_start: periodStart,
            period_end: periodEnd,
            carryover_enabled: form.carryover_enabled,
            visible_on_home: form.visible_on_home,
            enabled: form.enabled,
            income_included_in_budget: form.income_included_in_budget,
            daily_budget_enabled: form.daily_budget_enabled,
            daily_budget_mode: form.daily_budget_enabled ? form.daily_budget_mode : null,
            reminder_threshold_percent: reminderThresholdPercent,
          }),
        )
        notifySuccess(t('projects.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createProject(token, activeLedgerId, base, {
            name: form.name.trim(),
            icon: form.icon || null,
            budget_amount: budgetAmount,
            period_type: form.period_type,
            period_start: periodStart,
            period_end: periodEnd,
            carryover_enabled: form.carryover_enabled,
            visible_on_home: form.visible_on_home,
            income_included_in_budget: form.income_included_in_budget,
            daily_budget_enabled: form.daily_budget_enabled,
            daily_budget_mode: form.daily_budget_enabled ? form.daily_budget_mode : null,
            reminder_threshold_percent: reminderThresholdPercent,
          }),
        )
        notifySuccess(t('projects.notice.created'))
      }
      setForm(projectDefaults())
      await refresh()
      return true
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
      return false
    }
  }

  const onDelete = async (project: ReadProject): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) => deleteProject(token, activeLedgerId, project.id, base))
      notifySuccess(t('projects.notice.deleted'))
      await refresh()
    } catch (err) {
      if (isWriteConflict(err)) await refresh()
      notifyError(err)
    }
  }

  const onSubmitCategoryBudget = async (project: ReadProject): Promise<boolean> => {
    if (!activeLedgerId) return false
    const f = categoryBudgetForm
    const payload = {
      mode: f.mode,
      fixed_amount: f.mode === 'fixed' && f.fixed_amount.trim() ? Number(f.fixed_amount) : null,
      percentage: f.mode === 'percentage' && f.percentage.trim() ? Number(f.percentage) : null,
      carryover_enabled: f.carryover_enabled,
    }
    try {
      if (f.editingId) {
        await retryOnConflict(activeLedgerId, (base) =>
          updateProjectCategoryBudget(token, activeLedgerId, project.id, f.editingId!, base, payload),
        )
        notifySuccess(t('projects.categoryBudgets.notice.updated'))
      } else {
        await retryOnConflict(activeLedgerId, (base) =>
          createProjectCategoryBudget(token, activeLedgerId, project.id, base, {
            category_id: f.category_id,
            ...payload,
          }),
        )
        notifySuccess(t('projects.categoryBudgets.notice.created'))
      }
      setCategoryBudgetForm(projectCategoryBudgetDefaults())
      onLoadCategoryBudgets(project)
      return true
    } catch (err) {
      if (isWriteConflict(err)) onLoadCategoryBudgets(project)
      notifyError(err)
      return false
    }
  }

  const onDeleteCategoryBudget = async (
    project: ReadProject,
    budget: ReadProjectCategoryBudget,
  ): Promise<void> => {
    if (!activeLedgerId) return
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteProjectCategoryBudget(token, activeLedgerId, project.id, budget.id, base),
      )
      notifySuccess(t('projects.categoryBudgets.notice.deleted'))
      onLoadCategoryBudgets(project)
    } catch (err) {
      if (isWriteConflict(err)) onLoadCategoryBudgets(project)
      notifyError(err)
    }
  }

  if (!activeLedgerId) {
    return <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
  }

  return (
    <>
      <ProjectsPanel
        projects={projects}
        currency={currency}
        form={form}
        onFormChange={setForm}
        onSubmit={onSubmit}
        onDelete={onDelete}
        canManage={canManage}
        categories={categories}
        categoryBudgetsByProjectId={categoryBudgetsByProjectId}
        onLoadCategoryBudgets={onLoadCategoryBudgets}
        categoryBudgetForm={categoryBudgetForm}
        onCategoryBudgetFormChange={setCategoryBudgetForm}
        onSubmitCategoryBudget={onSubmitCategoryBudget}
        onDeleteCategoryBudget={onDeleteCategoryBudget}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        onOpenDetail={setDetailProject}
      />
      <ProjectDetailDialog
        project={detailProject}
        onClose={() => setDetailProject(null)}
        categories={categories}
        currency={currency}
        iconPreviewUrlByFileId={iconPreviewByFileId}
      />
    </>
  )
}
