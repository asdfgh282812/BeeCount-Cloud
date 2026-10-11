import type { ReadDepositBalance } from '@beecount/api-client'

import type { TxForm } from '../forms'

/**
 * 訂金尾款追蹤(doc.moze.app/record/deposit)的純邏輯:日期編碼、排序、
 * 逾期判斷、交易 ⇄ 尾款反查。UI 元件(DepositBalancesPanel / 交易表單 /
 * 交易詳情)共用,避免各自手寫一份。
 */

/** 純日期 `YYYY-MM-DD` → 送 server 的 `YYYY-MM-DDT00:00:00Z`;空字串 → null。
 *  `due_at` 是純日期語意(同 debt due_at / 回饋規則起訖日),不可用 `toISOString()`
 *  轉本地時間,UTC+8 會少一天。 */
export function depositDateToWire(date: string | null | undefined): string | null {
  const v = (date || '').trim()
  return v ? `${v}T00:00:00Z` : null
}

/** server 回來的 `due_at`(可能缺時區位移)→ `YYYY-MM-DD`(UTC 年月日);null/空 → ''。
 *  缺位移標記時補 "Z" 強制當 UTC,避免 UTC+8 使用者多/少一天。 */
export function depositDateFromWire(iso: string | null | undefined): string {
  if (!iso) return ''
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  const d = new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

/** 今天(瀏覽器本地日期)的 `YYYY-MM-DD`,跟 `depositDateFromWire` 的純日期字串比較用。 */
export function localTodayDateString(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/** 輸入框字串 → 金額;空字串 / 非正數 / NaN → null(= 未定)。 */
export function parseDepositAmount(raw: string | null | undefined): number | null {
  const text = (raw ?? '').toString().trim()
  if (!text) return null
  const n = Number(text)
  return Number.isFinite(n) && n > 0 ? n : null
}

/** 只有 pending 且有預計日期、日期早於今天(本地日期)才算逾期。 */
export function isDepositBalanceOverdue(
  b: Pick<ReadDepositBalance, 'status' | 'due_at'>,
  todayDate: string = localTodayDateString()
): boolean {
  if (b.status !== 'pending') return false
  const due = depositDateFromWire(b.due_at)
  return Boolean(due) && due < todayDate
}

/** 待補尾款排序:有日期的依日期由近到遠(逾期自然排最前),未定日期排最後;
 *  同日期依訂金交易時間新到舊,最後以 id 穩定排序。 */
export function sortPendingDepositBalances(
  list: readonly ReadDepositBalance[]
): ReadDepositBalance[] {
  return [...list].sort((a, b) => {
    const da = depositDateFromWire(a.due_at)
    const db = depositDateFromWire(b.due_at)
    if (da && !db) return -1
    if (!da && db) return 1
    if (da !== db) return da < db ? -1 : 1
    const ta = a.source_happened_at ? new Date(a.source_happened_at).getTime() : 0
    const tb = b.source_happened_at ? new Date(b.source_happened_at).getTime() : 0
    if (ta !== tb) return tb - ta
    return a.id.localeCompare(b.id)
  })
}

/** 已補上清單排序:訂金交易時間新到舊。 */
export function sortCompletedDepositBalances(
  list: readonly ReadDepositBalance[]
): ReadDepositBalance[] {
  return [...list].sort((a, b) => {
    const ta = a.source_happened_at ? new Date(a.source_happened_at).getTime() : 0
    const tb = b.source_happened_at ? new Date(b.source_happened_at).getTime() : 0
    return tb - ta || a.id.localeCompare(b.id)
  })
}

/** 交易總計 = 訂金 + 尾款(尾款未定視為 0)。 */
export function depositBalanceTotal(b: ReadDepositBalance): number {
  return (b.source_amount ?? 0) + (b.amount ?? 0)
}

export type DepositBalanceLink =
  | { role: 'source'; balance: ReadDepositBalance }
  | { role: 'completed'; balance: ReadDepositBalance }

/** 反查某筆交易在尾款追蹤裡的角色:訂金交易(source_tx_id 命中)或已補上的
 *  尾款交易(completed_tx_id 命中);都不是 → null。 */
export function findDepositBalanceLink(
  list: readonly ReadDepositBalance[],
  txId: string | null | undefined
): DepositBalanceLink | null {
  if (!txId) return null
  const asSource = list.find((b) => b.source_tx_id === txId)
  if (asSource) return { role: 'source', balance: asSource }
  const asCompleted = list.find((b) => b.completed_tx_id === txId)
  if (asCompleted) return { role: 'completed', balance: asCompleted }
  return null
}

// ───────────── 交易表單(TxForm)⇄ 尾款 ─────────────

/** 「金額|日期」快照,存檔時比對有沒有改過。 */
export function depositOriginalKey(amount: string, dueAt: string): string {
  return `${amount.trim()}|${dueAt.trim()}`
}

/** 開編輯表單時,依這筆交易的尾款反查結果組出要覆蓋進 TxForm 的欄位。
 *  - 訂金交易(source):帶出尾款金額/日期/狀態,pending 時預設開關為開。
 *  - 已補上的尾款交易(completed):本身不能再掛尾款 → blocked。
 *  - 查不到:不改。 */
export function depositFormPatchFromLink(
  link: DepositBalanceLink | null
): Partial<TxForm> {
  if (!link) return {}
  if (link.role === 'completed') return { deposit_blocked: true }
  const b = link.balance
  const amount = b.amount != null ? String(b.amount) : ''
  const due = depositDateFromWire(b.due_at)
  return {
    deposit_enabled: true,
    deposit_amount: amount,
    deposit_due_at: due,
    deposit_balance_id: b.id,
    deposit_status: b.status,
    deposit_original: depositOriginalKey(amount, due)
  }
}

/** 這個表單狀態下,交易是否「應該帶一筆待補尾款」。
 *  expense/transfer 才有;週期性/分期(新建時勾選或編輯既有產生交易)不行;
 *  已補上的不再由表單管理。 */
export function isDepositFormActive(
  form: Pick<
    TxForm,
    | 'deposit_enabled'
    | 'deposit_status'
    | 'deposit_blocked'
    | 'recurring_enabled'
    | 'installment_enabled'
    | 'tx_type'
  >
): boolean {
  return (
    form.deposit_enabled &&
    form.deposit_status !== 'completed' &&
    !form.deposit_blocked &&
    !form.recurring_enabled &&
    !form.installment_enabled &&
    (form.tx_type === 'expense' || form.tx_type === 'transfer')
  )
}

/** 存檔前驗證:有填尾款金額就必須是正數(留空 = 未定)。回傳 i18n key 或 null。 */
export function validateDepositForm(
  form: Pick<TxForm, 'deposit_amount'> &
    Parameters<typeof isDepositFormActive>[0]
): string | null {
  if (!isDepositFormActive(form)) return null
  const raw = form.deposit_amount.trim()
  if (raw && parseDepositAmount(raw) === null) return 'depositBalance.error.amountInvalid'
  return null
}
