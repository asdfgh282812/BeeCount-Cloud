import { useEffect, useRef, useState } from 'react'

import { AuthExhaustedError, notifyLicenseRequired } from '@beecount/api-client'

export type SyncSocketStatus = 'idle' | 'connecting' | 'connected' | 'disconnected'

export interface UseSyncSocketOptions {
  /** JWT access token. Supervisor pauses while null. */
  token: string | null
  /** Base WebSocket URL builder. */
  buildUrl: (token: string) => string
  /** Fires when the server pushes a sync_change or backup_restore event. */
  onEvent?: (payload: unknown) => void
  /** Fires when the supervisor (re)connects — caller should pull-drain. */
  onOpen?: () => void
  /** Fires when the supervisor loses the socket and starts backing off. */
  onDisconnect?: () => void
  /**
   * Called with the current token immediately before every connect attempt;
   * return a (possibly refreshed) token to use for that attempt. Lets the
   * caller proactively refresh an expired token even when nothing else in
   * the app is making REST calls to trigger the usual 401-driven refresh
   * (e.g. a backgrounded tab, where the REST poller no-ops while hidden but
   * this reconnect loop keeps running). Defaults to a passthrough.
   */
  ensureFreshToken?: (token: string) => Promise<string>
}

export interface SyncSocketState {
  status: SyncSocketStatus
}

const HEARTBEAT_INTERVAL_MS = 25_000
const HEARTBEAT_TIMEOUT_MS = 45_000
const BACKOFF_BASE_MS = 500
const BACKOFF_MAX_MS = 30_000
/**
 * 連線要穩定撐過這麼久才把退避次數歸零。不能在 onopen 當下就歸零 —— server
 * 「接受後馬上關閉」(例如下面的 4001 踢線)時,每次重連都會退回 500ms,
 * 變成每秒好幾次的 connect + drainPull 迴圈(2026-09-25 dev 環境 24 小時打出
 * 74 萬次 /ws + 75 萬次 /sync/pull 的事故)。
 */
const STABLE_CONNECTION_MS = 30_000
/** server 同一使用者連線數超過上限時踢掉最舊一條用的 close code(src/websocket_manager.py)。 */
const WS_CLOSE_EVICTED = 4001
/** 被踢掉時至少等這麼久再連 —— 立刻重連只會回頭把另一條(可能是別的分頁/裝置)踢掉。 */
const EVICTED_RECONNECT_MS = 60_000

function backoffDelay(attempt: number): number {
  const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt)
  const jitter = Math.floor(Math.random() * 500)
  return exp + jitter
}

/**
 * Supervised WebSocket connection with exponential-backoff reconnect,
 * application-level heartbeat, and visibility/network resumption.
 *
 * Replaces the naive ``new WebSocket`` inline usage which leaks a dead socket
 * whenever the network blips, the tab sleeps, or a proxy kills idle
 * connections — at which point mobile ↔ web sync silently stops.
 *
 * 所有連線狀態(socket / 計時器 / 退避次數 / disposed 旗標)都是「每次 effect
 * 執行各自一份」的區域變數,不放在跨 run 共用的 ref 裡:token 變化會讓 effect
 * cleanup + 重跑,舊 run 若正 await 在 `ensureFreshToken` 上,用共用的
 * `destroyedRef` 判斷會看到新 run 剛設回的 false,照樣開出一條沒人追蹤、
 * 永遠不會被關掉的殭屍連線(而且它手上的是過期 token,每次重連都會再觸發一次
 * refresh → 再一次 effect 重跑 → 再多一條)。累積超過 server 每人 5 條的上限
 * 後,就會互相踢線、無限重連。
 */
