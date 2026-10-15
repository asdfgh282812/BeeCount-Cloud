import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_BASE, ApiError, configureHttp, fetchFarmLeaderboard, type FarmLeaderboard } from '@beecount/api-client'
import { parseRoute, routePath } from './state/router'

const sample: FarmLeaderboard = {
  board: 'guardian', week: '2026-10-05', availableWeeks: ['2026-10-05'],
  playerCount: 3, averageScore: 4, myBookkeepingScore: 6, myValue: null, items: [], serverTime: '2026-10-15T00:00:00Z',
}

afterEach(() => { vi.unstubAllGlobals(); configureHttp({}) })

describe('farm leaderboard public read contract', () => {
  it('uses an authenticated read with explicit board and completed week', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(sample), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const data = await fetchFarmLeaderboard('test-token', 'guardian', '2026-10-05')
    expect(data).toEqual(sample)
    // S belongs to bookkeeping, even when the selected board has no score for this user.
    expect(data.myBookkeepingScore).toBe(6)
    expect(data.myValue).toBeNull()
    expect(fetch).toHaveBeenCalledWith(`${API_BASE}/game/leaderboard?board=guardian&week=2026-10-05`, {
      headers: { Authorization: 'Bearer test-token' }, cache: 'no-store',
    })
  })

  it('lets the server choose the latest completed week without using the browser clock', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(sample), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    await fetchFarmLeaderboard('test-token', 'bookkeeping')
    expect(fetch.mock.calls[0][0]).toBe(`${API_BASE}/game/leaderboard?board=bookkeeping`)
  })

  it('surfaces an API failure instead of silently treating it as an empty leaderboard', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: 'GAME_INVALID_WEEK', message: 'Week not completed' },
    }), { status: 400 })))
    await expect(fetchFarmLeaderboard('test-token', 'harvest', '2026-10-19')).rejects.toBeInstanceOf(ApiError)
  })

  it('round-trips a global route without treating farm-leaderboard as a ledger id', () => {
    const route = { kind: 'app' as const, ledgerId: '', section: 'farm-leaderboard' as const }
    expect(parseRoute('/app/farm-leaderboard/')).toEqual(route)
    expect(routePath(route)).toBe('/app/farm-leaderboard')
  })
})
