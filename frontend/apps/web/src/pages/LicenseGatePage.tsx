import { LogOut, ShieldAlert } from 'lucide-react'

import type { LicenseStatus } from '@beecount/api-client'
import { Button, LanguageToggle, ThemeToggle, useT, useToast } from '@beecount/ui'

import { LicenseKeyForm } from '../components/LicenseKeyForm'
import { formatLicenseDate, isLicenseExpired } from '../lib/license'

interface Props {
  token: string
  /** 查詢失敗(例如授權中途失效後重查也失敗)時為 null,只顯示通用說明。 */
  status: LicenseStatus | null
  onActivated: (status: LicenseStatus) => void
  onLogout: () => void
}

/**
 * 沒有有效授權時取代整個 AppShell 的全頁金鑰輸入畫面(docs/LICENSE_KEYS.md)。
 * 版面沿用 LoginPage 的漸層背景 + 置中卡片,讓使用者感覺還在「登入流程」裡,
 * 而不是 app 壞掉。
 */
export function LicenseGatePage({ token, status, onActivated, onLogout }: Props) {
  const t = useT()
  const toast = useToast()
  const expired = !!status?.expires_at && isLicenseExpired(status.expires_at, status.server_time)

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div
        className="pointer-events-none absolute inset-0 bg-gradient-to-br from-primary/15 via-primary/5 to-transparent"
        aria-hidden
      />
      <div className="absolute right-4 top-4 flex items-center gap-2">
        <LanguageToggle />
        <ThemeToggle />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-lg items-center justify-center px-4 py-10">
        <div className="w-full space-y-6 rounded-2xl border border-border/60 bg-card/90 p-8 shadow-xl backdrop-blur-md">
          <div className="flex items-center gap-3">
            <img src="/branding/logo.svg" alt={t('shell.appName')} className="h-10 w-10" />
            <div className="text-lg font-semibold">{t('app.brand')}</div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-primary" />
              <h1 className="text-xl font-bold">{t('license.gate.title')}</h1>
            </div>
            <p className="text-sm text-muted-foreground">{t('license.gate.desc')}</p>
            {expired ? (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
                {t('license.gate.expiredOn', { date: formatLicenseDate(status?.expires_at) })}
              </p>
            ) : null}
          </div>

          <LicenseKeyForm
            token={token}
            autoFocus
            onActivated={(next) => {
              if (next.licensed) {
                toast.success(
                  t('license.notice.activated', { date: formatLicenseDate(next.expires_at) }),
                  t('notice.success'),
                )
              }
              onActivated(next)
            }}
          />

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/50 pt-4 text-xs text-muted-foreground">
            <span className="truncate">
              {status?.email ? t('license.gate.signedInAs', { email: status.email }) : null}
            </span>
            <Button size="sm" variant="outline" onClick={onLogout}>
              <LogOut className="mr-1.5 h-3.5 w-3.5" />
              {t('shell.logout')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