export function useSyncSocket({
  token,
  buildUrl,
  onEvent,
  onOpen,
  onDisconnect,
  ensureFreshToken
}: UseSyncSocketOptions): SyncSocketState {
  const [status, setStatus] = useState<SyncSocketStatus>('idle')
  const handlersRef = useRef({ onEvent, onOpen, onDisconnect, ensureFreshToken })
  handlersRef.current = { onEvent, onOpen, onDisconnect, ensureFreshToken }

  useEffect(() => {
    if (!token) {
      setStatus('idle')
      return
    }

    let disposed = false
    let socket: WebSocket | null = null
    let connecting = false
    let attempt = 0
    let heartbeatInterval: ReturnType<typeof setInterval> | null = null
    let heartbeatTimeout: ReturnType<typeof setTimeout> | null = null
    let stableTimeout: ReturnType<typeof setTimeout> | null = null
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null

    function clearConnectionTimers() {
      if (heartbeatInterval) clearInterval(heartbeatInterval)
      if (heartbeatTimeout) clearTimeout(heartbeatTimeout)
      if (stableTimeout) clearTimeout(stableTimeout)
      heartbeatInterval = null
      heartbeatTimeout = null
      stableTimeout = null
    }

    function closeSocket() {
      const current = socket
      socket = null
      if (!current) return
      // 先拔掉 handler 再 close:主動關閉(心跳逾時 / cleanup)已經自己排好
      // 重連了,不能再讓 onclose 多排一次 —— 否則每次心跳逾時都會把重連
      // chain 複製成兩條。
      current.onopen = null
      current.onmessage = null
      current.onerror = null
      current.onclose = null
      try {
        current.close()
      } catch (_) {
        // Socket may already be in closing state — ignore.
      }
    }

    function scheduleReconnect(minDelay = 0) {
      if (disposed) return
      handlersRef.current.onDisconnect?.()
      setStatus('disconnected')
      const delay = Math.max(minDelay, backoffDelay(attempt))
      attempt += 1
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      reconnectTimeout = setTimeout(() => {
        reconnectTimeout = null
        void connect()
      }, delay)
    }

    function armHeartbeatTimeout() {
      if (heartbeatTimeout) clearTimeout(heartbeatTimeout)
      heartbeatTimeout = setTimeout(() => {
        // No frames received in time — assume dead socket, force reconnect.
        clearConnectionTimers()
        closeSocket()
        scheduleReconnect()
      }, HEARTBEAT_TIMEOUT_MS)
    }

    async function connect() {
      if (disposed || connecting || socket) return
      connecting = true
      try {
        setStatus('connecting')
        let tok = token!
        const ensure = handlersRef.current.ensureFreshToken
        if (ensure) {
          try {
            tok = await ensure(tok)
          } catch (err) {
            if (err instanceof AuthExhaustedError) {
              // Token is expired and refresh failed too — ensureFreshToken has
              // already triggered a global logout. Don't schedule another
              // reconnect with a token we know is dead; the token prop will
              // go null shortly and this effect's cleanup will tear itself down.
              return
            }
            // Any other failure: fall through with the original token —
            // connect failure below will still drive the normal backoff/retry path.
          }
          // A refresh propagates a new token prop, which disposes this run and
          // starts a fresh one — that one owns the connection from now on.
          if (disposed) return
        }
        let next: WebSocket
        try {
          next = new WebSocket(buildUrl(tok))
        } catch (_) {
          scheduleReconnect()
          return
        }
        socket = next

        next.onopen = () => {
          if (disposed || socket !== next) return
          setStatus('connected')
          handlersRef.current.onOpen?.()
          armHeartbeatTimeout()
          heartbeatInterval = setInterval(() => {
            if (next.readyState !== WebSocket.OPEN) return
            try {
              next.send(JSON.stringify({ type: 'ping' }))
            } catch (_) {
              // will trigger onclose
            }
          }, HEARTBEAT_INTERVAL_MS)
          stableTimeout = setTimeout(() => {
            stableTimeout = null
            attempt = 0
          }, STABLE_CONNECTION_MS)
        }

        next.onmessage = (event) => {
          if (disposed || socket !== next) return
          armHeartbeatTimeout()
          let payload: unknown = null
          try {
            payload = JSON.parse(event.data)
          } catch (_) {
            return
          }
          if (payload && typeof payload === 'object' && (payload as any).type === 'pong') {
            return
          }
          handlersRef.current.onEvent?.(payload)
        }

        next.onerror = () => {
          // onclose will follow; let that drive the backoff.
        }

        next.onclose = (event) => {
          if (disposed || socket !== next) return
          socket = null
          clearConnectionTimers()
          // server 以 4402 關閉 = 授權已失效(routers/ws.py)。通知 App 切回授權
          // 閘門 —— 閘門會卸載 AppShell,這個 supervisor 跟著 cleanup,不會
          // 真的一直拿著沒授權的 token 重連。
          if (event.code === 4402) notifyLicenseRequired()
          scheduleReconnect(event.code === WS_CLOSE_EVICTED ? EVICTED_RECONNECT_MS : 0)
        }
      } finally {
        connecting = false
      }
    }

    function handleOnline() {
      if (disposed || socket || connecting) return
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout)
        reconnectTimeout = null
      }
      attempt = 0
      void connect()
    }
    function handleVisibility() {
      if (typeof document === 'undefined') return
      if (document.hidden) return
      handleOnline()
      // If the socket is up, also trigger a pull to fill gaps created while tab was hidden.
      if (socket?.readyState === WebSocket.OPEN) {
        handlersRef.current.onOpen?.()
      }
    }

    window.addEventListener('online', handleOnline)
    document.addEventListener('visibilitychange', handleVisibility)
    void connect()

    return () => {
      disposed = true
      window.removeEventListener('online', handleOnline)
      document.removeEventListener('visibilitychange', handleVisibility)
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
      reconnectTimeout = null
      clearConnectionTimers()
      closeSocket()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, buildUrl])

  return { status }
}
