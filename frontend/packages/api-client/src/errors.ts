export class ApiError extends Error {
  status: number
  code?: string
  latestChangeId?: number
  latestServerTimestamp?: string | null
  /** server 端附加的调试 raw payload(目前给 AI parse 错误暴露 LLM 原始输出)。 */
  raw?: string

  constructor(
    message: string,
    options: {
      status: number
      code?: string
      latestChangeId?: number
      latestServerTimestamp?: string | null
      raw?: string
    }
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status
    this.code = options.code
    this.latestChangeId = options.latestChangeId
    this.latestServerTimestamp = options.latestServerTimestamp
    this.raw = options.raw
  }
}

// ---------------------------------------------------------------------------
// 授權失效(HTTP 402 / LICENSE_REQUIRED)全域通知
// ---------------------------------------------------------------------------
//
// 跟 http.ts 的 401 → logoutFn 同一個思路:授權在使用中途過期/被撤銷時,
// server 對所有需登入的端點回 402,不可能要求每個呼叫點各自檢查。所有非 2xx
// 回應(authedGet/Post/... 的 parseResponse,以及 attachments/import/ai 這些
// 自己 fetch 的呼叫點)最後都會走到 `extractApiError`,所以偵測放在這裡一處
// 就涵蓋全部。監聽者由 `configureHttp({ onLicenseRequired })` 設定,app 程式
// 碼不要直接呼叫 `setLicenseRequiredListener`。

let licenseRequiredListener: (() => void) | null = null

/** @internal 由 `configureHttp` 設定;傳 null 解除。 */
export function setLicenseRequiredListener(fn: (() => void) | null): void {
  licenseRequiredListener = fn
}

/** 通知 app「授權已失效」。除了 402 回應之外,WS 被 server 以 4402 關閉時
 *  也會呼叫(useSyncSocket),讓閒置中、沒有 REST 請求的分頁也能切回閘門。 */
export function notifyLicenseRequired(): void {
  licenseRequiredListener?.()
}

export const LICENSE_REQUIRED_CODE = 'LICENSE_REQUIRED'

export async function extractApiError(res: Response): Promise<ApiError> {
  const err = await buildApiError(res)
  // 402 在這個 server 只用在授權門檻(error_handling.py 把 402 對應到
  // LICENSE_REQUIRED),code 缺漏時也當成授權失效處理。
  if (res.status === 402 || err.code === LICENSE_REQUIRED_CODE) {
    notifyLicenseRequired()
  }
  return err
}

async function buildApiError(res: Response): Promise<ApiError> {
  const text = await res.text()
  if (!text) {
    return new ApiError(`HTTP ${res.status}`, { status: res.status })
  }

  let message = text
  let code: string | undefined
  let latestChangeId: number | undefined
  let latestServerTimestamp: string | null | undefined
  let raw: string | undefined

  try {
    const json = JSON.parse(text) as any
    // server 端 error_handling.py 把 HTTPException 的 detail dict 展开到顶层,
    // 包括我们的 `error_code` / `raw`。优先用顶层的 error_code(更有语义),
    // fallback 到 error.code(generic)
    const maybeCode = json?.error_code || json?.error?.code
    const maybeMessage = json?.error?.message || json?.detail
    const maybeLatestChangeId = json?.latest_change_id
    const maybeLatestServerTimestamp = json?.latest_server_timestamp
    const maybeRaw = json?.raw  // AI parse 失败时 server 附加 LLM 原始输出

    if (typeof maybeCode === 'string' && maybeCode) code = maybeCode
    if (maybeMessage) message = String(maybeMessage)
    if (typeof maybeLatestChangeId === 'number') latestChangeId = maybeLatestChangeId
    if (typeof maybeLatestServerTimestamp === 'string' || maybeLatestServerTimestamp === null) {
      latestServerTimestamp = maybeLatestServerTimestamp
    }
    if (typeof maybeRaw === 'string') raw = maybeRaw
  } catch {
    // keep plain text fallback
  }

  const resolvedMessage = code ? `[${code}] ${message}` : message
  return new ApiError(resolvedMessage, {
    status: res.status,
    code,
    latestChangeId,
    latestServerTimestamp,
    raw
  })
}
