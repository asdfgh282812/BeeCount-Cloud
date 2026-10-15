import { authedGet } from './http'

export const FARM_BOARDS = ['bookkeeping', 'harvest', 'thief', 'guardian'] as const
export type FarmBoard = (typeof FARM_BOARDS)[number]

/** Public game data only. Financial amounts, wallets and emails are never part of this contract. */
export type FarmLeaderboardItem = {
  rank: number
  userId: string | null
  displayName: string
  avatarUrl: string | null
  value: number
  rewardCoins: number
  title: string | null
  isMe: boolean
  /** Present only while the server-side caught-thief marker is active. */
  thiefUntil?: string | null
}

export type FarmLeaderboard = {
  board: FarmBoard
  /** Completed week, YYYY-MM-DD in the server's business timezone. */
  week: string
  availableWeeks: string[]
  playerCount: number
  /** Bookkeeping S average; independent of the selected board. */
  averageScore: number
  myBookkeepingScore: number | null
  myValue: number | null
  items: FarmLeaderboardItem[]
  serverTime: string
}

export function fetchFarmLeaderboard(
  token: string,
  board: FarmBoard,
  week?: string,
): Promise<FarmLeaderboard> {
  const params = new URLSearchParams({ board })
  if (week) params.set('week', week)
  return authedGet<FarmLeaderboard>(`/game/leaderboard?${params}`, token)
}
