import { extractApiError } from './errors'

export const API_BASE = (import.meta as any).env?.VITE_API_BASE_URL || '/api/v1'

function resolveApiBaseUrl(): string | null {
  const normalized = `${API_BASE || ''}`.trim()
  if (!normalized) return null
  try {
    return new URL(normalized).toString()
  } catch (_) {
    if (typeof window === 'undefined') return null
    try {
      return new URL(normalized, window.location.origin).toString()
    } catch (_) {
      return null
    }
  }
}

export function resolveApiUrl(value?: string | null): string | null {
  const normalized = `${value || ''}`.trim()
  if (!normalized) return null
  try {
    return new URL(normalized).toString()
  } catch (_) {
    const base = resolveApiBaseUrl()
    if (!base) return normalized
    try {
      return new URL(normalized, base).toString()
    } catch (_) {
      return normalized
    }
  }
}

// ---------------------------------------------------------------------------
// Auth token coordination
// ---------------------------------------------------------------------------
//
// Without a global 401 handler every caller has to remember to check for
// ``status === 401`` and trigger a logout. In practice they don't, which means
// one expired token mid-session leaves the UI half-alive: reads fail silently,
// writes succeed until the next refresh. This module centralizes the retry:
// call sites keep passing the old token; if the server rejects it, we do a
// single-flight refresh here and replay the request transparently.

type RefreshFn = () => Promise<string>
type LogoutFn = () => void

let refreshFn: RefreshFn | null = null
let logoutFn: LogoutFn | null = null
let refreshInFlight: Promise<string> | null = null

/**
 * Wire the http layer to app-level auth callbacks. Call once after login
 * succeeds; no-op safe to call repeatedly.
 */
export function configureHttp(opts: { refreshToken?: RefreshFn | null; onLogout?: LogoutFn | null }): void {
  refreshFn = opts.refreshToken ?? null
  logoutFn = opts.onLogout ?? null
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw await extractApiError(res)
  }
  // 204 No Content 或 Content-Length: 0 的响应 (DELETE 撤销/删除 PAT 这种)
  // 没有 body,直接 `res.json()` 会抛 `Unexpected end of JSON input`。返
  // `undefined as T` —— 调用方签名是 `Promise<void>` 时 OK,期望 JSON
  // 的调用方本来就不会发出 204 请求。
  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T
  }
  return res.json()
}

/**
 * 公开 GET(无 Authorization header),目前用于 /version 这种不敏感且
 * 未登录也应该能打到的端点。
 */
export async function publicGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' }
  })
  return parseResponse<T>(res)
}

export type BeeCountCloudVersion = {
  name: string
  version: string
}

export async function fetchCloudVersion(): Promise<BeeCountCloudVersion> {
  return publicGet<BeeCountCloudVersion>('/version')
}

/** 取当前浏览器 localStorage 存的 device_id(login 时落盘)。服务端鉴权中间件
 *  根据这个 header bump Device.last_seen_at,让"设备页最近活跃时间"真实反映
 *  web 操作而非"上次登录时间"。延迟 require 防止 auth.ts / http.ts 循环依赖。*/
function currentDeviceId(): string | null {
  if (typeof window === 'undefined') return null
  try {
    // 跟 auth.ts 的 DEVICE_ID_KEY 同名同义,复制避免循环 import
    return window.localStorage.getItem(`beecount.web.device_id.${API_BASE}`)
  } catch {
    return null
  }
}

function authHeaders(token: string, idempotencyKey?: string): Record<string, string> {
  const out: Record<string, string> = {
    Authorization: `Bearer ${token}`
  }
  if (idempotencyKey) out['Idempotency-Key'] = idempotencyKey
  const deviceId = currentDeviceId()
  if (deviceId) out['X-Device-ID'] = deviceId
  return out
}

async function doRefresh(): Promise<string> {
  if (!refreshFn) throw new Error('no refresh configured')
  if (!refreshInFlight) {
    refreshInFlight = refreshFn().finally(() => {
      refreshInFlight = null
    })
  }
  return refreshInFlight
}

/** JWT payload 的 `exp`(秒)。解析失败(格式非法等)时返回 null —— 调用方
 *  应把这当成"不知道是否过期",不要当成"已过期"处理。*/
