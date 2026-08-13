import { authedGet } from '@beecount/api-client'

const POLL_INTERVAL_MS = 30_000

export interface SyncChangeEnvelope {
  change_id: number
  ledger_id: string
  entity_type: string
  entity_sync_id: string
  action: string
  payload: unknown
  updated_at: string
  updated_by_device_id?: string | null
}

export interface SyncPullResponse {
  changes: SyncChangeEnvelope[]
  server_cursor: number
  has_more: boolean
}

function cursorKey(userId: string): string {
  return `beecount.sync.cursor.${userId}`
}

export function loadCursor(userId: string): number {
  if (typeof window === 'undefined') return 0
  const raw = window.localStorage.getItem(cursorKey(userId))
  const parsed = raw ? Number.parseInt(raw, 10) : 0
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

export function saveCursor(userId: string, cursor: number): void {
  if (typeof window === 'undefined') return
  if (!Number.isFinite(cursor) || cursor <= 0) return
  const current = loadCursor(userId)
  if (cursor <= current) return
  window.localStorage.setItem(cursorKey(userId), String(cursor))
}

export function clearCursor(userId: string): void {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(cursorKey(userId))
}

/**
 * Fetch all changes since the caller's last-known cursor, draining the
 * ``has_more`` flag so callers don't have to loop manually. Returns the
 * final server cursor (already persisted) and total change count.
 */
export async function drainPull(
  token: string,
  userId: string,
  deviceId: string | null
): Promise<{ changes: SyncChangeEnvelope[]; cursor: number }> {
  let cursor = loadCursor(userId)
  const collected: SyncChangeEnvelope[] = []
  // Cap the drain loop so a misbehaving server can't spin us forever.
  for (let iteration = 0; iteration < 50; iteration += 1) {
    const params = new URLSearchParams()
    params.set('since', String(cursor))
    params.set('limit', '1000')
    if (deviceId) params.set('device_id', deviceId)
    let body: SyncPullResponse
    try {
      // 之前这里是裸 fetch,token 过期后会永远带着同一个过期 token 重试且
      // 401 静默吞掉,WS reconnect 闭包也不会跟着换新 token(见 connection
      // rejected 日志里同一个 jti 被重放几小时)。改走 authedGet 让 401 触发
      // http.ts 的单飞 refresh,新 token 会 setState 回 App.tsx 并带动 WS
      // reconnect。
      body = await authedGet<SyncPullResponse>(`/sync/pull?${params.toString()}`, token)
    } catch (_) {
      // Non-OK / network error 停止本轮 drain;调用方决定要不要重试。
      break
    }
    if (Array.isArray(body.changes) && body.changes.length > 0) {
      collected.push(...body.changes)
    }
    if (typeof body.server_cursor === 'number' && body.server_cursor > cursor) {
      cursor = body.server_cursor
      saveCursor(userId, cursor)
    }
    if (!body.has_more) break
  }
  return { changes: collected, cursor }
}

export interface PollerControls {
  stop: () => void
  /** Force an immediate drain; honors in-flight ticks via a single-flight lock. */
  tickNow: () => Promise<void>
}

/**
 * Background polling fallback. Runs every POLL_INTERVAL_MS while the tab is
 * visible and a token is set. When WebSocket delivery is alive, this layer
 * just no-ops past the server's since-filter; when WS is down, it catches up.
 */
export function startPoller(params: {
  token: string
  userId: string
  deviceId: string | null
  onChanges: (changes: SyncChangeEnvelope[]) => void
}): PollerControls {
  let stopped = false
  let inflight = false
  async function tick() {
    if (stopped || inflight) return
    if (typeof document !== 'undefined' && document.hidden) return
    inflight = true
    try {
      const { changes } = await drainPull(params.token, params.userId, params.deviceId)
      if (changes.length > 0 && !stopped) {
        params.onChanges(changes)
      }
    } catch (_) {
      // Swallow network errors — next tick will retry.
    } finally {
      inflight = false
    }
  }

  const interval = setInterval(tick, POLL_INTERVAL_MS)
  // Run once on start so a freshly-mounted page picks up anything missed.
  void tick()

  return {
    stop: () => {
      stopped = true
      clearInterval(interval)
    },
    tickNow: () => tick()
  }
}
