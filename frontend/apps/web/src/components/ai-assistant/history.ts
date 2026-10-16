import type { TxDraft, AssistantHistory } from '@beecount/api-client'

export type Message = {
  id: string; role: 'user' | 'assistant'; content: string
  status: 'done' | 'pending' | 'error' | 'stopped'
  drafts?: TxDraft[]; savedIds?: string[]; errorCode?: string; query?: string; readOnly?: boolean
}

export function loadHistory(key: string): Message[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(key) || '[]')
    if (!Array.isArray(rows)) return []
    return rows.filter((m): m is Message => Boolean(m) && typeof m.id === 'string' &&
      ['user', 'assistant'].includes(m.role) && typeof m.content === 'string' &&
      ['done', 'pending', 'error', 'stopped'].includes(m.status))
      .slice(-80).map(m => ({ ...m, content: m.content.slice(0, 24000), status: m.status === 'pending' ? 'stopped' : m.status }))
  } catch { return [] }
}

export function memoryHistory(messages: Message[]): AssistantHistory[] {
  return messages.filter(m => m.status === 'done' && m.content && !m.drafts)
    .slice(-8).map(m => ({ role: m.role, content: m.content.slice(0, 6000) }))
}

export function persistHistory(key: string, messages: Message[]) {
  try { localStorage.setItem(key, JSON.stringify(messages.slice(-80))) } catch { /* private mode / quota */ }
}
