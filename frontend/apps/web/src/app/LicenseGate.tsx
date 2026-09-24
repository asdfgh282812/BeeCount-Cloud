import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, fetchLicenseStatus, type LicenseStatus } from '@beecount/api-client'
import { useT } from '@beecount/ui'

import { LicenseProvider, type LicenseContextValue } from '../context/LicenseContext'
import { LicenseGatePage } from '../pages/LicenseGatePage'
import { jwtUserId } from '../state/jwt'

interface Props {
  token: string
  onLogout: () => void
  /**
   * App.tsx 在收到 `configureHttp({ onLicenseRequired })` 通知(任何 API 回
   * 402 / LICENSE_REQUIRED,或 WS 被 4402 關閉)時 +1。數值本身沒意義,只用
   * 「有變化」觸發閘門切回金鑰輸入頁。
   */
  lapseSignal: number
  children: ReactNode
}

type Phase = 'loading' | 'ok' | 'gate'

/**
 * 授權閘門(docs/LICENSE_KEYS.md)—— 夾在 `RequireAuth` 跟 `AppShell` 之間:
 * 登入後先 GET /license/status,licensed=false 就整頁換成金鑰輸入頁,完全
 * 不掛載 AppShell(否則 AppShell 的 profile/ledgers/WS 會打出一堆 402)。
 * 管理者由 server 回 licensed=true,不會看到閘門。
 *
 * 這層只是 UX,真正的權威是 server 端的 402 —— 所以 status 查詢本身失敗
 * (網路錯誤、舊版 server 沒有這支端點)時一律放行,由後續請求的 402 再把
 * 畫面切回來。
 *
 * 只以 user id 當重新查詢的依據,不以 token 字串:token refresh 會換新字串,
 * 若跟著重查會讓 phase 閃回 loading、整個 AppShell 被卸載重建。
 */
export function LicenseGate({ token, onLogout, lapseSignal, children }: Props) {
  const t = useT()
  const userId = useMemo(() => jwtUserId(token), [token])
  const tokenRef = useRef(token)
  tokenRef.current = token

  const [status, setStatus] = useState<LicenseStatus | null>(null)
  const [phase, setPhase] = useState<Phase>('loading')
  // 換使用者時舊請求晚回來不能蓋掉新使用者的狀態。
  const requestSeqRef = useRef(0)

  const load = useCallback(async () => {
    const seq = ++requestSeqRef.current
    try {
      const next = await fetchLicenseStatus(tokenRef.current)
      if (seq !== requestSeqRef.current) return
      setStatus(next)
      setPhase(next.licensed ? 'ok' : 'gate')
    } catch (err) {
      if (seq !== requestSeqRef.current) return
      // 401 已由 http 層處理(refresh 重送,或全域登出)。
      if (err instanceof ApiError && err.status === 401) return
      // eslint-disable-next-line no-console
      console.warn('[LicenseGate] status check failed, failing open', err)
      setPhase((prev) => (prev === 'loading' ? 'ok' : prev))
    }
  }, [])

  useEffect(() => {
    if (!userId) return
    setStatus(null)
    setPhase('loading')
    void load()
  }, [userId, load])

  // 使用中途授權失效:立刻切回閘門,再重查一次拿到期日給閘門頁顯示。
  // 以 mount 當下的值為基準,換使用者重新掛載時不會被上一個 session 的
  // 計數誤觸發。
  // 一次失效通常會同時有好幾個請求回 402(AppShell/頁面平行載入),每個都
  // 讓計數 +1;重查進行中就不再重複打 /license/status。
  const seenLapseRef = useRef(lapseSignal)
  const lapseReloadInFlightRef = useRef(false)
  useEffect(() => {
    if (lapseSignal === seenLapseRef.current) return
    seenLapseRef.current = lapseSignal
    setPhase('gate')
    if (lapseReloadInFlightRef.current) return
    lapseReloadInFlightRef.current = true
    void load().finally(() => {
      lapseReloadInFlightRef.current = false
    })
  }, [lapseSignal, load])

  const applyStatus = useCallback((next: LicenseStatus) => {
    requestSeqRef.current += 1
    setStatus(next)
    setPhase(next.licensed ? 'ok' : 'gate')
  }, [])

  const contextValue = useMemo<LicenseContextValue>(
    () => ({ status, applyStatus, refresh: load }),
    [status, applyStatus, load],
  )

  if (phase === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-foreground">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-muted border-t-primary" />
          {t('license.gate.checking')}
        </div>
      </div>
    )
  }

  if (phase === 'gate') {
    return (
      <LicenseGatePage token={token} status={status} onActivated={applyStatus} onLogout={onLogout} />
    )
  }

  return <LicenseProvider value={contextValue}>{children}</LicenseProvider>
}
