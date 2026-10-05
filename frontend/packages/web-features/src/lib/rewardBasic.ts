/**
 * 信用卡紅利回饋「基本回饋」(is_basic)自動帶入——純函式,不依賴 React,
 * 給 `TransactionsPanel` 新增支出時使用,並可單獨做單元測試。
 *
 * 規則:使用者在「新增」支出交易選了信用卡帳戶後,該帳戶下「啟用中、
 * is_basic、且交易日期落在 starts_at~ends_at 有效範圍內」的規則自動加進
 * reward_rule_ids 勾選;使用者手動點過任何回饋 chip 後(touched)由呼叫端
 * 停止呼叫本函式,不再覆寫。換帳戶時先前「自動帶入」的規則要移除,但使用者
 * 手動勾的保留——所以必須另外記住哪些是自動帶入的(`autoAppliedIds`)。
 */

export type BasicRewardRuleLike = {
  id: string
  account_id: string
  enabled: boolean
  is_basic?: boolean | null
  starts_at?: string | null
  ends_at?: string | null
}

/** 取 ISO / datetime-local 字串的日期部分(yyyy-mm-dd)。規則的 starts_at/
 *  ends_at 是以 UTC 日期 00:00 儲存的「日」,後端也以日期(含起訖當天)判斷,
 *  所以這裡用日期字串比對,避免結束日當天 00:00 之後的交易被誤判為過期。 */
function datePart(value: string | null | undefined): string {
  return (value || '').slice(0, 10)
}

/** 規則在交易日期當天是否仍在 starts_at~ends_at 有效範圍內(起訖日皆含)。
 *  不看 enabled;交易日期為空字串時視為不限(不排除)。 */
export function isRuleWithinWindow(
  rule: Pick<BasicRewardRuleLike, 'starts_at' | 'ends_at'>,
  happenedAt: string,
): boolean {
  const txDate = datePart(happenedAt)
  if (!txDate) return true
  const start = datePart(rule.starts_at)
  const end = datePart(rule.ends_at)
  if (start && txDate < start) return false
  if (end && txDate > end) return false
  return true
}

/** 該帳戶底下應自動帶入的基本回饋規則 id(啟用中 + is_basic + 在有效範圍內)。
 *  依 `account_id` 過濾,避免換帳戶瞬間 rules prop 還是舊帳戶的清單。 */
export function pickBasicRewardRuleIds(
  rules: readonly BasicRewardRuleLike[],
  accountId: string,
  happenedAt: string,
): string[] {
  if (!accountId) return []
  return rules
    .filter(
      (r) =>
        r.account_id === accountId &&
        r.enabled &&
        Boolean(r.is_basic) &&
        isRuleWithinWindow(r, happenedAt),
    )
    .map((r) => r.id)
}

export type BasicRewardAutoFillResult = {
  /** 套用後的 reward_rule_ids(沒變動時回傳原陣列參考)。 */
  ids: string[]
  /** 目前「由自動帶入貢獻」的 id,呼叫端存起來給下次呼叫(換帳戶時移除用)。 */
  autoAppliedIds: string[]
  changed: boolean
}

/**
 * 套用自動帶入:
 *  - 先前自動帶入、但現在不再符合(換日期移出範圍/規則停用)的 id 移除;
 *  - 現在符合、且尚未勾選的 id 加入並記為自動帶入;
 *  - 使用者原本就勾著的(含複製交易預填的)不算自動帶入,之後不會被移除。
 * 使用者手動勾的 id 永遠保留。
 */
export function applyBasicRewardAutoFill(args: {
  rules: readonly BasicRewardRuleLike[]
  accountId: string
  happenedAt: string
  currentIds: readonly string[]
  autoAppliedIds: readonly string[]
}): BasicRewardAutoFillResult {
  const { rules, accountId, happenedAt, currentIds, autoAppliedIds } = args
  const eligible = pickBasicRewardRuleIds(rules, accountId, happenedAt)
  const eligibleSet = new Set(eligible)
  const prevAuto = new Set(autoAppliedIds)

  // 移除:曾自動帶入、現在不再符合的。
  const kept = currentIds.filter((id) => !prevAuto.has(id) || eligibleSet.has(id))
  const keptSet = new Set(kept)
  // 加入:符合但尚未勾選的。
  const added = eligible.filter((id) => !keptSet.has(id))

  const nextAuto = [
    ...kept.filter((id) => prevAuto.has(id) && eligibleSet.has(id)),
    ...added,
  ]
  const nextIds = [...kept, ...added]
  const changed = added.length > 0 || kept.length !== currentIds.length
  return {
    ids: changed ? nextIds : (currentIds as string[]),
    autoAppliedIds: nextAuto,
    changed,
  }
}

/** 換帳戶(或離開信用卡帳戶)時:移除先前自動帶入的 id,保留使用者手動勾的。 */
export function stripAutoAppliedRewardIds(
  currentIds: readonly string[],
  autoAppliedIds: readonly string[],
): string[] {
  const auto = new Set(autoAppliedIds)
  return currentIds.filter((id) => !auto.has(id))
}
