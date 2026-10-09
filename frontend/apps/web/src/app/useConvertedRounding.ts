import { useCallback, useMemo, useState } from 'react'

import { patchProfileMe } from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import { resolveConvertedRounding, type ConvertedRounding } from '@beecount/web-features'

import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'
import { localizeError } from '../i18n/errors'

/**
 * 外幣折算成本位幣之後的取整方式(以帳本為單位,跟著雲端帳號同步)。
 * 存在 profile `appearance.converted_rounding`(`{ledger_id: mode}`)+
 * `converted_rounding_updated_at`,整份 last-write-wins(同 App
 * `converted_rounding_providers.dart`);appearance 是整體替換語意,所以寫入時把
 * 現有 appearance 全量帶上。
 */
export function useConvertedRounding() {
  const t = useT()
  const toast = useToast()
  const { token, profileMe, refreshProfile } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [saving, setSaving] = useState(false)
  const appearance = profileMe?.appearance ?? {}
  const base = profileMe?.primary_currency || ''

  const mode = useMemo(
    () => resolveConvertedRounding(appearance.converted_rounding, activeLedgerId, base),
    [appearance.converted_rounding, activeLedgerId, base],
  )

  const setMode = useCallback(
    async (next: ConvertedRounding) => {
      if (!activeLedgerId || saving) return
      setSaving(true)
      try {
        await patchProfileMe(token, {
          appearance: {
            ...appearance,
            converted_rounding: { ...(appearance.converted_rounding ?? {}), [activeLedgerId]: next },
            converted_rounding_updated_at: Date.now(),
          },
        })
        await refreshProfile()
      } catch (err) {
        toast.error(localizeError(err, t))
      } finally {
        setSaving(false)
      }
    },
    [activeLedgerId, saving, token, appearance, refreshProfile, toast, t],
  )

  return { mode, setMode, saving }
}
