import { useState } from 'react'

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

import type { ReadProject } from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { ConfirmDialog } from '../components/ConfirmDialog'
import type { ProjectForm, ProjectPeriodType } from '../forms'
import { projectDefaults } from '../forms'

type ProjectsPanelProps = {
  projects: readonly ReadProject[]
  currency: string
  form: ProjectForm
  onFormChange: (next: ProjectForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (project: ReadProject) => Promise<void> | void
  /** 账本 owner 才能新建/编辑/删除专案(server `_OWNER_ONLY_ROLES`)。 */
  canManage: boolean
}

const PERIOD_TYPES: ProjectPeriodType[] = ['monthly', 'yearly', 'fixed']

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
}: ProjectsPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadProject | null>(null)
  const [deleting, setDeleting] = useState(false)

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
                  <Input
                    type="date"
                    value={form.period_start}
                    onChange={(e) => onFormChange({ ...form, period_start: e.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label>{t('projects.field.periodEnd')}</Label>
                  <Input
                    type="date"
                    value={form.period_end}
                    onChange={(e) => onFormChange({ ...form, period_end: e.target.value })}
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
}: {
  project: ReadProject
  currency: string
  canManage: boolean
  onEdit: () => void
  onDelete: () => void
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
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onEdit}>
          {t('common.edit')}
        </Button>
        <Button size="sm" variant="ghost" disabled={!canManage} onClick={onDelete}>
          {t('common.delete')}
        </Button>
      </div>
    </div>
  )
}
