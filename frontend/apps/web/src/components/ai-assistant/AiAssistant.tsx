import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Brain, Check, Copy, Maximize2, Minimize2, Minus, RefreshCw, Settings2, Sparkles, Square, Trash2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button, Dialog, DialogContent, DialogHeader, DialogTitle, useLocale } from '@beecount/ui'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { TxDraftList } from '../cmdk-ai/TxDraftList'
import { assistantCopy } from './copy'
import { MessageText } from './MessageText'
import { useAssistant } from './useAssistant'
import './assistant.css'

export function AiAssistant() {
  const { sessionUserId } = useAuth()
  return <AssistantPanel key={sessionUserId} />
}

function AssistantPanel() {
  const { locale } = useLocale()
  const copy = assistantCopy(locale)
  const chat = useAssistant()
  const { activeLedgerId, currentLedger, refreshLedgers } = useLedgers()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [instant, setInstant] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [hint, setHint] = useState(true)
  const [input, setInput] = useState('')
  const [group, setGroup] = useState(0)
  const [confirm, setConfirm] = useState<'memory' | 'clear' | null>(null)
  const [reviewId, setReviewId] = useState<string | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const [atBottom, setAtBottom] = useState(true)
  const launcher = useRef<HTMLButtonElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLElement>(null)
  const stickToBottom = useRef(true)
  const review = chat.messages.find(m => m.id === reviewId)

  useEffect(() => {
    setInput(''); setReviewId(null); setGroup(0)
    stickToBottom.current = true
  }, [activeLedgerId])
  useEffect(() => {
    if (open && window.matchMedia('(min-width: 768px)').matches) textarea.current?.focus({ preventScroll: true })
  }, [open])
  useEffect(() => {
    if (!open || !window.visualViewport) return
    const viewport = window.visualViewport
    const resize = () => {
      panel.current?.style.setProperty('--assistant-viewport-height', `${viewport.height}px`)
      panel.current?.style.setProperty('--assistant-viewport-top', `${viewport.offsetTop}px`)
    }
    resize()
    viewport.addEventListener('resize', resize)
    viewport.addEventListener('scroll', resize)
    return () => { viewport.removeEventListener('resize', resize); viewport.removeEventListener('scroll', resize) }
  }, [open])
  useEffect(() => {
    if (open && stickToBottom.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [chat.messages, open])
  useEffect(() => {
    if (textarea.current) {
      textarea.current.style.height = 'auto'
      textarea.current.style.height = `${Math.min(textarea.current.scrollHeight, 130)}px`
    }
  }, [input, open])

  const close = (immediate = false) => { setInstant(immediate); setOpen(false); launcher.current?.focus({ preventScroll: true }) }
  const show = (immediate: boolean) => { setInstant(immediate); setOpen(true); setHint(false) }
  const submit = (text = input, readOnly = false) => {
    if (!text.trim() || chat.busy || !activeLedgerId) return
    setInput('')
    stickToBottom.current = true
    void chat.send(text, readOnly)
    textarea.current?.focus()
  }
  const errorText = (code?: string) => code === 'AI_DISABLED' ? copy.disabled : code === 'AI_NO_CHAT_PROVIDER' ? copy.noProvider : code === 'AI_QUERY_INVALID' ? copy.queryError : code === 'AI_STREAM_INTERRUPTED' ? copy.interrupted : copy.error
  const avatar = <span className="assistant-avatar"><img src="/branding/logo.svg" alt="" /><Sparkles className="h-3 w-3" aria-hidden="true" /></span>
  const suggestions = copy.queries.slice((group % 3) * 3, (group % 3) * 3 + 3)

  return <>
    <div className="assistant-launcher-wrap">
      {!open && hint && <div className="assistant-hint bc-glass-panel">
        <button type="button" onClick={event => show(event.detail === 0)} className="text-left">
          <strong className="block text-primary-strong">{copy.title}</strong><span>{copy.hint}</span>
        </button>
        <button type="button" className="assistant-icon-button" aria-label={copy.dismiss} onClick={() => setHint(false)}><X size={14} /></button>
      </div>}
      <button ref={launcher} type="button" className="assistant-launcher bc-glass-panel" aria-label={open ? copy.minimize : copy.open}
        data-instant={instant} aria-expanded={open} aria-controls="ai-assistant-panel" onClick={event => { if (open) close(event.detail === 0); else show(event.detail === 0) }}>
        <span className="assistant-launcher-art" aria-hidden="true">{avatar}</span>
        <span className="assistant-launcher-close" aria-hidden="true"><Minus size={25} /></span>
        {!open && chat.busy && <span className="assistant-busy-dot" />}
      </button>
    </div>
    <section ref={panel} id="ai-assistant-panel" role="dialog" aria-modal="false" aria-labelledby="ai-assistant-title"
      data-open={open} data-instant={instant} aria-hidden={!open} {...(!open ? { inert: '' } : {})}
      className={`assistant-panel bc-glass-panel ${expanded ? 'assistant-expanded' : ''}`}
      onKeyDown={event => {
        if (event.key === 'Escape' && !confirm && !reviewId) { event.stopPropagation(); close(true) }
      }}>
      <header className="assistant-header">
        {avatar}
        <div className="min-w-0 flex-1"><h2 id="ai-assistant-title" className="text-lg font-semibold">{copy.title}</h2>
          <p className="truncate text-xs text-muted-foreground" title={currentLedger?.ledger_name}>{currentLedger?.ledger_name || copy.subtitle}</p></div>
        <button type="button" className="assistant-icon-button" title={copy.memory} aria-label={copy.memory} aria-pressed={chat.memory}
          onClick={() => chat.memory ? chat.setMemoryEnabled(false) : setConfirm('memory')}><Brain size={19} /></button>
        <button type="button" className="assistant-icon-button" title={copy.clear} aria-label={copy.clear} disabled={!chat.messages.length || chat.busy} onClick={() => setConfirm('clear')}><Trash2 size={18} /></button>
        <button type="button" className="assistant-icon-button assistant-expand-button" title={expanded ? copy.restore : copy.expand} aria-label={expanded ? copy.restore : copy.expand} onClick={() => setExpanded(!expanded)}>{expanded ? <Minimize2 size={18} /> : <Maximize2 size={18} />}</button>
        <button type="button" className="assistant-icon-button" aria-label={copy.minimize} title={copy.minimize} onClick={event => close(event.detail === 0)}><Minus size={21} /></button>
      </header>
      <div ref={scroll} className="assistant-messages" onScroll={() => {
        const el = scroll.current!
        stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 70
        setAtBottom(stickToBottom.current)
      }}>
        {!chat.messages.length && <div className="assistant-welcome">
          <div className="assistant-welcome-icon">{avatar}</div>
          <h3>{copy.greeting}</h3><p>{copy.intro}</p>
          <div className="assistant-starters">{copy.queries.slice(0, 3).map(q => <button key={q} type="button" className="bc-glass-control" disabled={!activeLedgerId} onClick={() => submit(q, true)}>{q}<ArrowUp size={14} /></button>)}</div>
        </div>}
        {chat.messages.map(message => <div key={message.id} className={`assistant-message ${message.role === 'user' ? 'assistant-user' : 'assistant-reply'}`}>
          {message.role === 'assistant' && <div className="assistant-message-label"><Sparkles size={12} /><span>{copy.title}</span></div>}
          <div className="assistant-bubble">
            {message.content && <MessageText text={message.content} />}
            {message.status === 'pending' && <div className="assistant-thinking" role="status"><span className="assistant-dots"><i /><i /><i /></span>{copy[chat.stage]}</div>}
            {message.status === 'error' && <div role="alert" className="mt-2 text-sm text-destructive">{errorText(message.errorCode)}</div>}
            {message.status === 'stopped' && <p className="mt-2 text-sm text-muted-foreground">{message.errorCode === 'AI_STREAM_INTERRUPTED' ? copy.interrupted : copy.stopped}</p>}
            {!!message.drafts?.length && <div className="assistant-draft-card">
              {message.drafts.map((draft, i) => <div key={i} className="flex min-w-0 items-center justify-between gap-3 border-b border-border/30 py-2 last:border-0"><span className="min-w-0 truncate">{draft.note || draft.category_name || draft.type}</span><strong className="shrink-0 tabular-nums">{currentLedger?.currency} {draft.amount.toLocaleString(locale)}</strong></div>)}
              {message.savedIds ? <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-sm"><span className="flex items-center gap-1 text-primary-strong"><Check size={15} />{copy.saved}</span><button type="button" className="underline" onClick={() => { close(); navigate('/app/transactions') }}>{copy.view}</button></div> : <Button className="mt-3 w-full" size="sm" disabled={chat.busy || message.status !== 'done'} onClick={() => setReviewId(message.id)}>{copy.review} · {message.drafts.length}</Button>}
            </div>}
            {message.status === 'error' && ['AI_NO_CHAT_PROVIDER', 'AI_DISABLED'].includes(message.errorCode || '') && <Button size="sm" variant="outline" className="mt-3" onClick={() => { close(); navigate('/app/settings/ai') }}>{copy.settings}</Button>}
          </div>
          {message.role === 'assistant' && message.status !== 'pending' && <div className="assistant-message-actions">
            {message.content && <button type="button" title={copy.copy} aria-label={copy.copy} onClick={() => {
              void navigator.clipboard.writeText(message.content).then(() => setCopied(message.id)).catch(() => {})
            }}>{copied === message.id ? <Check size={13} /> : <Copy size={13} />}{copied === message.id ? copy.copied : copy.copy}</button>}
            {(message.status === 'error' || message.status === 'stopped') && <button type="button" disabled={chat.busy} onClick={() => void chat.send(message.query || '', message.readOnly, message.id)}><RefreshCw size={13} />{copy.retry}</button>}
          </div>}
        </div>)}
      </div>
      {!atBottom && <button type="button" className="assistant-scroll-bottom bc-glass-panel" aria-label={locale === 'en' ? 'Scroll to latest message' : '回到最新訊息'} onClick={() => { stickToBottom.current = true; scroll.current?.scrollTo({ top: scroll.current.scrollHeight }); setAtBottom(true) }}><ArrowDown size={18} /></button>}
      <footer className="assistant-footer">
        {chat.memory && <p className="assistant-memory"><Brain size={14} />{copy.memoryOn}</p>}
        <div className="assistant-suggestions" aria-label={copy.suggestions}>
          <div>{suggestions.map(q => <button type="button" key={q} disabled={chat.busy || !activeLedgerId} className="bc-glass-control" onClick={() => submit(q, true)}>{q}</button>)}</div>
          <button type="button" className="assistant-icon-button" aria-label={copy.switchGroup} title={copy.switchGroup} onClick={() => setGroup(group + 1)}><RefreshCw size={17} /></button>
        </div>
        {!activeLedgerId && <p className="mb-2 text-sm text-muted-foreground">{copy.noLedger}</p>}
        <form className="assistant-composer" onSubmit={event => { event.preventDefault(); submit() }}>
          <button type="button" className="assistant-icon-button" title={copy.settings} aria-label={copy.settings} onClick={() => { close(); navigate('/app/settings/ai') }}><Settings2 size={19} /></button>
          <textarea ref={textarea} value={input} rows={1} maxLength={5000} disabled={!activeLedgerId} aria-label={copy.placeholder} placeholder={copy.placeholder} onChange={event => setInput(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); submit() } }} />
          {chat.busy ? <button type="button" className="assistant-send" aria-label={copy.stop} title={copy.stop} onClick={chat.stop}><Square size={17} /></button> : <button type="submit" className="assistant-send" disabled={!input.trim() || !activeLedgerId} aria-label={copy.send} title={copy.send}><ArrowUp size={23} /></button>}
        </form>
        <p className="assistant-footnote">{copy.local}</p>
      </footer>
    </section>
    <Dialog open={confirm !== null} onOpenChange={value => { if (!value) setConfirm(null) }}>
      <DialogContent><DialogHeader><DialogTitle>{confirm === 'memory' ? copy.memoryTitle : copy.clearTitle}</DialogTitle></DialogHeader>
        <p className="text-sm leading-relaxed text-muted-foreground">{confirm === 'memory' ? copy.memoryBody : copy.clearBody}</p>
        <div className="flex justify-end gap-2"><Button variant="outline" onClick={() => setConfirm(null)}>{copy.cancel}</Button><Button onClick={() => { if (confirm === 'memory') chat.setMemoryEnabled(true); else chat.clear(); setConfirm(null) }}>{confirm === 'memory' ? copy.enable : copy.clear}</Button></div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!review && !!activeLedgerId} onOpenChange={value => { if (!value) setReviewId(null) }}>
      <DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{copy.draftTitle}</DialogTitle></DialogHeader>
        {review?.drafts && activeLedgerId && <TxDraftList key={review.id} drafts={review.drafts} ledgerId={activeLedgerId} extraTagName={locale === 'en' ? 'AI chat' : 'AI 對話'} locale={locale}
          onSaved={(ids, drafts) => { chat.markSaved(review.id, ids, drafts); setReviewId(null); void refreshLedgers() }} onCancel={() => setReviewId(null)} />}
      </DialogContent>
    </Dialog>
  </>
}
