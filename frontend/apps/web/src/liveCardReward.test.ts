import { describe, expect, it } from 'vitest'

import { isLiveCardReward, liveCardRewardPeriod } from '@beecount/web-features'

// 即時回饋交易(2026-10-16,Cloud services/card_reward_live.py)的判斷/期別解析,
// 跟 App `TransactionLiveCardReward`(lib/data/db.dart)同一套規則。
describe('liveCardReward', () => {
  it('needs both rule id and payout key', () => {
    expect(isLiveCardReward({ reward_payout_rule_id: 'r1', reward_payout_key: 'live:2026-10-01~2026-10-31:l' })).toBe(true)
    expect(isLiveCardReward({ reward_payout_rule_id: 'r1', reward_payout_key: null })).toBe(false)
    expect(isLiveCardReward({ reward_payout_rule_id: null, reward_payout_key: 'tx-1' })).toBe(false)
    expect(isLiveCardReward(null)).toBe(false)
  })

  it('parses period keys and shifts billing-cycle start by one day', () => {
    const tx = { reward_payout_rule_id: 'r1', reward_payout_key: 'live:2026-09-05~2026-10-05:ledger-1' }
    expect(liveCardRewardPeriod(tx, 'calendar_month')).toEqual({ start: '2026-09-05', end: '2026-10-05' })
    expect(liveCardRewardPeriod(tx, 'billing_cycle')).toEqual({ start: '2026-09-06', end: '2026-10-05' })
    expect(liveCardRewardPeriod({ reward_payout_rule_id: 'r1', reward_payout_key: 'tx-source' })).toBeNull()
  })
})
