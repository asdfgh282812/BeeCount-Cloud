/**
 * 账本数据导入客户端 —— 跟 .docs/web-ledger-import.md 对齐。
 *
 * 4 个端点:
 *  - uploadImport(file, ?ledgerId) —— multipart 上传 + 解析,返 token + summary
 *  - previewImport(token, opts)    —— 重 preview,改 mapping/账本/dedup 等
 *  - executeImport(token)          —— SSE 流,生成 stage / complete / error 事件
 *  - cancelImport(token)           —— DELETE 取消
 */
import { API_BASE, authedDelete, authedPost } from './http'
import { ApiError, extractApiError } from './errors'

export type ImportSourceFormat = 'beecount' | 'alipay' | 'wechat' | 'generic'

export type ImportFieldMapping = {
  /** v30 多币种:币种列(可选,值须像 ISO code)。 */
  currency?: string | null
  tx_type: string | null
  amount: string | null
  happened_at: string | null
  /** 一级分类(顶级 bucket) */
  category_name: string | null
  /** 二级分类(具体 leaf,可选) */
  subcategory_name: string | null
  account_name: string | null
  from_account_name: string | null
  to_account_name: string | null
  note: string | null
  tags: string[]
  datetime_format: string | null
  strip_currency_symbols: boolean
  expense_is_negative: boolean
  /**
   * 客户端本地时区相对 UTC 的分钟偏移(东为正,UTC+8 = 480)。CSV 里的时间是
   * 用户本地墙钟,后端据此换算成 UTC 存储,避免被当作 UTC 整体偏移(issue #314)。
   * 由 api-client 在 preview 时自动注入 -new Date().getTimezoneOffset(),UI 无需关心。
   */
  tz_offset_minutes?: number | null
}

export type ImportEntityDiff = {
  new_names: string[]
  matched_names: string[]
}

export type ImportStats = {
  total_rows: number
  time_range_start: string | null
  time_range_end: string | null
  total_signed_amount: string
  by_type: {
    expense_count: number
    expense_total: string
    income_count: number
    income_total: string
    transfer_count: number
  }
  accounts: ImportEntityDiff
  categories: ImportEntityDiff
  tags: ImportEntityDiff
  skipped_dedup: number
  parse_errors: Array<{
    code: string
    row_number: number
    message: string
    field_name: string | null
  }>
  parse_errors_total: number
  parse_warnings: Array<{
    code: string
    row_number: number
    message: string
  }>
  parse_warnings_total: number
}

export type ImportTransactionSample = {
  tx_type: 'expense' | 'income' | 'transfer'
  amount: string
  happened_at: string
  note: string | null
  category_name: string | null
  parent_category_name: string | null
  account_name: string | null
  from_account_name: string | null
  to_account_name: string | null
  tag_names: string[]
  source_row_number: number
}

export type ImportSummary = {
  import_token: string
  expires_at: string
  source_format: ImportSourceFormat
  headers: string[]
  suggested_mapping: ImportFieldMapping
  current_mapping: ImportFieldMapping
  target_ledger_id: string | null
  dedup_strategy: 'skip_duplicates' | 'insert_all'
  auto_tag_names: string[]
  stats: ImportStats
  sample_rows: Array<Record<string, string>>
  sample_transactions: ImportTransactionSample[]
}

/** SSE 事件类型 */
export type ImportSseEvent =
  | { event: 'stage'; data: { stage: 'accounts' | 'categories' | 'tags' | 'transactions'; done: number; total: number; skipped?: number } }
  | { event: 'complete'; data: { created_tx_count: number; skipped_count: number; new_change_id: number } }
  | {
      event: 'error'
      data: {
        code: string
        row_number: number
        field_name: string | null
        message: string
        raw_line?: string
        total_errors?: number
      }
    }

// ──────────────── upload ────────────────

