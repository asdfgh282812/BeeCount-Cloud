import { describe, expect, it } from 'vitest'

import {
  buildSuggestionRank,
  buildTopLevelSuggestionRank,
  compareBySuggestionThenOrder,
} from '@beecount/web-features'

/**
 * Phase 21(docs/PH17_USER_FEEDBACK_2026-08_SD.md):分類智慧推薦排序邏輯。
 * `CategorySelector` 的 topLevels/children 排序委派给这两个纯函式,这里直接
 * 测排序契约,不用拉起整个元件渲染。
 */
type Row = { id: string; sort_order?: number | null; name?: string | null }

function row(id: string, sortOrder = 0, name = id): Row {
  return { id, sort_order: sortOrder, name }
}

describe('buildSuggestionRank', () => {
  it('空/未傳回傳 null', () => {
    expect(buildSuggestionRank(undefined)).toBeNull()
    expect(buildSuggestionRank([])).toBeNull()
  })

  it('依清單順序建立 rank,index 越小分數越高', () => {
    const rank = buildSuggestionRank(['b', 'a', 'c'])
    expect(rank?.get('b')).toBe(0)
    expect(rank?.get('a')).toBe(1)
    expect(rank?.get('c')).toBe(2)
  })

  it('重複 id 只取第一次出現的 index', () => {
    const rank = buildSuggestionRank(['a', 'b', 'a'])
    expect(rank?.get('a')).toBe(0)
    expect(rank?.size).toBe(2)
  })
})

describe('compareBySuggestionThenOrder', () => {
  it('rank 為 null 時完全等同原本 sort_order/name 排序,不套用推薦', () => {
    const rank = null
    const rows = [row('z', 2, 'Zebra'), row('a', 1, 'Apple'), row('m', 1, 'Mango')]
    rows.sort((a, b) => compareBySuggestionThenOrder(a, b, rank))
    expect(rows.map((r) => r.id)).toEqual(['a', 'm', 'z'])
  })

  it('推薦命中的分類排到最前面,即使 sort_order/name 排序本來在後面', () => {
    // Coffee 的 sort_order 比 Taxi 大(本来排更後面),但 Taxi 被推薦,应该反超。
    const rank = buildSuggestionRank(['taxi'])
    const rows = [row('coffee', 1, 'Coffee'), row('taxi', 5, 'Taxi'), row('grocery', 2, 'Grocery')]
    rows.sort((a, b) => compareBySuggestionThenOrder(a, b, rank))
    expect(rows[0].id).toBe('taxi')
  })

  it('多個推薦項目之間依推薦清單順序排序,不是 tie-breaker 排序', () => {
    // 推薦清單顺序 b > a,即使 a 的 sort_order/name 排序本来在前面。
    const rank = buildSuggestionRank(['b', 'a'])
    const rows = [row('a', 0, 'Apple'), row('b', 9, 'Zebra')]
    rows.sort((x, y) => compareBySuggestionThenOrder(x, y, rank))
    expect(rows.map((r) => r.id)).toEqual(['b', 'a'])
  })

  it('未命中推薦的項目維持原本 sort_order/name 排序當 tie-breaker', () => {
    const rank = buildSuggestionRank(['taxi'])
    const rows = [row('taxi', 0), row('zebra', 1, 'Zebra'), row('apple', 1, 'Apple')]
    rows.sort((a, b) => compareBySuggestionThenOrder(a, b, rank))
    expect(rows.map((r) => r.id)).toEqual(['taxi', 'apple', 'zebra'])
  })
})

describe('buildTopLevelSuggestionRank', () => {
  // 手動瀏覽器驗證時發現的真實案例:後端推薦的是「午餐」這種二級子分類,
  // 不是頂層「飲食」本身——頂層網格預設收合,子級徽章使用者完全看不到,
  // 必須讓父層也繼承子分類的推薦排名才找得到。
  it('rank 為 null 時回傳 null', () => {
    expect(buildTopLevelSuggestionRank([row('food', 0, 'Food')], {}, null)).toBeNull()
  })

  it('子分類命中推薦時,父層繼承子分類的 rank(即使父層自己沒被推薦)', () => {
    const rank = buildSuggestionRank(['lunch']) // 只推荐子分类,不推荐父层
    const tops = [row('food', 0, 'Food'), row('transport', 1, 'Transport')]
    const childrenByParent = { food: [row('lunch', 0, 'Lunch'), row('dinner', 1, 'Dinner')] }
    const topRank = buildTopLevelSuggestionRank(tops, childrenByParent, rank)
    expect(topRank?.get('food')).toBe(0)
    expect(topRank?.has('transport')).toBe(false)
  })

  it('父層自己被推薦時直接用自己的 rank,不受子分類影響', () => {
    const rank = buildSuggestionRank(['food'])
    const tops = [row('food', 0, 'Food')]
    const childrenByParent = { food: [row('lunch', 0, 'Lunch')] }
    const topRank = buildTopLevelSuggestionRank(tops, childrenByParent, rank)
    expect(topRank?.get('food')).toBe(0)
  })

  it('父層自己跟子分類都命中時取分數較高(index 較小)的那個', () => {
    const rank = buildSuggestionRank(['lunch', 'food']) // lunch(0) 比 food(1) 分數高
    const tops = [row('food', 0, 'Food')]
    const childrenByParent = { food: [row('lunch', 0, 'Lunch')] }
    const topRank = buildTopLevelSuggestionRank(tops, childrenByParent, rank)
    expect(topRank?.get('food')).toBe(0) // 继承 lunch 的 rank,不是自己的 1
  })

  it('父層排序時,繼承的 rank 能讓它反超其他頂層項目', () => {
    const rank = buildSuggestionRank(['lunch'])
    const tops = [row('transport', 0, 'Transport'), row('food', 1, 'Food')]
    const childrenByParent = { food: [row('lunch', 0, 'Lunch')] }
    const topRank = buildTopLevelSuggestionRank(tops, childrenByParent, rank)
    tops.sort((a, b) => compareBySuggestionThenOrder(a, b, topRank))
    expect(tops.map((r) => r.id)).toEqual(['food', 'transport'])
  })
})
