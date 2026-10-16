import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamAssistant } from '@beecount/api-client'
import { loadHistory, memoryHistory, persistHistory, type Message } from './history'

afterEach(() => vi.unstubAllGlobals())

describe('assistant stream', () => {
  const request = { query: '早餐', ledger_id: 'a', locale: 'zh-TW', use_memory: false, history: [], read_only: true }
  function response(text: string, terminal = true) {
    const bytes = new TextEncoder().encode(text + (terminal ? 'data: {"type":"done"}\r\n\r\n' : ''))
    return new Response(new ReadableStream({ start(controller) {
      // One byte at a time splits both UTF-8 and CRLF event boundaries.
      bytes.forEach(byte => controller.enqueue(new Uint8Array([byte])))
      controller.close()
    } }))
  }
  it('handles split UTF-8 and CRLF and forwards cancellation', async () => {
    const fetcher = vi.fn().mockResolvedValue(response('data: {"type":"chunk","text":"繁體中文"}\r\n\r\n'))
    vi.stubGlobal('fetch', fetcher)
    const signal = new AbortController().signal
    const result = []
    for await (const event of streamAssistant('token', request, signal)) result.push(event)
    expect(result).toEqual([{ type: 'chunk', text: '繁體中文' }, { type: 'done' }])
    expect(fetcher.mock.calls[0][1].signal).toBe(signal)
    expect(JSON.parse(fetcher.mock.calls[0][1].body).read_only).toBe(true)
  })
  it('reports a truncated response instead of silently succeeding', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('data: {"type":"chunk","text":"partial"}\n\n', false)))
    const consume = async () => { for await (const event of streamAssistant('token', request, new AbortController().signal)) void event }
    await expect(consume()).rejects.toMatchObject({ code: 'AI_STREAM_INTERRUPTED' })
  })
  it('ends on an error event without requiring done', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response('data: {"type":"error","error_code":"AI_PROVIDER_ERROR","message":"Unavailable"}\n\n', false)))
    const result = []
    for await (const event of streamAssistant('token', request, new AbortController().signal)) result.push(event)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('error')
  })
})

describe('assistant local history', () => {
  const row = (id: string, status: Message['status'] = 'done'): Message => ({ id, role: 'assistant', content: id, status })
  it('limits memory to eight completed text turns and excludes drafts/errors', () => {
    const messages = Array.from({ length: 12 }, (_, i) => row(String(i)))
    messages.push(row('pending', 'pending'), row('error', 'error'), { ...row('draft'), drafts: [] })
    expect(memoryHistory(messages).map(m => m.content)).toEqual(['4', '5', '6', '7', '8', '9', '10', '11'])
  })
  it('isolates account/ledger keys and restores unfinished replies as stopped', () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('localStorage', { setItem: (key: string, value: string) => storage.set(key, value), getItem: (key: string) => storage.get(key) })
    persistHistory('userA.ledgerA', [row('a', 'pending')])
    persistHistory('userA.ledgerB', [row('b')])
    expect(loadHistory('userB.ledgerA')).toEqual([])
    expect(loadHistory('userA.ledgerA')[0].status).toBe('stopped')
    expect(loadHistory('userA.ledgerB')[0].content).toBe('b')
  })
  it('tolerates corrupted or unavailable storage', () => {
    vi.stubGlobal('localStorage', { getItem: () => '{broken', setItem: () => { throw new Error('quota') } })
    expect(loadHistory('x')).toEqual([])
    expect(() => persistHistory('x', [row('a')])).not.toThrow()
  })
})
