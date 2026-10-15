import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Cloud, RefreshCcw, User, Wifi } from 'lucide-react'
import { fetchAdminHealth } from '@beecount/api-client'
import { Button, Card, CardContent, CardHeader, CardTitle, useT } from '@beecount/ui'
import { useAuth } from '../../context/AuthContext'

/** Web 固定使用目前 Cloud 伺服器，App 的本機與備份後端沒有 Web 對應。 */
export function SettingsCloudPage() {
  const t = useT()
  const { token, profileMe } = useAuth()
  const [status, setStatus] = useState<'checking' | 'ok' | 'error'>('checking')
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision((v) => v + 1), [])
  useEffect(() => {
    let active = true
    setStatus('checking')
    void fetchAdminHealth(token).then(
      (health) => { if (active) setStatus(health.status === 'ok' ? 'ok' : 'error') },
      () => { if (active) setStatus('error') },
    )
    return () => { active = false }
  }, [token, revision])

  return <div className="mx-auto max-w-3xl space-y-4" data-testid="cloud-management">
    <Card className="bc-panel">
      <CardHeader><CardTitle>{t('cloud.management.title')}</CardTitle></CardHeader>
      <CardContent className="space-y-5">
        <div className="flex items-start gap-3"><Cloud className="mt-1 h-5 w-5 shrink-0 text-primary" />
          <div className="min-w-0"><p className="font-medium">{t('cloud.management.backend')}</p>
            <p className="break-all text-sm text-muted-foreground">{window.location.origin}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t('cloud.management.webOnly')}</p>
          </div>
        </div>
        <div className="flex items-start gap-3"><User className="mt-1 h-5 w-5 shrink-0 text-primary" />
          <div className="min-w-0"><p className="break-all font-medium">{profileMe?.email || '—'}</p>
            <Link className="text-sm text-primary underline" to="/app/settings/profile">{t('nav.profile')}</Link>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3" aria-live="polite">
          <div className="flex items-center gap-3"><Wifi className="h-5 w-5 text-primary" />
            <span>{t(`cloud.management.${status}`)}</span>
          </div>
          <Button variant="outline" disabled={status === 'checking'} onClick={refresh}>
            <RefreshCcw className="mr-2 h-4 w-4" />{t('cloud.management.refresh')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{t('cloud.management.statusHint')}</p>
        <div className="flex flex-wrap gap-3">
          <Link className="rounded-lg border px-4 py-2 text-sm hover:bg-muted" to="/app/settings/devices">{t('nav.devices')}</Link>
          <Link className="rounded-lg border px-4 py-2 text-sm hover:bg-muted" to="/app/settings/health">{t('nav.health')}</Link>
        </div>
      </CardContent>
    </Card>
  </div>
}
