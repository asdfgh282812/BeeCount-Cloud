import { Bell } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type NotificationItem,
} from '@beecount/api-client'
import {
  Badge,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
  useT,
} from '@beecount/ui'

import { useAuth } from '../context/AuthContext'

const POLL_INTERVAL_MS = 60_000

/**
 * 通知中心入口(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0 web UI)。
 *
 * 跟其余 header 图标不同 —— notifications 是 user-global 的普通 REST 资源,
 * **不**进 `sync_changes`,没有 WS 推送(server 端注释已确认),所以这里靠
 * 定时轮询 + "打开面板时立即刷新" 两条路径拿新数据,不接 useSyncRefresh。
 */
export function NotificationBell() {
  const t = useT()
  const { token, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<NotificationItem[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      const resp = await fetchNotifications(token, { limit: 20 })
      setItems(resp.items)
      setUnreadCount(resp.unread_count)
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        logout()
        return
      }
      // 轮询失败静默 —— 不打断用户,下一轮再试。
    }
  }, [token, logout])

  useEffect(() => {
    if (!token) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [token, refresh])

  const handleOpenChange = (next: boolean) => {
    setOpen(next)
    if (next) void refresh()
  }

  const handleMarkRead = async (item: NotificationItem) => {
    if (item.read_at) return
    setItems((prev) =>
      prev.map((row) => (row.id === item.id ? { ...row, read_at: new Date().toISOString() } : row)),
    )
    setUnreadCount((prev) => Math.max(0, prev - 1))
    try {
      await markNotificationRead(token, item.id)
    } catch {
      void refresh()
    }
  }

  const handleMarkAllRead = async () => {
    if (unreadCount === 0 || loading) return
    setLoading(true)
    try {
      await markAllNotificationsRead(token)
      setItems((prev) => prev.map((row) => ({ ...row, read_at: row.read_at || new Date().toISOString() })))
      setUnreadCount(0)
    } catch {
      void refresh()
    } finally {
      setLoading(false)
    }
  }

  return (
    <DropdownMenu open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={t('notifications.title')}
          aria-label={t('notifications.title')}
          className="relative flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-primary/15 hover:text-primary"
        >
          <Bell className="h-4 w-4" />
          {unreadCount > 0 ? (
            <span className="absolute right-0.5 top-0.5 flex h-4 min-w-[16px] items-center justify-center rounded-full bg-destructive px-1 text-[9px] font-semibold leading-none text-destructive-foreground">
              {unreadCount > 99 ? '99+' : unreadCount}
            </span>
          ) : null}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 rounded-xl border-border/60 bg-card/95 p-0">
        <div className="flex items-center justify-between px-3 py-2.5">
          <span className="text-[13px] font-semibold">{t('notifications.title')}</span>
          <button
            type="button"
            disabled={unreadCount === 0 || loading}
            onClick={() => void handleMarkAllRead()}
            className="text-[11px] text-primary transition-opacity hover:opacity-70 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {t('notifications.markAllRead')}
          </button>
        </div>
        <div className="mx-2 h-px bg-border/60" />
        <div className="max-h-96 overflow-y-auto p-1.5">
          {items.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              {t('notifications.empty')}
            </p>
          ) : (
            items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => void handleMarkRead(item)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent/40 ${
                  item.read_at ? '' : 'bg-primary/5'
                }`}
              >
                <span className="flex w-full items-center gap-1.5">
                  {!item.read_at ? (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
                  ) : null}
                  <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium">
                    {item.title}
                  </span>
                </span>
                {item.body ? (
                  <span className="w-full truncate text-[11px] text-muted-foreground">
                    {item.body}
                  </span>
                ) : null}
                <span className="text-[10px] text-muted-foreground/70">
                  {formatRelativeTime(item.created_at, t)}
                </span>
              </button>
            ))
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function formatRelativeTime(iso: string, t: (key: string, params?: Record<string, string | number>) => string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diffMs = Date.now() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return t('notifications.time.justNow')
  if (diffMin < 60) return t('notifications.time.minutesAgo', { n: diffMin })
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return t('notifications.time.hoursAgo', { n: diffHour })
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 30) return t('notifications.time.daysAgo', { n: diffDay })
  return d.toLocaleDateString()
}
