import { authedGet, authedPost } from './http'
import type { LicenseStatus } from './types'

/**
 * 使用者自己的授權狀態(docs/LICENSE_KEYS.md)。`/license/*` 在 server 端的
 * 授權豁免清單裡,沒有授權時也能呼叫 —— web 端的授權閘門靠這支決定要不要
 * 擋在 AppShell 前面。
 */
export async function fetchLicenseStatus(token: string): Promise<LicenseStatus> {
  return authedGet<LicenseStatus>('/license/status', token)
}

/**
 * 輸入金鑰啟用 / 延長授權。格式 `BC-XXXXX-XXXXX-XXXXX-XXXXX`,server 容忍
 * 小寫、空白、少了連字號。錯誤碼:LICENSE_KEY_INVALID(400)、
 * LICENSE_KEY_NOT_FOUND(404)、LICENSE_KEY_ALREADY_REDEEMED(409)、
 * LICENSE_KEY_REVOKED(410)、RATE_LIMITED(429)。
 */
export async function activateLicense(token: string, key: string): Promise<LicenseStatus> {
  return authedPost<LicenseStatus>('/license/activate', token, { key })
}
