import { useCallback, useEffect, useState } from 'react'

import {
  createProject,
  deleteProject,
  fetchReadProjects,
  updateProject,
  type ReadProject,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import { ProjectsPanel, projectDefaults, type ProjectForm } from '@beecount/web-features'

import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { usePageCache } from '../../context/PageDataCacheContext'
import { useSyncRefresh } from '../../context/SyncSocketContext'
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

  const projectBucket = activeLedgerId || '__none__'
  const [projects, setProjects] = usePageCache<ReadProject[]>(`projects:${projectBucket}:rows`, [])
  const [form, setForm] = useState<ProjectForm>(projectDefaults())
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
      setProjects(await fetchReadProjects(token, activeLedgerId))
    } catch (err) {
      notifyError(err)
    }
    // setProjects 来自 usePageCache,引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeLedgerId, notifyError])

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

  if (!activeLedgerId) {
    return <p className="text-sm text-muted-foreground">{t('shell.selectLedgerFirst')}</p>
  }

  return (
    <ProjectsPanel
      projects={projects}
      currency={currency}
      form={form}
      onFormChange={setForm}
      onSubmit={onSubmit}
      onDelete={onDelete}
      canManage={canManage}
    />
  )
}
