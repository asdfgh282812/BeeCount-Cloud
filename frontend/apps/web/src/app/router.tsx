import { type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

interface RequireAuthProps {
  isAuthed: boolean
  children: ReactNode
}

/**
 * 未登录的 /app/* 深链会 replace 到 /login,原始 location(含 querystring)
 * 存进 `state.from`。登录成功后 `App.tsx::onLoggedIn` 优先导回 `state.from`,
 * 没有才 fallback `/app/overview`(docs/PH15_SWIPESMART_QUICKADD_SD.md §3.6)。
 */
export function RequireAuth({ isAuthed, children }: RequireAuthProps) {
  const location = useLocation()
  if (!isAuthed) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }
  return <>{children}</>
}
