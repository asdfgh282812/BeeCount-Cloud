import { ShieldCheck } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle, useT, useToast } from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'
import { useLicense } from '../../context/LicenseContext'
import { formatLicenseDate, isLicenseExpired } from '../../lib/license'
import { LicenseKeyForm } from '../LicenseKeyForm'

/**
 * 設定 → 個人資料頁的授權卡片(docs/LICENSE_KEYS.md):顯示授權到期日,並可
 * 再輸入一把金鑰延長(server 端從目前到期日往後累加)。狀態讀 LicenseGate
 * 已經查好的那份,不自己 fetch;啟用成功後 `applyStatus` 寫回閘門。
 *
 * 管理者免金鑰,只顯示「管理員免授權」,不給輸入框 —— 管理者啟用金鑰會白白
 * 消耗掉一把。
 */
export function SettingsLicenseSection() {
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const license = useLicense()
  const status = license?.status ?? null

  if (!license || !status) return null

  const expired = isLicenseExpired(status.expires_at, status.server_time)

  return (
    <Card className="bc-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-primary" />
          {t('license.settings.title')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg border border-border/60 bg-muted/20 px-4 py-3 text-sm">
          {status.exempt ? (
            t('license.settings.adminExempt')
          ) : status.expires_at ? (
            <span className="flex flex-wrap items-center gap-2">
              {t('license.settings.expiresAt', { date: formatLicenseDate(status.expires_at) })}
              {expired ? (
                <span className="inline-flex items-center rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
                  {t('license.settings.expired')}
                </span>
              ) : null}
            </span>
          ) : (
            t('license.settings.none')
          )}
        </div>

        {status.exempt ? null : (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t('license.settings.extendHint')}</p>
            <LicenseKeyForm
              token={token}
              submitLabel={t('license.settings.extend')}
              onActivated={(next) => {
                toast.success(
                  t('license.notice.activated', { date: formatLicenseDate(next.expires_at) }),
                  t('notice.success'),
                )
                license.applyStatus(next)
              }}
            />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
