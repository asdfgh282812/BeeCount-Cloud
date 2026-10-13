import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type PointerEvent } from 'react'

import { cn } from '../lib/cn'

type Option<T extends string | number> = { value: T; label: ReactNode; disabled?: boolean; testId?: string; title?: string; ariaLabel?: string }
type Props<T extends string | number> = {
  options: Option<T>[]
  value: T
  onValueChange: (value: T) => void
  label: string
  className?: string
  semantics?: 'group' | 'tabs' | 'navigation'
}

// Critically damped spring with Apple's 0.3s response. Exact integration keeps
// interrupted movement continuous even when a frame arrives late.
function step(position: number, velocity: number, target: number, dt: number, ratio = 1) {
  const omega = 2 * Math.PI / .3
  const distance = position - target
  if (ratio < 1) {
    const damping = ratio * omega
    const frequency = omega * Math.sqrt(1 - ratio * ratio)
    const c = (velocity + damping * distance) / frequency
    const decay = Math.exp(-damping * dt)
    const cos = Math.cos(frequency * dt), sin = Math.sin(frequency * dt)
    const wave = distance * cos + c * sin
    return [target + decay * wave,
      decay * (-damping * wave - distance * frequency * sin + c * frequency * cos)] as const
  }
  const c = velocity + omega * distance
  const decay = Math.exp(-omega * dt)
  return [(distance + c * dt) * decay + target,
    (velocity - omega * c * dt) * decay] as const
}

