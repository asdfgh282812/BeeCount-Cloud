/**
 * 通知中心 API client(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0）。
 *
 * user-global，非 sync 实体 —— 不带 base_change_id / WriteCommitMeta，走普通
 * REST。跟 pats.ts 是同一种"简单资源 CRUD"模式。server 没有 WS 推送这类事件
 * (`src/routers/notifications.py` 头部注释确认),前端需要自己轮询。
 */
import { authedGet, authedPost } from './http'
import type { NotificationListResponse, NotificationItem } from './types'

export async function fetchNotifications(
  token: string,
  options?: { limit?: number; offset?: number; category?: string; unreadOnly?: boolean }
): Promise<NotificationListResponse> {
  const query = new URLSearchParams()
  if (options?.limit) query.set('limit', `${options.limit}`)
  if (options?.offset) query.set('offset', `${options.offset}`)
  if (options?.category) query.set('category', options.category)
  if (options?.unreadOnly) query.set('unread_only', 'true')
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return authedGet<NotificationListResponse>(`/notifications${suffix}`, token)
}

export async function markNotificationRead(
  token: string,
  notificationId: number
): Promise<NotificationItem> {
  return authedPost<NotificationItem>(`/notifications/${notificationId}/read`, token, undefined)
}

export async function markAllNotificationsRead(token: string): Promise<{ updated: number }> {
  return authedPost<{ updated: number }>('/notifications/read-all', token, undefined)
}
