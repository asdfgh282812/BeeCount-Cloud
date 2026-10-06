import { describe, expect, it } from 'vitest'

import {
  applyBasicRewardAutoFill,
  isRuleWithinWindow,
  pickBasicRewardRuleIds,
  stripAutoAppliedRewardIds,
  type BasicRewardRuleLike,
} from '../../../../packages/web-features/src/lib/rewardBasic'

const rule = (over: Partial<BasicRewardRuleLike> & { id: string }): BasicRewardRuleLike => ({
  account_id: 'A',
  enabled: true,
  is_basic: true,
  starts_at: null,
  ends_at: null,
  ...over,
})

describe('isRuleWithinWindow', () => {
  it('起訖日含當天,以日期比對', () => {
    const r = { starts_at: '2026-10-01T00:00:00+00:00', ends_at: '2026-10-31T00:00:00+00:00' }
    expect(isRuleWithinWindow(r, '2026-10-01T00:00')).toBe(true)
    expect(isRuleWithinWindow(r, '2026-10-31T23:59')).toBe(true)
    expect(isRuleWithinWindow(r, '2026-09-30T23:59')).toBe(false)
    expect(isRuleWithinWindow(r, '2026-11-01T00:00')).toBe(false)
  })
  it('帶時區的 ISO 瞬間用「本地日期」比對(UTC+8 凌晨 02:04 屬於當天)', () => {
    const r = { starts_at: '2026-10-06T00:00:00+00:00', ends_at: null }
    // 本地 2026-10-06 02:04 在不同時區換成 UTC 的字串
    const local = new Date(2026, 9, 6, 2, 4).toISOString()
    expect(isRuleWithinWindow(r, local)).toBe(true)
    const dayBefore = new Date(2026, 9, 5, 23, 59).toISOString()
    expect(isRuleWithinWindow(r, dayBefore)).toBe(false)
  })
  it('沒設起訖日 = 不限', () => {
    expect(isRuleWithinWindow({}, '2026-10-06T10:00')).toBe(true)
  })
})

describe('pickBasicRewardRuleIds', () => {
  it('只挑該帳戶、啟用、is_basic、在有效範圍內的規則', () => {
    const rules = [
      rule({ id: 'ok' }),
      rule({ id: 'notBasic', is_basic: false }),
      rule({ id: 'disabled', enabled: false }),
      rule({ id: 'otherAccount', account_id: 'B' }),
      rule({ id: 'expired', ends_at: '2026-09-30T00:00:00+00:00' }),
      rule({ id: 'future', starts_at: '2026-11-01T00:00:00+00:00' }),
      rule({ id: 'inRange', starts_at: '2026-10-01T00:00:00+00:00', ends_at: '2026-10-31T00:00:00+00:00' }),
    ]
    expect(pickBasicRewardRuleIds(rules, 'A', '2026-10-06T10:00')).toEqual(['ok', 'inRange'])
  })
  it('帳戶為空 → 空陣列', () => {
    expect(pickBasicRewardRuleIds([rule({ id: 'x' })], '', '2026-10-06T10:00')).toEqual([])
  })
})

describe('applyBasicRewardAutoFill', () => {
  const base = { accountId: 'A', happenedAt: '2026-10-06T10:00' }

  it('新增時自動帶入,並記為自動帶入', () => {
    const r = applyBasicRewardAutoFill({
      ...base,
      rules: [rule({ id: 'b1' }), rule({ id: 'n1', is_basic: false })],
      currentIds: [],
      autoAppliedIds: [],
    })
    expect(r.changed).toBe(true)
    expect(r.ids).toEqual(['b1'])
    expect(r.autoAppliedIds).toEqual(['b1'])
  })

  it('已經勾著的不重複加入,也不算自動帶入(複製交易預填)', () => {
    const r = applyBasicRewardAutoFill({
      ...base,
      rules: [rule({ id: 'b1' })],
      currentIds: ['b1'],
      autoAppliedIds: [],
    })
    expect(r.changed).toBe(false)
    expect(r.ids).toEqual(['b1'])
    expect(r.autoAppliedIds).toEqual([])
  })

  it('保留使用者手動勾的,並與自動帶入並存', () => {
    const r = applyBasicRewardAutoFill({
      ...base,
      rules: [rule({ id: 'b1' }), rule({ id: 'm1', is_basic: false })],
      currentIds: ['m1'],
      autoAppliedIds: [],
    })
    expect(r.ids).toEqual(['m1', 'b1'])
    expect(r.autoAppliedIds).toEqual(['b1'])
  })

  it('交易日期移出有效範圍:移除先前自動帶入的,手動勾的保留', () => {
    const rules = [
      rule({ id: 'b1', ends_at: '2026-10-10T00:00:00+00:00' }),
      rule({ id: 'm1', is_basic: false }),
    ]
    const r = applyBasicRewardAutoFill({
      accountId: 'A',
      happenedAt: '2026-10-20T10:00',
      rules,
      currentIds: ['m1', 'b1'],
      autoAppliedIds: ['b1'],
    })
    expect(r.changed).toBe(true)
    expect(r.ids).toEqual(['m1'])
    expect(r.autoAppliedIds).toEqual([])
  })

  it('沒有變動時回傳原陣列參考', () => {
    const current = ['b1']
    const r = applyBasicRewardAutoFill({
      ...base,
      rules: [rule({ id: 'b1' })],
      currentIds: current,
      autoAppliedIds: ['b1'],
    })
    expect(r.changed).toBe(false)
    expect(r.ids).toBe(current)
    expect(r.autoAppliedIds).toEqual(['b1'])
  })

  it('rules 還是舊帳戶的清單時不會帶入(依 account_id 過濾)', () => {
    const r = applyBasicRewardAutoFill({
      accountId: 'B',
      happenedAt: '2026-10-06T10:00',
      rules: [rule({ id: 'b1', account_id: 'A' })],
      currentIds: [],
      autoAppliedIds: [],
    })
    expect(r.changed).toBe(false)
    expect(r.ids).toEqual([])
  })
})

describe('stripAutoAppliedRewardIds', () => {
  it('換帳戶:只移除自動帶入的,保留手動勾的', () => {
    expect(stripAutoAppliedRewardIds(['m1', 'b1'], ['b1'])).toEqual(['m1'])
  })
})