export async function uploadImport(
  token: string,
  options: { file: File; targetLedgerId?: string | null },
): Promise<ImportSummary> {
  const form = new FormData()
  form.append('file', options.file)
  if (options.targetLedgerId) form.append('target_ledger_id', options.targetLedgerId)
  // CSV/Excel 里的时间是用户本地墙钟,带上浏览器时区偏移(东为正,UTC+8 = 480),
  // 让后端从上传起就正确换算成 UTC,sample/preview/execute 全程一致(issue #314)。
  form.append('tz_offset_minutes', String(-new Date().getTimezoneOffset()))
  const response = await fetch(`${API_BASE}/import/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })
  if (!response.ok) throw await extractApiError(response)
  return (await response.json()) as ImportSummary
}

// ──────────────── preview ────────────────

export type PreviewImportOptions = {
  mapping?: ImportFieldMapping
  targetLedgerId?: string | null
  dedupStrategy?: 'skip_duplicates' | 'insert_all'
  autoTagNames?: string[]
}

export async function previewImport(
  token: string,
  importToken: string,
  options: PreviewImportOptions,
): Promise<ImportSummary> {
  const body: Record<string, unknown> = {}
  if (options.mapping) {
    // CSV 里的时间是用户本地墙钟,带上浏览器时区偏移(东为正,UTC+8 = 480),
    // 让后端正确换算成 UTC,避免导入后整体偏移(issue #314)。
    body.mapping = {
      ...options.mapping,
      tz_offset_minutes:
        options.mapping.tz_offset_minutes ?? -new Date().getTimezoneOffset(),
    }
  }
  if (options.targetLedgerId !== undefined) body.target_ledger_id = options.targetLedgerId
  if (options.dedupStrategy !== undefined) body.dedup_strategy = options.dedupStrategy
  if (options.autoTagNames !== undefined) body.auto_tag_names = options.autoTagNames
  return authedPost<ImportSummary>(
    `/import/${encodeURIComponent(importToken)}/preview`,
    token,
    body,
  )
}

// ──────────────── execute(SSE) ────────────────

/**
 * 执行导入,返回 async generator yielding SSE 事件。
 * 调用方:
 *   for await (const ev of streamExecuteImport(token, importToken)) { ... }
 *
 * stream 收到 `complete` 或 `error` 后自动结束。
 */
export async function* streamExecuteImport(
  token: string,
  importToken: string,
): AsyncGenerator<ImportSseEvent> {
  const response = await fetch(
    `${API_BASE}/import/${encodeURIComponent(importToken)}/execute`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      },
    },
  )
  if (!response.ok) throw await extractApiError(response)
  if (!response.body) {
    throw new ApiError('response body missing', { status: response.status, code: 'IMPORT_NO_BODY' })
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        let eventName = ''
        let dataPayload = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataPayload += line.slice(5).trim()
        }
        if (!eventName) continue
        try {
          const parsed = JSON.parse(dataPayload) as ImportSseEvent['data']
          const evt = { event: eventName, data: parsed } as ImportSseEvent
          yield evt
          if (eventName === 'complete' || eventName === 'error') return
        } catch {
          // 半截 chunk,跳过
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

// ──────────────── cancel ────────────────

export async function cancelImport(token: string, importToken: string): Promise<void> {
  await authedDelete(`/import/${encodeURIComponent(importToken)}`, token)
}

// ──────────────── 分類 / 帳戶匯入(2026-08 新增) ────────────────
//
// 跟交易匯入不同:範本格式固定,upload 直接回傳可執行的 preview(有效筆數 +
// 逐行錯誤),沒有欄位對應(field mapping)這層,少一個 preview 步驟。

export type SimpleImportEntityType = 'categories' | 'accounts'

export type SimpleImportRowError = {
  code: string
  row_number: number
  message: string
  field_name: string | null
}

export type SimpleImportSummary = {
  import_token: string
  entity_type: SimpleImportEntityType
  target_ledger_id: string
  total_rows: number
  valid_rows: number
  errors: SimpleImportRowError[]
  sample: Array<Record<string, unknown>>
}

export type SimpleImportSseEvent =
  | { event: 'stage'; data: { stage: string; done: number; total: number } }
  | { event: 'complete'; data: { created_count: number; new_change_id: number } }
  | { event: 'error'; data: { code: string; row_number: number; message: string } }

async function uploadSimpleImport(
  token: string,
  entityType: SimpleImportEntityType,
  options: { file: File; targetLedgerId: string },
): Promise<SimpleImportSummary> {
  const form = new FormData()
  form.append('file', options.file)
  form.append('target_ledger_id', options.targetLedgerId)
  const response = await fetch(`${API_BASE}/import/${entityType}/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })
  if (!response.ok) throw await extractApiError(response)
  return (await response.json()) as SimpleImportSummary
}

