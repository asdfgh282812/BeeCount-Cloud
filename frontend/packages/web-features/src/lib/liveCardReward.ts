/**
 * 即時回饋交易(2026-10-16,Cloud `src/services/card_reward_live.py`)的判斷與
 * 期別解析——純函式。跟 App `TransactionLiveCardReward`(lib/data/db.dart)同一套
 * 規則:`reward_payout_rule_id` + `reward_payout_key` 都有值才算;key 是
 * `live:{起}~{迄}:{帳本}` 時是整期模式,否則(來源消費 id)是逐筆模式。
 */

export type LiveCardRewardLike = {
  reward_payout_rule_id?: string | null
  reward_payout_key?: string | null
}

export function isLiveCardReward(tx: LiveCardRewardLike | null | undefined): boolean {
  return Boolean(tx?.reward_payout_rule_id && tx?.reward_payout_key)
}

/** 整期模式的期別(yyyy-mm-dd,起訖)。Cloud 帳單週期的起點是「上一期結帳日」
 *  (不含),`interval === 'billing_cycle'` 時顯示用的起點往後一天。逐筆模式回 null。 */
export function liveCardRewardPeriod(
  tx: LiveCardRewardLike | null | undefined,
  interval?: string | null,
): { start: string; end: string } | null {
  const m = /^live:(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})/.exec(tx?.reward_payout_key || '')
  if (!m) return null
  let start = m[1]
  if (interval === 'billing_cycle') {
    const d = new Date(`${start}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() + 1)
    start = d.toISOString().slice(0, 10)
  }
  return { start, end: m[2] }
}
