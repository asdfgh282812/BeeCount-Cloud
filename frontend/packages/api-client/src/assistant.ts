import { API_BASE } from './http'
import { ApiError, extractApiError } from './errors'
import type { TxDraft } from './ai'

export type AssistantHistory = { role: 'user' | 'assistant'; content: string }
export type AssistantEvent =
  | { type: 'status'; stage: 'thinking' | 'querying' | 'drafting' }
  | { type: 'chunk'; text: string }
  | { type: 'drafts'; items: TxDraft[] }
  | { type: 'done' }
  | { type: 'error'; error_code: string; message: string }

export async function* streamAssistant(
  token: string,
  request: { query: string; ledger_id: string; locale: string; use_memory: boolean; history: AssistantHistory[]; read_only: boolean },
  signal: AbortSignal,
): AsyncGenerator<AssistantEvent> {
  const response = await fetch(`${API_BASE}/ai/chat`, {
    method: 'POST', signal,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(request),
  })
  if (!response.ok) throw await extractApiError(response)
  if (!response.body) throw new ApiError('Missing response', { status: response.status, code: 'AI_NO_BODY' })
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { value, done } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      let match: RegExpExecArray | null
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const raw = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        const data = raw.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
        if (!data) continue
        const event = JSON.parse(data) as AssistantEvent
        yield event
        if (event.type === 'done' || event.type === 'error') return
      }
      if (done) throw new ApiError('Stream interrupted', { status: response.status, code: 'AI_STREAM_INTERRUPTED' })
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