export function uploadCategoriesImport(
  token: string,
  options: { file: File; targetLedgerId: string },
): Promise<SimpleImportSummary> {
  return uploadSimpleImport(token, 'categories', options)
}

export function uploadAccountsImport(
  token: string,
  options: { file: File; targetLedgerId: string },
): Promise<SimpleImportSummary> {
  return uploadSimpleImport(token, 'accounts', options)
}

async function* streamExecuteSimpleImport(
  token: string,
  entityType: SimpleImportEntityType,
  importToken: string,
): AsyncGenerator<SimpleImportSseEvent> {
  const response = await fetch(
    `${API_BASE}/import/${entityType}/${encodeURIComponent(importToken)}/execute`,
    { method: 'POST', headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' } },
  )
  if (!response.ok) throw await extractApiError(response)
  if (!response.body) {
    throw new ApiError('response body missing', { status: response.status, code: 'IMPORT_NO_BODY' })
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sepIdx: number
      while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, sepIdx)
        buffer = buffer.slice(sepIdx + 2)
        let eventName = ''
        let dataPayload = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataPayload += line.slice(5).trim()
        }
        if (!eventName) continue
        try {
          const parsed = JSON.parse(dataPayload) as SimpleImportSseEvent['data']
          const evt = { event: eventName, data: parsed } as SimpleImportSseEvent
          yield evt
          if (eventName === 'complete' || eventName === 'error') return
        } catch {
          // 半截 chunk,跳过
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function streamExecuteCategoriesImport(
  token: string,
  importToken: string,
): AsyncGenerator<SimpleImportSseEvent> {
  return streamExecuteSimpleImport(token, 'categories', importToken)
}

export function streamExecuteAccountsImport(
  token: string,
  importToken: string,
): AsyncGenerator<SimpleImportSseEvent> {
  return streamExecuteSimpleImport(token, 'accounts', importToken)
}

export async function cancelSimpleImport(token: string, importToken: string): Promise<void> {
  await authedDelete(`/import/simple/${encodeURIComponent(importToken)}`, token)
}

// ──────────────── 範本下載(2026-08 新增) ────────────────

function parseSimpleFilenameFromDisposition(value: string): string | null {
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(value)
  if (star) {
    try {
      return decodeURIComponent(star[1].trim())
    } catch {
      // ignore,落 plain
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(value)
  return plain ? plain[1].trim() : null
}

export async function downloadImportTemplate(
  token: string,
  options: {
    entityType: 'transactions' | 'categories' | 'accounts'
    format: 'csv' | 'xlsx'
    lang?: string
  },
): Promise<void> {
  const query = new URLSearchParams()
  query.set('entity_type', options.entityType)
  query.set('format', options.format)
  if (options.lang) query.set('lang', options.lang)
  const response = await fetch(`${API_BASE}/import/template?${query.toString()}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!response.ok) throw await extractApiError(response)
  const disposition = response.headers.get('Content-Disposition') || ''
  const filename =
    parseSimpleFilenameFromDisposition(disposition) ||
    `beecount-${options.entityType}-template.${options.format}`
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}
