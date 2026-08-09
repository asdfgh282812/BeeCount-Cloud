import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { parseSsoCallbackFragment } from '@beecount/api-client'
import { useT } from '@beecount/ui'

type SsoCallbackPageProps = {
  onLoggedIn: (token: string) => void
}

/**
 * `/auth/sso/callback` 换完 token 后,后端会 302 到这里,把 token 交在 URL
 * fragment(`#access_token=...`)里 —— fragment 不送 server、不进 access
 * log,是浏览器导页交接 token 的惯用安全作法。这个页面只做一件事:读
 * fragment、落地 session、把浏览器地址栏的 token 抹掉、跳去目标深链。
 */
export function SsoCallbackPage({ onLoggedIn }: SsoCallbackPageProps) {
  const navigate = useNavigate()
  const t = useT()
  const [failed, setFailed] = useState(false)
  // React 18 StrictMode 在开发模式会把 effect 跑两次 —— 用 ref 保证 token
  // 只被消费一次,第二次进来 fragment 已经被 replaceState 清空,不会重复处理。
  const handled = useRef(false)

  useEffect(() => {
    if (handled.current) return
    handled.current = true

    const result = parseSsoCallbackFragment(window.location.hash)
    window.history.replaceState(null, '', window.location.pathname)

    if (!result) {
      setFailed(true)
      return
    }
    onLoggedIn(result.accessToken)
    navigate(result.redirectPath, { replace: true })
  }, [navigate, onLoggedIn])

  if (failed) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-4 text-center text-foreground">
        <p className="text-sm text-muted-foreground">{t('login.sso.callbackError')}</p>
        <a className="text-sm font-medium text-primary underline" href="/login">
          {t('login.sso.backToLogin')}
        </a>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-muted border-t-primary" />
    </div>
  )
}