export function GlassSegmented<T extends string | number>({ options, value, onValueChange, label, className, semantics = 'group' }: Props<T>) {
  const root = useRef<HTMLDivElement>(null)
  const thumb = useRef<HTMLSpanElement>(null)
  const optionKeys = JSON.stringify(options.map((option) => option.value))
  const selected = options.findIndex((o) => o.value === value)
  const controlledIndex = useRef(selected)
  controlledIndex.current = selected
  const [hover, setHover] = useState<number | null>(null)
  const state = useRef({ x: Math.max(0, selected), vx: 0, target: Math.max(0, selected), scale: 1, vs: 0, lift: 1 })
  const frame = useRef(0)
  const drag = useRef<null | { id: number; x: number; y: number; start: number; moved: boolean; lastX: number; time: number; velocity: number; index: number }>(null)
  const suppressClick = useRef(false)
  const instant = useRef(false)
  const count = useRef(options.length)
  count.current = options.length

  function geometry() {
    const bounds = root.current!.getBoundingClientRect()
    return { bounds, width: (root.current!.clientWidth - 6) / count.current,
      rtl: getComputedStyle(root.current!).direction === 'rtl' }
  }
  function reduced() {
    return document.documentElement.dataset.reduceMotion === 'true' ||
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
  }
  function paint() {
    if (!thumb.current || !root.current) return
    const { width, rtl } = geometry()
    const s = state.current
    const x = rtl ? count.current - 1 - s.x : s.x
    thumb.current.style.opacity = controlledIndex.current < 0 && !drag.current ? '0' : '1'
    thumb.current.style.width = `${width}px`
    thumb.current.style.transform = `translateX(${x * width}px) scale(${reduced() ? 1 : s.scale})`
  }
  function animate() {
    cancelAnimationFrame(frame.current)
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, .05)
      last = now
      const s = state.current
      if (!drag.current?.moved) {
        if (reduced() || instant.current) { s.x = s.target; s.vx = 0 }
        else [s.x, s.vx] = step(s.x, s.vx, s.target, dt)
      }
      if (reduced()) { s.scale = 1; s.vs = 0 }
      else [s.scale, s.vs] = step(s.scale, s.vs, s.lift, dt, s.lift === 1 ? .8 : 1)
      paint()
      if (Math.abs(s.x - s.target) + Math.abs(s.vx) + Math.abs(s.scale - s.lift) + Math.abs(s.vs) > .001) {
        frame.current = requestAnimationFrame(tick)
      }
    }
    frame.current = requestAnimationFrame(tick)
  }
  useLayoutEffect(() => {
    if (drag.current) {
      const id = drag.current.id
      drag.current = null
      state.current.lift = 1
      suppressClick.current = true
      setHover(null)
      if (root.current?.hasPointerCapture(id)) root.current.releasePointerCapture(id)
    }
    state.current.target = Math.max(0, selected)
    if (instant.current || reduced()) {
      state.current.x = Math.max(0, selected)
      state.current.vx = 0
      paint()
    }
    animate()
    instant.current = false
  }, [selected, optionKeys])
  useLayoutEffect(paint)
  useEffect(() => {
    const observer = new ResizeObserver(paint)
    observer.observe(root.current!)
    return () => { observer.disconnect(); cancelAnimationFrame(frame.current) }
  }, [])

  function indexAt(x: number) {
    const { bounds, width, rtl } = geometry()
    const index = Math.max(0, Math.min(count.current - 1, Math.floor((x - bounds.left - root.current!.clientLeft - 3) / width)))
    return rtl ? count.current - 1 - index : index
  }
  function isDisabled(index: number) {
    return !options[index] || options[index].disabled ||
      Boolean(root.current?.querySelectorAll('button')[index]?.matches(':disabled'))
  }
  function finish(event: PointerEvent<HTMLDivElement>, cancel = false) {
    const d = drag.current
    if (!d || d.id !== event.pointerId) return
    if (!d.moved && Math.abs(event.clientY - d.y) > 12) cancel = true
    drag.current = null
    state.current.lift = 1
    state.current.target = cancel || isDisabled(d.index) ? selected : d.index
    suppressClick.current = true
    setHover(null)
    animate()
    if (root.current?.hasPointerCapture(event.pointerId)) root.current.releasePointerCapture(event.pointerId)
    if (!cancel && !isDisabled(d.index) && d.index !== selected) {
      onValueChange(options[d.index].value)
      requestAnimationFrame(() => {
        if (root.current && !drag.current && controlledIndex.current !== d.index) {
          state.current.target = controlledIndex.current
          animate()
        }
      })
    }
  }

  return (
    <div ref={root} role={semantics === 'tabs' ? 'tablist' : 'group'} aria-label={label}
      className={cn('bc-glass-control relative flex h-10 w-full touch-pan-y select-none rounded-xl p-[3px]', className)}
      onPointerDown={(event) => {
        if (drag.current || !event.isPrimary || event.button !== 0) return
        suppressClick.current = false
        const index = indexAt(event.clientX)
        if (isDisabled(index)) return
        drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY,
          start: index === selected ? state.current.x : index, moved: false,
          lastX: event.clientX, time: event.timeStamp, velocity: 0, index }
        state.current.lift = 1.06
        root.current!.setPointerCapture(event.pointerId)
        animate()
      }}
      onPointerMove={(event) => {
        const d = drag.current
        if (!d || d.id !== event.pointerId) return
        const dx = event.clientX - d.x
        const dy = event.clientY - d.y
        if (!d.moved && Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { finish(event, true); return }
        if (!d.moved && Math.abs(dx) < 8) return
        d.moved = true
        const { width, rtl } = geometry()
        d.velocity = (event.clientX - d.lastX) / Math.max(1, event.timeStamp - d.time) * 1000
        d.lastX = event.clientX; d.time = event.timeStamp
        const s = state.current
        s.x = Math.max(0, Math.min(count.current - 1, d.start + dx / width * (rtl ? -1 : 1)))
        s.vx = d.velocity / width * (rtl ? -1 : 1)
        d.index = indexAt(event.clientX)
        s.target = s.x
        setHover(d.index)
        paint()
      }}
      onPointerUp={(event) => finish(event)}
      onPointerCancel={(event) => finish(event, true)}
      onLostPointerCapture={(event) => finish(event, true)}
      onClickCapture={(event) => { if (suppressClick.current && event.detail !== 0) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false } }}
    >
      <span ref={thumb} aria-hidden="true" className="bc-glass-segment pointer-events-none absolute bottom-[3px] left-[3px] top-[3px] rounded-lg" />
      {options.map((option, index) => (
        <button key={option.value} type="button" disabled={option.disabled}
          data-testid={option.testId}
          title={option.title ?? (typeof option.label === 'string' ? option.label : undefined)}
          aria-label={option.ariaLabel}
          role={semantics === 'tabs' ? 'tab' : undefined}
          aria-selected={semantics === 'tabs' ? option.value === value : undefined}
          aria-pressed={semantics === 'group' ? option.value === value : undefined}
          aria-current={semantics === 'navigation' && option.value === value ? 'page' : undefined}
          className={cn('relative z-10 min-w-0 flex-1 truncate rounded-lg px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40',
            (hover ?? selected) === index ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground')}
          onClick={(event) => {
            instant.current = event.detail === 0
            if (option.value !== value) onValueChange(option.value)
          }}
          onKeyDown={(event) => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const direction = geometry().rtl ? -1 : 1
            let next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 :
              index + (event.key === 'ArrowRight' ? direction : -direction)
            const delta = next >= index ? 1 : -1
            while (next >= 0 && next < options.length && isDisabled(next)) next += delta
            if (next < 0 || next >= options.length) return
            instant.current = true
            onValueChange(options[next].value)
            root.current?.querySelectorAll('button')[next].focus()
          }}
        >{option.label}</button>
      ))}
    </div>
  )
}
