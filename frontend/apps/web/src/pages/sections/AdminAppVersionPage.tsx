import { useCallback, useEffect, useState } from 'react'
import { RefreshCcw, Smartphone } from 'lucide-react'

import {
  checkAppVersionNow,
  fetchAppVersionConfig,
  updateAppVersionConfig,
  type AppVersionConfig,
} from '@beecount/api-client'
import { Button, Card, CardContent, Input, useT, useToast } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'

/**
 * 管理员 · App 端新版本提醒設定(docs/superpowers/specs/
 * 2026-09-08-app-update-reminder-design.md §2)。跟 `AdminScheduledJobsPage`
 * 同款 useAuth() admin 判斷 + 首次載入 fetch + 本地 draft state 樣板 —— draft
 * state 避免打字被輪詢/重新 fetch 覆蓋掉使用者還沒送出的輸入。
 *
 * 密碼欄位刻意不回顯明文(server 只回傳 `nas_webdav_password_set` 布林值),
 * 所以這裡永遠不會把已設定的密碼帶回 PUT body —— 空白 = 不變更,這是
 * server 端 `nas_webdav_password` 更新語意的唯一合理對應。
 */
export function AdminAppVersionPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [config, setConfig] = useState<AppVersionConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)

  const [latestVersionDraft, setLatestVersionDraft] = useState('')
  const [urlDraft, setUrlDraft] = useState('')
  const [userDraft, setUserDraft] = useState('')
  const [passwordDraft, setPasswordDraft] = useState('')

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const applyConfig = useCallback((row: AppVersionConfig) => {
    setConfig(row)
    setLatestVersionDraft(row.latest_version ?? '')
    setUrlDraft(row.nas_webdav_url ?? '')
    setUserDraft(row.nas_webdav_user ?? '')
    setPasswordDraft('')
  }, [])

  const refresh = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      const row = await fetchAppVersionConfig(token)
      applyConfig(row)
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, isAdmin, applyConfig, notifyError])

  useEffect(() => {
    if (!isAdminResolved || !isAdmin) return
    void refresh()
  }, [isAdminResolved, isAdmin, refresh])

  const handleSave = useCallback(async () => {
    setSaving(true)
    try {
      const updated = await updateAppVersionConfig(token, {
        latest_version: latestVersionDraft,
        nas_webdav_url: urlDraft,
        nas_webdav_user: userDraft,
        ...(passwordDraft ? { nas_webdav_password: passwordDraft } : {}),
      })
      applyConfig(updated)
      toast.success(t('admin.appVersion.notice.saved'), t('notice.success'))
    } catch (err) {
      notifyError(err)
    } finally {
      setSaving(false)
    }
  }, [token, latestVersionDraft, urlDraft, userDraft, passwordDraft, applyConfig, toast, t, notifyError])

  const handleCheckNow = useCallback(async () => {
    setChecking(true)
    try {
      const result = await checkAppVersionNow(token)
      if (result.status === 'error') {
        toast.error(
          t('admin.appVersion.notice.checkFailed', { message: result.last_check_error || '' }),
          t('notice.error'),
        )
      } else if (result.status === 'skipped') {
        toast.error(t('admin.appVersion.notice.checkSkipped'), t('notice.error'))
      } else {
        toast.success(t('admin.appVersion.notice.checkSuccess'), t('notice.success'))
      }
      await refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setChecking(false)
    }
  }, [token, toast, t, notifyError, refresh])

  const formatDateTime = (iso: string | null | undefined): string => {
    if (!iso) return t('admin.appVersion.never')
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return t('admin.appVersion.never')
    return d.toLocaleString()
  }

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
            <Smartphone className="h-5 w-5 text-primary" />
            <div>
              <h3 className="text-sm font-medium">{t('admin.appVersion.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('admin.appVersion.subtitle')}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
            <RefreshCcw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('admin.appVersion.refresh')}
          </Button>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-4 py-4">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="app-version-latest">
              {t('admin.appVersion.field.latestVersion')}
            </label>
            <Input
              id="app-version-latest"
              className="h-9"
              value={latestVersionDraft}
              onChange={(e) => setLatestVersionDraft(e.target.value)}
              placeholder="3.2.0"
              disabled={saving}
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="app-version-url">
              {t('admin.appVersion.field.webdavUrl')}
            </label>
            <Input
              id="app-version-url"
              className="h-9"
              value={urlDraft}
              onChange={(e) => setUrlDraft(e.target.value)}
              placeholder="https://nas.example.com/webdav/BeeCount/latest_version.txt"
              disabled={saving}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="app-version-user">
                {t('admin.appVersion.field.webdavUser')}
              </label>
              <Input
                id="app-version-user"
                className="h-9"
                value={userDraft}
                onChange={(e) => setUserDraft(e.target.value)}
                disabled={saving}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="app-version-password">
                {t('admin.appVersion.field.webdavPassword')}
              </label>
              <Input
                id="app-version-password"
                type="password"
                className="h-9"
                value={passwordDraft}
                onChange={(e) => setPasswordDraft(e.target.value)}
                placeholder={
                  config?.nas_webdav_password_set
                    ? t('admin.appVersion.field.webdavPasswordSetPlaceholder')
                    : ''
                }
                disabled={saving}
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border/50 pt-3 text-xs text-muted-foreground">
            <span>
              {t('admin.appVersion.lastCheckedAt')}: {formatDateTime(config?.last_checked_at)}
            </span>
            {config?.last_check_error ? (
              <span className="text-destructive">
                {t('admin.appVersion.lastCheckError')}: {config.last_check_error}
              </span>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
              {t('admin.appVersion.save')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleCheckNow()}
              disabled={checking}
            >
              {checking ? t('admin.appVersion.checking') : t('admin.appVersion.checkNow')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
