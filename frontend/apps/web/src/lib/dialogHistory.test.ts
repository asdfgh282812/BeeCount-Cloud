import { describe, expect, it } from 'vitest'
import { createMemoryRouter } from 'react-router-dom'
import { dialogFrames, pushDialogState, replaceDialogState, retainedDialogValue, topDialog } from './dialogHistory'

describe('actual dialog source history', () => {
  it('retains the originating transaction when opening an editor', () => {
    const detail = pushDialogState({ source: '/app/overview' }, { kind: 'tx', value: { id: 'A' } })
    const editor = pushDialogState(detail, { kind: 'tx-editor', value: true })
    expect(dialogFrames(editor).map((frame) => frame.kind)).toEqual(['tx', 'tx-editor'])
    expect(topDialog(detail)?.value).toEqual({ id: 'A' })
    expect(editor.source).toBe('/app/overview')
  })

  it('keeps A when opening related transaction B and refreshing B', () => {
    const a = pushDialogState(null, { kind: 'tx', value: { id: 'A' } })
    const b = pushDialogState(a, { kind: 'tx', value: { id: 'B', amount: 1 } })
    const refreshed = replaceDialogState(b, { kind: 'tx', value: { id: 'B', amount: 2 } })
    expect(dialogFrames(refreshed).map((frame) => frame.value)).toEqual([
      { id: 'A' }, { id: 'B', amount: 2 },
    ])
    expect(dialogFrames(a)).toHaveLength(1)
  })

  it('leaves a directly opened page with no fabricated parent dialog', () => {
    expect(dialogFrames(null)).toEqual([])
    expect(dialogFrames({ beeDialogs: 'invalid' })).toEqual([])
  })

  it('browser history returns editor → related transaction → original transaction → source page', async () => {
    const router = createMemoryRouter([{ path: '*', element: null }], {
      initialEntries: ['/app/overview'],
    })
    try {
      const sourceKey = router.state.location.key
      await router.navigate('/app/overview', {
        state: pushDialogState(null, { kind: 'tx', value: { id: 'A' } }),
      })
      await router.navigate('/app/overview', {
        state: pushDialogState(router.state.location.state, { kind: 'tx', value: { id: 'B' } }),
      })
      await router.navigate('/app/overview', {
        state: pushDialogState(router.state.location.state, { kind: 'tx-editor', value: true }),
      })
      expect(retainedDialogValue(router.state.location.state, 'tx')).toEqual({ id: 'B' })
      await router.navigate(-1)
      expect(topDialog(router.state.location.state)?.value).toEqual({ id: 'B' })
      await router.navigate('/app/overview', {
        state: replaceDialogState(router.state.location.state, { kind: 'tx', value: { id: 'B', amount: 75 } }),
        replace: true,
      })
      await router.navigate(-1)
      expect(topDialog(router.state.location.state)?.value).toEqual({ id: 'A' })
      await router.navigate(-1)
      expect(router.state.location.key).toBe(sourceKey)
      expect(dialogFrames(router.state.location.state)).toEqual([])
    } finally {
      router.dispose()
    }
  })

  it('a cross-page shortcut returns to its actual transaction detail', async () => {
    const router = createMemoryRouter([{ path: '*', element: null }], {
      initialEntries: ['/app/overview'],
    })
    try {
      await router.navigate('/app/overview', {
        state: pushDialogState(null, { kind: 'tx', value: { id: 'A' } }),
      })
      await router.navigate('/app/debts?highlight=debt-1')
      await router.navigate(-1)
      expect(router.state.location.pathname).toBe('/app/overview')
      expect(topDialog(router.state.location.state)?.value).toEqual({ id: 'A' })
    } finally {
      router.dispose()
    }
  })
})
