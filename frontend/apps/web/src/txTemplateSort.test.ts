import { describe, expect, it } from 'vitest'

import type { ReadTxTemplate } from '@beecount/api-client'
import { sortTemplates } from '@beecount/web-features'

const tpl = (over: Partial<ReadTxTemplate>): ReadTxTemplate => ({
  id: over.id ?? 'x',
  name: over.name ?? 'x',
  tx_type: 'expense',
  amount: 10,
  tag_ids: [],
  sort_order: 0,
  last_change_id: 1,
  ...over,
})

describe('sortTemplates(「記憶」排序)', () => {
  const rows = [
    tpl({ id: 'a', name: 'A', sort_order: 0, usage_count: 1, last_used_at: '2026-10-01T00:00:00Z' }),
    tpl({ id: 'b', name: 'B', sort_order: 1, usage_count: 5, last_used_at: '2026-09-01T00:00:00Z' }),
    tpl({ id: 'c', name: 'C', sort_order: 2, usage_count: 5, last_used_at: '2026-10-05T00:00:00Z' }),
    tpl({ id: 'd', name: 'D', sort_order: 3 }),
    tpl({ id: 'e', name: 'E', sort_order: 4 }),
  ]

  it('常用:次數多的在前,同次數看最近使用,沒用過的退回手動順序', () => {
    expect(sortTemplates(rows, 'frequent').map((r) => r.id)).toEqual(['c', 'b', 'a', 'd', 'e'])
  })

  it('手動:照 sort_order,不受使用次數影響', () => {
    const shuffled = [rows[2], rows[4], rows[0], rows[3], rows[1]]
    expect(sortTemplates(shuffled, 'manual').map((r) => r.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
  })

  it('不會改動傳入的陣列', () => {
    const copy = rows.map((r) => r.id)
    sortTemplates(rows, 'frequent')
    expect(rows.map((r) => r.id)).toEqual(copy)
  })
})
