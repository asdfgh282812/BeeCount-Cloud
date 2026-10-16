import { useEffect, useRef, useState } from 'react'
import { ApiError, streamAssistant, type TxDraft } from '@beecount/api-client'
import { useLocale } from '@beecount/ui'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { assistantCopy } from './copy'
import { loadHistory, memoryHistory, persistHistory, type Message } from './history'

export function useAssistant() {
  const { token, sessionUserId } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { locale } = useLocale()
  const copy = assistantCopy(locale)
  const key = `beecount.assistant.${sessionUserId}.${activeLedgerId}`
  const memoryKey = `beecount.assistant.memory.${sessionUserId}`
  const [messages, setMessages] = useState<Message[]>(() => loadHistory(key))
  const [memory, setMemory] = useState(() => { try { return localStorage.getItem(memoryKey) === 'true' } catch { return false } })
  const [busy, setBusy] = useState(false)
  const [stage, setStage] = useState<'thinking' | 'querying' | 'drafting'>('thinking')
  const controller = useRef<AbortController | null>(null)
  const currentKey = useRef(key)
  const rows = useRef(messages)

  // Synchronous key guard means a late chunk cannot enter a different ledger.
  currentKey.current = key
  useEffect(() => {
    controller.current?.abort()
    controller.current = null
    const loaded = loadHistory(key)
    rows.current = loaded
    setMessages(loaded)
    setBusy(false)
    return () => { controller.current?.abort() }
  }, [key])

  const update = (transform: (previous: Message[]) => Message[]) => {
    rows.current = transform(rows.current).slice(-80)
    setMessages(rows.current)
    persistHistory(key, rows.current)
  }
  const setMemoryEnabled = (enabled: boolean) => {
    setMemory(enabled)
    try { localStorage.setItem(memoryKey, String(enabled)) } catch { /* storage unavailable */ }
  }
  const stop = () => { controller.current?.abort() }
  const clear = () => {
    controller.current?.abort()
    controller.current = null
    setBusy(false)
    update(() => [])
  }
  const send = async (text: string, readOnly = false, retryId?: string) => {
    const query = text.trim()
    if (!query || query.length > 5000 || !activeLedgerId || controller.current) return
    const requestKey = key
    const abort = new AbortController()
    controller.current = abort
    const id = retryId || crypto.randomUUID()
    const before = retryId ? rows.current.slice(0, Math.max(0, rows.current.findIndex(m => m.id === retryId) - 1)) : rows.current
    const history = memory ? memoryHistory(before) : []
    const response: Message = { id, role: 'assistant', content: '', status: 'pending', query, readOnly }
    update(previous => retryId ? previous.map(m => m.id === retryId ? response : m) : [
      ...previous, { id: crypto.randomUUID(), role: 'user', content: query, status: 'done' }, response,
    ])
    setBusy(true)
    setStage('thinking')
    const patch = (change: Partial<Message> | ((m: Message) => Partial<Message>)) => {
      if (currentKey.current === requestKey && controller.current === abort) {
        update(previous => previous.map(m => m.id === id ? { ...m, ...(typeof change === 'function' ? change(m) : change) } : m))
      }
    }
    const timeout = window.setTimeout(() => abort.abort('timeout'), 180000)
    try {
      for await (const event of streamAssistant(token, { query, ledger_id: activeLedgerId, locale, use_memory: memory, history, read_only: readOnly }, abort.signal)) {
        if (abort.signal.aborted || currentKey.current !== requestKey) break
        if (event.type === 'status') setStage(event.stage)
        if (event.type === 'chunk') patch(m => ({ content: m.content + event.text }))
        if (event.type === 'drafts') patch({ drafts: event.items, content: event.items.length ? copy.draftReady : copy.emptyDraft })
        if (event.type === 'done') patch({ status: 'done' })
        if (event.type === 'error') patch({ status: 'error', errorCode: event.error_code })
      }
    } catch (error) {
      patch({ status: abort.signal.aborted ? 'stopped' : 'error', errorCode: abort.signal.reason === 'timeout' ? 'AI_STREAM_INTERRUPTED' : error instanceof ApiError ? error.code : 'AI_CHAT_FAILED' })
    } finally {
      window.clearTimeout(timeout)
      if (controller.current === abort) {
        patch(m => m.status === 'pending' ? { status: 'stopped' } : {})
        controller.current = null
        setBusy(false)
      }
    }
  }
  return { messages, busy, stage, memory, setMemoryEnabled, send, stop, clear,
    markSaved: (id: string, ids: string[], drafts: TxDraft[]) => {
      const saved = (previous: Message[]) => previous.map(m => m.id === id ? { ...m, savedIds: ids, drafts } : m)
      // A save may finish after its dialog closes and the active ledger changes.
      if (currentKey.current === key) update(saved)
      else persistHistory(key, saved(loadHistory(key)))
    },
  }
}
