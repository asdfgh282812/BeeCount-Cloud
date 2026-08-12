/**
 * 分類智慧推薦(Phase 21,docs/PH17_USER_FEEDBACK_2026-08_SD.md):把後端
 * `category-suggestions` 回傳的 id 清單(依分數高到低排序)轉成排名表,供
 * `CategorySelector` 把命中的分類排到最前面。抽成純函式方便單元測試,不用
 * 拉起整個元件渲染。
 */

/** 依清單順序建 rank(index 越小分數越高)。空/未傳回傳 null,呼叫方視為
 *  「沒有推薦,維持原本排序」。 */
export function buildSuggestionRank(
  suggestedIds: readonly string[] | undefined
): Map<string, number> | null {
  if (!suggestedIds || suggestedIds.length === 0) return null
  const rank = new Map<string, number>()
  suggestedIds.forEach((id, idx) => {
    if (!rank.has(id)) rank.set(id, idx)
  })
  return rank
}

/** 推薦優先、其餘維持原本 sort_order/name 排序當 tie-breaker。`rank` 為
 *  null 時完全等同原本排序(不套用推薦)。 */
export function compareBySuggestionThenOrder<
  T extends { id: string; sort_order?: number | null; name?: string | null }
>(a: T, b: T, rank: Map<string, number> | null): number {
  if (rank) {
    const ra = rank.get(a.id) ?? Number.POSITIVE_INFINITY
    const rb = rank.get(b.id) ?? Number.POSITIVE_INFINITY
    if (ra !== rb) return ra - rb
  }
  return (
    (a.sort_order ?? 0) - (b.sort_order ?? 0) ||
    (a.name || '').localeCompare(b.name || '')
  )
}

/** 父層「有效推薦排名」:命中推薦的常常是二級子分類(例如「午餐」掛在
 *  「飲食」底下),子級網格預設收合,只在子級層面加註徽章的話使用者在
 *  頂層完全看不到任何提示,得先盲猜展開哪個父層才找得到。取父層自己的
 *  rank 跟底下所有子分類 rank 的最小值(數字越小分數越高),讓父層的排序/
 *  徽章也能反映出「這裡面有常用分類」。`rank` 為 null(沒有推薦清單)時
 *  回傳 null。 */
export function buildTopLevelSuggestionRank<T extends { id: string; name?: string | null }>(
  tops: readonly T[],
  childrenByParentNameLower: Readonly<Record<string, readonly T[]>>,
  rank: Map<string, number> | null
): Map<string, number> | null {
  if (!rank) return null
  const topRank = new Map<string, number>()
  for (const top of tops) {
    let best = rank.get(top.id) ?? Number.POSITIVE_INFINITY
    const childList = childrenByParentNameLower[(top.name || '').toLowerCase()] || []
    for (const child of childList) {
      const childRank = rank.get(child.id)
      if (childRank !== undefined && childRank < best) best = childRank
    }
    if (best !== Number.POSITIVE_INFINITY) topRank.set(top.id, best)
  }
  return topRank
}
