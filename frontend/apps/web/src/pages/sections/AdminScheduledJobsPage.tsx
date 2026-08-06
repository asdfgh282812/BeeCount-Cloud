import { useCallback, useEffect, useState } from 'react'
import { Clock, Loader2, PlayCircle, RefreshCcw } from 'lucide-react'

import {
  fetchScheduledJobs,
  runScheduledJobNow,
  updateScheduledJob,
  type ScheduledJobConfig,
} from '@beecount/api-client'
import { Badge, Button, Card, CardContent, Input, useT, useToast } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

/**
 * 管理员 · 背景排程管理後台 —— 原本 4 條各自獨立的 asyncio 迴圈(7 個排程
 * 動作)收斂成一張設定表後,在這裡提供 UI 讓 admin 調整頻率/停用/立即執行,
 * 不需要改代碼重新部署。跟 `AdminDataCleanupPage`/`AdminBackupPage` 同款
 * useAuth() admin 判斷 + 首次載入 fetch 樣板。
 */
export function AdminScheduledJobsPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [jobs, setJobs] = useState<ScheduledJobConfig[]>([])
  const [loading, setLoading] = useState(false)
  // 每列输入框的分钟值,跟服务器 interval_seconds 分开维护,避免每次
  // re-render(如轮询刷新)覆盖用户正在编辑但还没套用的输入。
  const [intervalDrafts, setIntervalDrafts] = useState<Record<string, string>>({})
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [runningKey, setRunningKey] = useState<string | null>(null)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      const result = await fetchScheduledJobs(token)
      setJobs(result)
      setIntervalDrafts((prev) => {
        const next = { ...prev }
        for (const job of result) {
          if (next[job.job_key] === undefined) {
            next[job.job_key] = String(Math.round(job.interval_seconds / 60))
          }
        }
        return next
      })
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, isAdmin, notifyError])

  useEffect(() => {
    if (!isAdminResolved || !isAdmin) return
    void refresh()
  }, [isAdminResolved, isAdmin, refresh])

  const applyInterval = useCallback(
    async (jobKey: string) => {
      const raw = intervalDrafts[jobKey]
      const minutes = Number(raw)
      if (!Number.isFinite(minutes) || minutes < 1) {
        toast.error(t('admin.scheduledJobs.error.intervalInvalid'), t('notice.error'))
        return
      }
      setSavingKey(jobKey)
      try {
        const updated = await updateScheduledJob(token, jobKey, {
          interval_seconds: Math.round(minutes * 60),
        })
        setJobs((prev) => prev.map((j) => (j.job_key === jobKey ? updated : j)))
        setIntervalDrafts((prev) => ({
          ...prev,
          [jobKey]: String(Math.round(updated.interval_seconds / 60)),
        }))
        toast.success(t('admin.scheduledJobs.notice.updated'), t('notice.success'))
      } catch (err) {
        notifyError(err)
      } finally {
        setSavingKey(null)
      }
    },
    [intervalDrafts, token, toast, t, notifyError],
  )

  const toggleEnabled = useCallback(
    async (job: ScheduledJobConfig) => {
      setSavingKey(job.job_key)
      try {
        const updated = await updateScheduledJob(token, job.job_key, { enabled: !job.enabled })
        setJobs((prev) => prev.map((j) => (j.job_key === job.job_key ? updated : j)))
        toast.success(t('admin.scheduledJobs.notice.updated'), t('notice.success'))
      } catch (err) {
        notifyError(err)
      } finally {
        setSavingKey(null)
      }
    },
    [token, toast, t, notifyError],
  )

  const runNow = useCallback(
    async (jobKey: string) => {
      setRunningKey(jobKey)
      try {
        const result = await runScheduledJobNow(token, jobKey)
        if (result.status === 'error') {
          toast.error(
            t('admin.scheduledJobs.notice.runFailed', { message: result.message || '' }),
            t('notice.error'),
          )
        } else {
          toast.success(
            t('admin.scheduledJobs.notice.runSuccess', { message: result.message || '' }),
            t('notice.success'),
          )
        }
        await refresh()
      } catch (err) {
        notifyError(err)
      } finally {
        setRunningKey(null)
      }
    },
    [token, toast, t, notifyError, refresh],
  )

  if (!isAdminResolved) {
    return null
  }

  if (!isAdmin) {
    return (
      <Card className="bc-panel">
        <CardContent className="py-6">
          <p className="text-center text-sm text-muted-foreground">
            {t('admin.users.noPermission')}
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardContent className="flex items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            <Clock className="h-5 w-5 text-primary" />
            <div>
              <h3 className="text-sm font-medium">{t('admin.scheduledJobs.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('admin.scheduledJobs.subtitle')}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
            <RefreshCcw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('admin.scheduledJobs.refresh')}
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-3">
        {jobs.map((job) => (
          <JobRow
            key={job.job_key}
            job={job}
            intervalDraft={intervalDrafts[job.job_key] ?? ''}
            onIntervalDraftChange={(value) =>
              setIntervalDrafts((prev) => ({ ...prev, [job.job_key]: value }))
            }
            onApplyInterval={() => void applyInterval(job.job_key)}
            onToggleEnabled={() => void toggleEnabled(job)}
            onRunNow={() => void runNow(job.job_key)}
            saving={savingKey === job.job_key}
            running={runningKey === job.job_key}
            t={t}
          />
        ))}
      </div>
    </div>
  )
}

type TFunction = (key: string, vars?: Record<string, string | number>) => string

function formatDateTime(iso: string | null | undefined, t: TFunction): string {
  if (!iso) return t('admin.scheduledJobs.never')
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return t('admin.scheduledJobs.never')
  return d.toLocaleString()
}

function JobRow(props: {
  job: ScheduledJobConfig
  intervalDraft: string
  onIntervalDraftChange: (value: string) => void
  onApplyInterval: () => void
  onToggleEnabled: () => void
  onRunNow: () => void
  saving: boolean
  running: boolean
  t: TFunction
}) {
  const {
    job,
    intervalDraft,
    onIntervalDraftChange,
    onApplyInterval,
    onToggleEnabled,
    onRunNow,
    saving,
    running,
    t,
  } = props

  const displayName = t(`admin.scheduledJobs.job.${job.job_key}`)
  const intervalChanged =
    intervalDraft !== '' && Number(intervalDraft) !== Math.round(job.interval_seconds / 60)

  return (
    <Card className="bc-panel">
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium">{displayName}</p>
            <p className="text-xs text-muted-foreground">{job.job_key}</p>
          </div>
          {job.last_run_status ? (
            <Badge variant={job.last_run_status === 'error' ? 'destructive' : 'default'}>
              {job.last_run_status === 'error'
                ? t('admin.scheduledJobs.status.error')
                : t('admin.scheduledJobs.status.ok')}
            </Badge>
          ) : null}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">{t('admin.scheduledJobs.table.lastRun')}</p>
            <p className="truncate text-sm">{formatDateTime(job.last_run_at, t)}</p>
            {job.last_run_message ? (
              <p className="truncate text-xs text-muted-foreground">{job.last_run_message}</p>
            ) : null}
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">{t('admin.scheduledJobs.table.nextRun')}</p>
            <p className="truncate text-sm">{formatDateTime(job.next_run_at, t)}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t border-border/50 pt-3">
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted-foreground" htmlFor={`interval-${job.job_key}`}>
              {t('admin.scheduledJobs.table.interval')}
            </label>
            <Input
              id={`interval-${job.job_key}`}
              type="number"
              min={1}
              className="h-8 w-24"
              value={intervalDraft}
              onChange={(e) => onIntervalDraftChange(e.target.value)}
              disabled={saving}
            />
            {intervalChanged ? (
              <Button size="sm" variant="outline" onClick={onApplyInterval} disabled={saving}>
                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t('admin.scheduledJobs.applyInterval')}
              </Button>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">{t('admin.scheduledJobs.table.enabled')}</span>
            <button
              type="button"
              role="switch"
              aria-checked={job.enabled}
              aria-label={t('admin.scheduledJobs.table.enabled')}
              onClick={onToggleEnabled}
              disabled={saving}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                job.enabled ? 'bg-primary' : 'bg-muted-foreground/30'
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                  job.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>

          <Button size="sm" className="ml-auto" onClick={onRunNow} disabled={running}>
            {running ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <PlayCircle className="mr-1.5 h-3.5 w-3.5" />
            )}
            {running ? t('admin.scheduledJobs.running') : t('admin.scheduledJobs.runNow')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
