export type DialogFrame = { kind: string; value: unknown }
type DialogState = Record<string, unknown> & { beeDialogs?: DialogFrame[] }

export function dialogFrames(state: unknown): DialogFrame[] {
  if (!state || typeof state !== 'object') return []
  const frames = (state as DialogState).beeDialogs
  return Array.isArray(frames)
    ? frames.filter((frame) => frame && typeof frame.kind === 'string')
    : []
}

export function pushDialogState(state: unknown, frame: DialogFrame): DialogState {
  return {
    ...(state && typeof state === 'object' ? state : {}),
    beeDialogs: [...dialogFrames(state), frame],
  }
}

export function replaceDialogState(state: unknown, frame: DialogFrame): DialogState {
  const frames = dialogFrames(state)
  return {
    ...(state && typeof state === 'object' ? state : {}),
    beeDialogs: [...frames.slice(0, -1), frame],
  }
}

export function topDialog(state: unknown): DialogFrame | undefined {
  const frames = dialogFrames(state)
  return frames[frames.length - 1]
}

export function retainedDialogValue<T>(state: unknown, kind: string): T | null {
  const frames = dialogFrames(state)
  for (let i = frames.length - 1; i >= 0; i--) {
    if (frames[i].kind === kind) return frames[i].value as T
  }
  return null
}
