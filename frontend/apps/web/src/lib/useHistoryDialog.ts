import { useCallback, useRef, type Dispatch, type SetStateAction } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { pushDialogState, replaceDialogState, retainedDialogValue, topDialog } from './dialogHistory'

/** Each dialog occupies one browser history entry. Hidden parents keep their data. */
export function useHistoryDialog<T>(kind: string) {
  const location = useLocation()
  const navigate = useNavigate()
  const latest = useRef(location)
  latest.current = location
  const frame = topDialog(location.state)
  const value = frame?.kind === kind ? frame.value as T : null

  const setValue: Dispatch<SetStateAction<T | null>> = useCallback((update) => {
    const current = latest.current
    const top = topDialog(current.state)
    const previous = top?.kind === kind ? top.value as T : null
    const next = typeof update === 'function'
      ? (update as (value: T | null) => T | null)(previous)
      : update
    if (next === null) {
      if (top?.kind === kind) navigate(-1)
      return
    }
    if (Object.is(previous, next)) return
    const replace = typeof update === 'function' && top?.kind === kind
    const state = replace
      ? replaceDialogState(current.state, { kind, value: next })
      : pushDialogState(current.state, { kind, value: next })
    navigate(`${current.pathname}${current.search}${current.hash}`, { state, replace })
  }, [kind, navigate])

  // Refreshing a retained detail changes its data, not its return destination.
  const replaceValue = useCallback((next: T) => {
    const current = latest.current
    if (topDialog(current.state)?.kind !== kind) return
    navigate(`${current.pathname}${current.search}${current.hash}`, {
      state: replaceDialogState(current.state, { kind, value: next }),
      replace: true,
    })
  }, [kind, navigate])

  return [value, setValue, replaceValue, retainedDialogValue<T>(location.state, kind)] as const
}