function decodeTokenExp(token: string): number | null {
  const parts = token.split('.')
  if (parts.length !== 3) return null
  try {
    const json = atob(parts[1].replace(/-/g, '+').replace(/_/g, '/'))
    const payload = JSON.parse(json)
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch (_) {
    return null
  }
}

/** 由 `ensureFreshToken` 在"token 已过期且 refresh 也救不回来"时抛出——
 *  调用方(WS 重连 supervisor)看到这个应该立刻停手,不要再拿着这个已知
 *  死掉的 token 排 backoff 重试;`ensureFreshToken` 在抛出前已经调用过
 *  `logoutFn`,全局登出/跳转登录页已经在路上了。 */
export class AuthExhaustedError extends Error {
  constructor() {
    super('token expired and refresh failed')
    this.name = 'AuthExhaustedError'
  }
}

/**
 * 长连接(WebSocket)重连前的主动 token 保鲜检查。
 *
 * 背景:REST 请求靠 `authedFetch` 收到 401 才触发刷新,但轮询(startPoller)
 * 在分页隐藏(document.hidden)时整个跳过 tick,不会发任何请求 —— 这种情况下
 * 分页在背景放上一小时以上,token 早过期了却没有任何 REST 调用去触发刷新。
 * WS supervisor 的重连 timer 不受页面可见性影响(参见 useSyncSocket.ts),
 * 会一直拿着这个"没人去刷新"的过期 token 反复重连,永远 403/1008。
 *
 * 在每次实际发起 WS 连接前调用这个函数:token 还没过期(留 10s 容错)就原样
 * 返回;已经过期或临近过期,就借用跟 REST 401 相同的单飞 refreshFn 主动刷新
 * 一次,新 token 会 setState 回 App 层,WS 用到的 token prop 跟着更新。
 * 无法判断新鲜度(decode 失败)或没配置 refreshFn(还没登录成功前的窗口期)
 * 时原样返回旧 token,交给连接失败后的既有 backoff 重试逻辑兜底。
 *
 * 但如果 token 确实过期、refreshFn 也配置了、refresh 本身却失败(refresh
 * token 同样过期/失效,或多次拿着这个 refresh token 都换不回新 access
 * token)—— 这时候不该再假装"再试一次也许会好",跟 `authedFetch` 的 401
 * 处理路径一致,直接调用 `logoutFn` 触发全局登出、清空本地会话、导回登录
 * 页,并抛出 `AuthExhaustedError` 让调用方知道不用再排队重连了。
 */
export async function ensureFreshToken(token: string): Promise<string> {
  const exp = decodeTokenExp(token)
  if (exp === null) return token
  const nowSec = Date.now() / 1000
  if (exp - nowSec > 10) return token
  if (!refreshFn) return token
  try {
    return await doRefresh()
  } catch (_) {
    logoutFn?.()
    throw new AuthExhaustedError()
  }
}

type FetchMaker = (token: string) => Promise<Response>

/**
 * Perform an authed fetch with transparent single-flight token refresh on 401.
 * Callers provide a factory that builds the request given the current token
 * string so we can replay the call with a refreshed token.
 */
async function authedFetch(makeRequest: FetchMaker, token: string): Promise<Response> {
  const res = await makeRequest(token)
  if (res.status !== 401) return res
  // Drain the body so we don't leak the connection on node/fetch implementations.
  try {
    await res.text()
  } catch (_) {
    // ignore
  }
  if (!refreshFn) {
    // No refresh path configured — surface 401 so caller logs out explicitly.
    logoutFn?.()
    return res
  }
  try {
    const fresh = await doRefresh()
    return await makeRequest(fresh)
  } catch (_) {
    logoutFn?.()
    return res
  }
}

export async function authedGet<T>(path: string, token: string): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        headers: authHeaders(tok),
        // 数据是事件日志 + 最新快照驱动的，任何缓存命中都会让 refresh-after-write
        // 看到上一份数据。显式拒绝，避免浏览器/中间 CDN 给同路径返回旧响应。
        cache: 'no-store'
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedPost<T>(
  path: string,
  token: string,
  body: unknown,
  idempotencyKey?: string
): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'POST',
        headers: {
          ...authHeaders(tok, idempotencyKey),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedPatch<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'PATCH',
        headers: {
          ...authHeaders(tok),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedPut<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'PUT',
        headers: {
          ...authHeaders(tok),
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      }),
    token
  )
  return parseResponse<T>(res)
}

export async function authedDelete<T>(path: string, token: string, body?: unknown): Promise<T> {
  const hasBody = typeof body !== 'undefined'
  const res = await authedFetch(
    (tok) =>
      fetch(`${API_BASE}${path}`, {
        method: 'DELETE',
        headers: hasBody
          ? {
              ...authHeaders(tok),
              'Content-Type': 'application/json'
            }
          : authHeaders(tok),
        body: hasBody ? JSON.stringify(body) : undefined
      }),
    token
  )
  return parseResponse<T>(res)
}
