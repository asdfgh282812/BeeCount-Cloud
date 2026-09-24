/**
 * 授權金鑰相關的小工具(docs/LICENSE_KEYS.md),給授權閘門、設定頁授權卡片、
 * 管理者金鑰頁共用。
 */

/**
 * 解析 server 回來的時間。SQLite 存的是不帶時區的 naive datetime(實際是
 * UTC),回應會變成 `2026-09-24T21:51:19` 這種沒有 `Z` 的字串,直接丟給
 * `new Date()` 會被當成本地時間,台灣時區會差 8 小時 —— 沒有時區標記時一律
 * 補 `Z` 當 UTC。
 */
export function parseServerDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso.trim())
  const d = new Date(hasZone ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

/** ISO 時間 → 使用者本地時區的 `YYYY-MM-DD`;null/無法解析回傳空字串。 */
export function formatLicenseDate(iso: string | null | undefined): string {
  const d = parseServerDate(iso)
  if (!d) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** ISO 時間 → 本地 `YYYY-MM-DD HH:mm`,給管理表格的啟用/建立時間欄位。 */
export function formatLicenseDateTime(iso: string | null | undefined): string {
  const d = parseServerDate(iso)
  if (!d) return ''
  const date = formatLicenseDate(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 到期日已經過了(以 server_time 為準,沒有才退回本機時間)。 */
export function isLicenseExpired(expiresAt: string | null | undefined, serverTime?: string | null): boolean {
  const exp = parseServerDate(expiresAt)
  if (!exp) return false
  const now = parseServerDate(serverTime)?.getTime() ?? Date.now()
  return exp.getTime() <= now
}
