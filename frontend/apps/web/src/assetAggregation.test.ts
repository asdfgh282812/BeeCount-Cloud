import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'
import {
  accountBalance,
  type AssetGroup,
  buildDoubleCountedChildIds,
  computeCurrencySummary,
  computeTypeGroups,
  effectiveRateToBase,
  mergeGroupsToBase,
  resolveAccountGroupDisplayType,
  splitByCurrency
} from '@beecount/web-features'
import { describe, expect, it } from 'vitest'

/**
 * 资产页多币种聚合契约 —— 锁住"绝不跨币种相加"这条铁律。
 * 历史上这页裸加 balance 把不同币种当同币种加错了($1000 当 ¥1000)。
 *
 * 这些函数只读 account_type / currency / balance / initial_balance,其余 ReadAccount
 * 字段不参与聚合,所以用 partial 造数据再 cast,免得每条都填全。
 */
function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
  return p as ReadAccount
}

describe('asset aggregation — 绝不跨币种相加', () => {
  it('splitByCurrency 按归一化币种码分组(缺省 CNY、大小写归一)', () => {
    const map = splitByCurrency([
      acc({ currency: 'CNY', balance: 100 }),
      acc({ currency: 'usd', balance: 5 }),
      acc({ currency: 'USD', balance: 7 }),
      acc({ currency: null, balance: 1 })
    ])
    expect([...map.keys()].sort()).toEqual(['CNY', 'USD'])
    expect(map.get('CNY')?.length).toBe(2)
    expect(map.get('USD')?.length).toBe(2)
  })

  it('accountBalance 优先 balance,回退 initial_balance', () => {
    expect(accountBalance(acc({ balance: 42, initial_balance: 1 }))).toBe(42)
    expect(accountBalance(acc({ balance: null, initial_balance: 9 }))).toBe(9)
    expect(accountBalance(acc({ initial_balance: 3 }))).toBe(3)
    expect(accountBalance(acc({}))).toBe(0)
  })

  it('computeCurrencySummary:资产负债都带符号,净值 = 资产 + 负债(对齐 mobile getNetWorthBreakdown)', () => {
    const s = computeCurrencySummary([
      acc({ account_type: 'cash', balance: 1000 }),
      acc({ account_type: 'bank_card', balance: -200 }), // 透支资产 → 扣减总资产
      acc({ account_type: 'credit_card', balance: -300 }), // 负债(欠款为负)
      acc({ account_type: 'loan', balance: -500 }) // 负债
    ])
    expect(s.assetTotal).toBe(800) // 1000 + (-200)
    expect(s.liabilityTotal).toBe(-800) // 带符号:−300 + −500,展示欠款时才 abs
    expect(s.netWorth).toBe(0) // 800 + (−800)
  })

  it('溢缴款:负债账户正余额记入资产而非负债,绝不反向当欠款扣减(历史 bug:abs 后减)', () => {
    // 复盘场景:脏数据把 76 万收入灌进信用卡,余额 +761779.84。app 端净资产
    // 正确地 +76 万,web 端旧逻辑 abs 后减、又扣 76 万,两端差 152 万。
    // 2026-08-12 使用者进一步回报:溢缴的这笔钱经济上是应收/资产头寸,顶部
    // 「负债」方块显示它仍算欠款不合理("溢缴應該是收入，而非負債")——
    // 改成逐账户按正负拆:负债类型账户余额为正时记入 assetTotal,不再留在
    // liabilityTotal 里(哪怕只是符号为正、数字上不再被当欠款扣)。
    const s = computeCurrencySummary([
      acc({ account_type: 'cash', balance: 1_000_000 }),
      acc({ account_type: 'credit_card', balance: 761_779.84 }) // 溢缴/正余额负债 → 记入资产
    ])
    expect(s.assetTotal).toBeCloseTo(1_761_779.84, 2)
    expect(s.liabilityTotal).toBe(0) // 没有真实欠款
    expect(s.netWorth).toBeCloseTo(1_761_779.84, 2) // 加上,而不是 1_000_000 − 761_779.84
  })

  it('负债类型账户不互相netting:+10w 信用卡(溢缴)记资产、−20w 贷款(欠款)留负债,不合并抵销', () => {
    // 2026-08-12 之前的口径是把同为「负债类型」的不同账户余额直接相加再看正负
    // (+10w 信用卡抵掉 −20w 贷款的一部分,显示欠 10w)—— 但这两张是完全不同的
    // 账户/债权债务关系,信用卡溢缴的钱不会自动拿去还别笔贷款,不该被netting掉。
    // 改成逐账户拆分:溢缴的信用卡进 assetTotal,欠款的贷款留在 liabilityTotal,
    // 净值总和不变,但「负债」方块只反映真实欠款(−20w),不再被溢缴冲淡成 −10w。
    const s = computeCurrencySummary([
      acc({ account_type: 'credit_card', balance: 100_000 }),
      acc({ account_type: 'loan', balance: -200_000 })
    ])
    expect(s.assetTotal).toBe(100_000)
    expect(s.liabilityTotal).toBe(-200_000)
    expect(Math.abs(s.liabilityTotal)).toBe(200_000) // 展示层口径:真实欠款 20w
    expect(s.netWorth).toBe(-100_000) // 总和不变
  })

  it('每币种汇总各自独立 —— CNY 与 USD 不合并', () => {
    const rows = [
      acc({ account_type: 'cash', currency: 'CNY', balance: 2_472_500 }),
      acc({ account_type: 'cash', currency: 'USD', balance: 1200 }),
      acc({ account_type: 'credit_card', currency: 'USD', balance: -300 })
    ]
    const byCur = splitByCurrency(rows)
    const cny = computeCurrencySummary(byCur.get('CNY') ?? [])
    const usd = computeCurrencySummary(byCur.get('USD') ?? [])

    expect(cny.netWorth).toBe(2_472_500)
    expect(usd.assetTotal).toBe(1200)
    expect(usd.liabilityTotal).toBe(-300)
    expect(usd.netWorth).toBe(900)

    // 反例:旧 bug 的裸加会把 $ 当 ¥ 得到 2_473_400 这种错值。分币种后绝不会出现。
    const naiveWrong = rows.reduce((sum, r) => sum + accountBalance(r), 0)
    expect(naiveWrong).toBe(2_473_400)
    expect(cny.netWorth).not.toBe(naiveWrong)
  })

  it('splitByCurrency 排除跨幣別 account_group 子帳戶,避免折算彙總卡雙重計入(2026-08-12)', () => {
    // 後端 list_workspace_accounts 已把子帳戶換算後回填到 account_group 自己的
    // balance 上(TWD 群組 = 19400 自己 + 換算後的 JPY/USD 子帳戶)。子帳戶因為
    // 幣別跟群組不同,若在 splitByCurrency 分桶後才排除,會被切進不同 bucket、
    // 逃過下游 computeCurrencySummary/computeTypeGroups 各自的
    // buildDoubleCountedChildIds 檢查,導致這筆錢被群組(已折算)+子帳戶(原幣)
    // 各算一次。這裡驗證分桶這一步就先排除,子帳戶完全不會出現在任何 bucket。
    const rows = [
      acc({ id: 'group', account_type: 'account_group', currency: 'TWD', balance: 22_989.196 }),
      acc({ id: 'twd-child', parent_account_id: 'group', account_type: 'bank_card', currency: 'TWD', balance: 19_400 }),
      acc({ id: 'jpy-child', parent_account_id: 'group', account_type: 'bank_card', currency: 'JPY', balance: 10_800 }),
      acc({ id: 'usd-child', parent_account_id: 'group', account_type: 'bank_card', currency: 'USD', balance: 41.81 })
    ]
    const byCur = splitByCurrency(rows)
    expect([...byCur.keys()]).toEqual(['TWD'])
    expect(byCur.get('TWD')?.map((r) => r.id)).toEqual(['group'])
    expect(computeCurrencySummary(byCur.get('TWD') ?? []).netWorth).toBeCloseTo(22_989.196, 3)
  })
})

/**
 * effectiveRateToBase 契约 —— pin 住各边界分支的有意行为。
 *
 * 特别注意:override 存在但非法(非 finite / <=0)→ 返回 null,**不回落 auto**。
 * 这是有意行为:用户手动填了一个坏值,宁可让折算缺失也不静默用自动值混淆来源。
 */
describe('effectiveRateToBase', () => {
  // 构造辅助
  function auto(rates: Record<string, string>, rateDate = '2025-01-01'): ExchangeRatesResponse {
    return { rates, rate_date: rateDate } as ExchangeRatesResponse
  }
  function ov(base_currency: string, quote_currency: string, rate: string): ExchangeRateOverride {
    return { base_currency, quote_currency, rate } as ExchangeRateOverride
  }

  it('① quote === base → rate 1, source auto', () => {
    const result = effectiveRateToBase('CNY', 'CNY', null, [])
    expect(result).not.toBeNull()
    expect(result!.rate).toBe(1)
    expect(result!.source).toBe('auto')
  })

  it('② override 优先于 auto —— override rate 用于计算,source=manual', () => {
    const autoRates = auto({ USD: '7.2' })    // 1 CNY = 7.2 USD → auto: 1 USD = 1/7.2 CNY
    const overrides = [ov('CNY', 'USD', '7.5')] // 1 USD = 7.5 CNY (手动)
    const result = effectiveRateToBase('USD', 'CNY', autoRates, overrides)
    expect(result).not.toBeNull()
    expect(result!.rate).toBe(7.5)
    expect(result!.source).toBe('manual')
  })

  it('③ auto 取倒数 —— rates["USD"]="0.25" → 1 USD = 4 base', () => {
    // auto rates 存储的是 1 base = x quote,故 1 quote = 1/x base
    const autoRates = auto({ USD: '0.25' })  // 1 CNY = 0.25 USD → 1 USD = 4 CNY
    const result = effectiveRateToBase('USD', 'CNY', autoRates, [])
    expect(result).not.toBeNull()
    expect(result!.rate).toBeCloseTo(4)
    expect(result!.source).toBe('auto')
  })

  it('④ override 存在但非法(非 finite/<=0) → null,不回落 auto(有意行为,pin 住)', () => {
    const autoRates = auto({ USD: '7.2' })   // auto 有值
    const overrides = [ov('CNY', 'USD', 'bad')] // override rate 非法
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overrides)).toBeNull()

    const overridesZero = [ov('CNY', 'USD', '0')]
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overridesZero)).toBeNull()

    const overridesNeg = [ov('CNY', 'USD', '-1')]
    expect(effectiveRateToBase('USD', 'CNY', autoRates, overridesNeg)).toBeNull()
  })

  it('⑤ auto 缺失/非法 → null', () => {
    // auto 为 null
    expect(effectiveRateToBase('USD', 'CNY', null, [])).toBeNull()

    // auto 存在但该 quote 不在 rates 里
    expect(effectiveRateToBase('EUR', 'CNY', auto({ USD: '7.2' }), [])).toBeNull()

    // auto rates 值非法
    expect(effectiveRateToBase('USD', 'CNY', auto({ USD: 'NaN' }), [])).toBeNull()
    expect(effectiveRateToBase('USD', 'CNY', auto({ USD: '0' }), [])).toBeNull()
  })
})

/**
 * mergeGroupsToBase 契约 —— 折算汇总视图的「合并构成 donut」聚合。
 * 锁两点:① 各币种同类型折算后跨币种累加进主币种;② 缺失汇率的整币种**剔除**,
 * 绝不按 1 折入(与净资产/资产/负债折算同口径)。
 */
describe('mergeGroupsToBase — 折算合并构成', () => {
  function group(p: Partial<AssetGroup> & { value: number; currency?: string }): AssetGroup {
    return {
      type: p.type ?? 'cash',
      label: p.label ?? p.type ?? 'cash',
      color: p.color ?? '#000',
      isLiability: p.isLiability ?? false,
      rows: p.rows ?? [],
      subtotals: [{ currency: p.currency ?? 'CNY', value: p.value }]
    }
  }
  function auto(rates: Record<string, string>): ExchangeRatesResponse {
    return { rates, rate_date: '2025-01-01' } as ExchangeRatesResponse
  }

  it('各币种同类型按汇率折算后跨币种合并到主币种', () => {
    // base=CNY。USD 走 auto:1 CNY = 0.25 USD → 1 USD = 4 CNY。
    const buckets = [
      { currency: 'CNY', groups: [group({ type: 'cash', value: 1000, currency: 'CNY' })] },
      {
        currency: 'USD',
        groups: [
          group({ type: 'cash', value: 100, currency: 'USD' }), // ×4 = 400 CNY
          group({ type: 'bank_card', value: 50, currency: 'USD' }) // ×4 = 200 CNY
        ]
      }
    ]
    const merged = mergeGroupsToBase(buckets, 'CNY', auto({ USD: '0.25' }), [])
    const cash = merged.find((g) => g.type === 'cash')!
    const bank = merged.find((g) => g.type === 'bank_card')!
    // cash: 1000(CNY) + 100×4(USD) = 1400;输出单条 subtotal、币种为 base。
    expect(cash.subtotals).toHaveLength(1)
    expect(cash.subtotals[0].currency).toBe('CNY')
    expect(cash.subtotals[0].value).toBeCloseTo(1400)
    expect(bank.subtotals[0].value).toBeCloseTo(200)
  })

  it('缺失汇率的整币种被剔除,绝不按 1 折入', () => {
    // EUR 既无 override 也不在 auto.rates → 整币种丢弃。
    const buckets = [
      { currency: 'CNY', groups: [group({ type: 'cash', value: 1000, currency: 'CNY' })] },
      { currency: 'EUR', groups: [group({ type: 'cash', value: 999, currency: 'EUR' })] }
    ]
    const merged = mergeGroupsToBase(buckets, 'CNY', auto({ USD: '0.25' }), [])
    const cash = merged.find((g) => g.type === 'cash')!
    // 只剩 CNY 的 1000;EUR 的 999 既没按 1 加、也没生成新组。
    expect(cash.subtotals[0].value).toBe(1000)
    expect(cash.subtotals[0].value).not.toBe(1999)
  })
})

/**
 * Phase 17(需求 #1)—— 主帐户(account_group)按子帐户内容归组,而非永远独立
 * 成一个「主帐户」分组。锁住 resolveAccountGroupDisplayType 三个分支 +
 * computeTypeGroups/computeCurrencySummary 的落地效果。
 */
describe('resolveAccountGroupDisplayType — 主帳戶依子帳戶內容分組(Phase 17)', () => {
  function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
    return p as ReadAccount
  }

  it('已有子帳戶且類型一致 → 用該類型', () => {
    const group = acc({ id: 'g1', account_type: 'account_group' })
    const children = [
      acc({ id: 'c1', account_type: 'credit_card', parent_account_id: 'g1' }),
      acc({ id: 'c2', account_type: 'credit_card', parent_account_id: 'g1' })
    ]
    expect(resolveAccountGroupDisplayType(group, children)).toBe('credit_card')
  })

  it('子帳戶類型混合 → 保守 fallback 回 account_group', () => {
    const group = acc({ id: 'g1', account_type: 'account_group' })
    const children = [
      acc({ id: 'c1', account_type: 'credit_card', parent_account_id: 'g1' }),
      acc({ id: 'c2', account_type: 'bank_card', parent_account_id: 'g1' })
    ]
    expect(resolveAccountGroupDisplayType(group, children)).toBe('account_group')
  })

  it('沒有子帳戶時 → 退回既有 credit-fields 推斷(有帳單日/額度/還款日 → credit_card)', () => {
    expect(resolveAccountGroupDisplayType(acc({ id: 'g1', account_type: 'account_group', credit_limit: 10000 }), [])).toBe(
      'credit_card'
    )
    expect(resolveAccountGroupDisplayType(acc({ id: 'g1', account_type: 'account_group', billing_day: 5 }), [])).toBe(
      'credit_card'
    )
    expect(resolveAccountGroupDisplayType(acc({ id: 'g1', account_type: 'account_group', payment_due_day: 20 }), [])).toBe(
      'credit_card'
    )
  })

  it('沒有子帳戶也沒有 credit 欄位 → 維持獨立 account_group', () => {
    expect(resolveAccountGroupDisplayType(acc({ id: 'g1', account_type: 'account_group' }), [])).toBe('account_group')
  })
})

describe('computeTypeGroups — 分組歸屬 + 負債計入(Phase 17)', () => {
  const t = (k: string) => k
  function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
    return p as ReadAccount
  }

  it('掛信用卡子帳戶的主帳戶歸類到信用卡分組,且小計計入負債', () => {
    const rows = [
      acc({ id: 'g1', name: '玉山信用卡', account_type: 'account_group', balance: 0 }),
      acc({ id: 'c1', name: 'U Bear', account_type: 'credit_card', parent_account_id: 'g1', balance: -7566 }),
      acc({ id: 'c2', name: 'Pi', account_type: 'credit_card', parent_account_id: 'g1', balance: -600 })
    ]
    const groups = computeTypeGroups(rows, t)
    // 不应再出现独立的 account_group 分组。
    expect(groups.find((g) => g.type === 'account_group')).toBeUndefined()
    const ccGroup = groups.find((g) => g.type === 'credit_card')!
    expect(ccGroup).toBeDefined()
    expect(ccGroup.isLiability).toBe(true)
    expect(ccGroup.rows.map((r) => r.id).sort()).toEqual(['c1', 'c2', 'g1'].sort())
  })

  it('掛銀行子帳戶的主帳戶歸類到銀行分組', () => {
    const rows = [
      acc({ id: 'g1', name: '主帳戶', account_type: 'account_group', balance: 0 }),
      acc({ id: 'c1', name: '子卡', account_type: 'bank_card', parent_account_id: 'g1', balance: 100 })
    ]
    const groups = computeTypeGroups(rows, t)
    expect(groups.find((g) => g.type === 'account_group')).toBeUndefined()
    const bankGroup = groups.find((g) => g.type === 'bank_card')!
    expect(bankGroup).toBeDefined()
    expect(bankGroup.isLiability).toBe(false)
  })

  it('沒有子帳戶時退回既有 credit-fields 推斷', () => {
    const rows = [acc({ id: 'g1', name: '主帳戶', account_type: 'account_group', billing_day: 5, balance: -500 })]
    const groups = computeTypeGroups(rows, t)
    expect(groups.find((g) => g.type === 'credit_card')).toBeDefined()
    expect(groups.find((g) => g.type === 'account_group')).toBeUndefined()
  })

  it('混合子帳戶類型時 fallback 維持獨立主帳戶分組', () => {
    const rows = [
      acc({ id: 'g1', name: '主帳戶', account_type: 'account_group', balance: 0 }),
      acc({ id: 'c1', name: '信用卡子卡', account_type: 'credit_card', parent_account_id: 'g1', balance: -100 }),
      acc({ id: 'c2', name: '銀行子卡', account_type: 'bank_card', parent_account_id: 'g1', balance: 200 })
    ]
    const groups = computeTypeGroups(rows, t)
    const fallbackGroup = groups.find((g) => g.type === 'account_group')!
    expect(fallbackGroup).toBeDefined()
    expect(fallbackGroup.rows.map((r) => r.id)).toEqual(['g1'])
  })
})

describe('computeCurrencySummary — 信用卡主帳戶群組計入負債(Phase 17 連帶修正)', () => {
  function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
    return p as ReadAccount
  }

  it('掛信用卡子帳戶的 account_group 主帳戶本身也計入負債(不再誤計資產)', () => {
    // g1.balance 對齊後端實際契約(routers/read/workspace.py):account_group
    // 的 balance 已經把子帳戶加總回填到自己身上,不會是 0(那是不存在的資料
    // 形狀,見 buildDoubleCountedChildIds 的雙倍計算修復)。
    const rows = [
      acc({ id: 'cash1', account_type: 'cash', balance: 1000 }),
      acc({ id: 'g1', account_type: 'account_group', balance: -300 }),
      acc({ id: 'c1', account_type: 'credit_card', parent_account_id: 'g1', balance: -300 })
    ]
    const s = computeCurrencySummary(rows)
    expect(s.assetTotal).toBe(1000)
    expect(s.liabilityTotal).toBe(-300)
    expect(s.netWorth).toBe(700)
  })
})

/**
 * 使用者回報(2026-08-12):掛了子帳戶的主帳戶「合計餘額」變兩倍 —— 銀行帳戶
 * 實際只有 1000,主帳戶(account_group)顯示 1000 沒問題(後端已把子帳戶加總
 * 回填到主帳戶自己的 balance),但銀行 type 分組的「合計餘額」卻算出 2000,
 * 因為分組小計把主帳戶(已含子帳戶)跟子帳戶自己都各加了一次。
 * `buildDoubleCountedChildIds` + `computeCurrencySummary`/`computeTypeGroups`
 * 的排除邏輯要把這種雙倍鎖住。
 */
describe('主帳戶 + 子帳戶金額雙倍問題(2026-08-12 使用者回報)', () => {
  function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
    return p as ReadAccount
  }

  it('buildDoubleCountedChildIds:父帳戶在同一批 rows 裡時,子帳戶 id 才算雙重計算', () => {
    const rows = [
      acc({ id: 'g1', account_type: 'account_group', balance: 1000 }),
      acc({ id: 'c1', account_type: 'bank_card', parent_account_id: 'g1', balance: 1000 }),
      acc({ id: 'standalone', account_type: 'bank_card', balance: 500 })
    ]
    const ids = buildDoubleCountedChildIds(rows)
    expect(ids.has('c1')).toBe(true)
    expect(ids.has('g1')).toBe(false)
    expect(ids.has('standalone')).toBe(false)
  })

  it('computeTypeGroups:單一銀行子帳戶掛在主帳戶下,分組小計只算一次(不是子帳戶+主帳戶相加)', () => {
    const rows = [
      acc({ id: 'g1', name: '測試', account_type: 'account_group', balance: 1000 }),
      acc({ id: 'c1', name: '銀行', account_type: 'bank_card', parent_account_id: 'g1', balance: 1000 })
    ]
    const groups = computeTypeGroups(rows, (k: string) => k)
    const bankGroup = groups.find((g) => g.type === 'bank_card')!
    expect(bankGroup).toBeDefined()
    // 反例:修復前的錯誤行為是 1000(主帳戶,已含子帳戶) + 1000(子帳戶自己) = 2000。
    expect(bankGroup.subtotals[0].value).toBe(1000)
    // rows 清單本身仍要保留兩筆(渲染巢狀清單用),只有「加總」排除子帳戶。
    expect(bankGroup.rows.map((r) => r.id).sort()).toEqual(['c1', 'g1'])
  })

  it('computeCurrencySummary:同樣的主帳戶+子帳戶組合,淨資產不能雙倍計算', () => {
    const rows = [
      acc({ id: 'g1', account_type: 'account_group', balance: 1000 }),
      acc({ id: 'c1', account_type: 'bank_card', parent_account_id: 'g1', balance: 1000 })
    ]
    const s = computeCurrencySummary(rows)
    expect(s.assetTotal).toBe(1000)
    expect(s.netWorth).toBe(1000)
  })
})

/**
 * Phase 18(需求 #4)—— 帳戶「納入總餘額」開關。過濾邏輯是純呼叫端行為
 * (`rows.filter(r => r.include_in_total !== false)`,不塞進
 * `computeCurrencySummary`/`computeTypeGroups` 內部),這裡直接鎖同一個過濾
 * 謂詞 + 兩個純函式的組合行為,對齊實際用法。
 *
 * 手測踩到的坑:實際渲染路徑是 `AccountsPage.tsx`(apps/web)的「折算匯總卡」
 * `converted` useMemo(`splitByCurrency`→`computeCurrencySummary`/
 * `mergeGroupsToBase`),不是 `AccountsPanel.tsx` 內部的 `currencyBuckets`
 * ——後者只有在 `hideCurrencyCards=false` 時才會渲染,但 `AccountsPage.tsx`
 * 唯一的呼叫點永遠傳 `hideCurrencyCards`(true),所以 `AccountsPanel.tsx`
 * 那份 hero/donut 其實是死路徑。兩處都要套用同一個過濾謂詞,只改
 * `AccountsPanel.tsx` 會讓瀏覽器手測看到「關掉開關但頂部總額沒變」——
 * SD 原本假設「只有 AccountsPanel.tsx 這一處」已經過時,`AccountsPage.tsx`
 * 的折算匯總卡是後來新增、SD 撰寫時還不存在的第二個總額計算點。
 */
describe('include_in_total 過濾(Phase 18)—— 獨立於 hidden 維度', () => {
  function acc(p: Partial<ReadAccount> & { balance?: number | null }): ReadAccount {
    return p as ReadAccount
  }
  const totalIncluded = (rows: ReadAccount[]) => rows.filter((r) => r.include_in_total !== false)

  it('include_in_total=false 的帳戶不計入 assetTotal/liabilityTotal/netWorth', () => {
    const rows = [
      acc({ id: 'a1', account_type: 'cash', balance: 1000 }),
      acc({ id: 'a2', account_type: 'cash', balance: 500, include_in_total: false }),
      acc({ id: 'a3', account_type: 'credit_card', balance: -300 }),
      acc({ id: 'a4', account_type: 'credit_card', balance: -200, include_in_total: false })
    ]
    const s = computeCurrencySummary(totalIncluded(rows))
    expect(s.assetTotal).toBe(1000) // a2(500)被排除
    expect(s.liabilityTotal).toBe(-300) // a4(-200)被排除
    expect(s.netWorth).toBe(700)
  })

  it('未設置(undefined)視同 true —— 舊資料/新帳戶預設納入', () => {
    const rows = [acc({ id: 'a1', account_type: 'cash', balance: 1000 })]
    expect(totalIncluded(rows)).toHaveLength(1)
    expect(computeCurrencySummary(totalIncluded(rows)).assetTotal).toBe(1000)
  })

  it('與 hidden 互不耦合:隱藏但納入總額的帳戶仍計入總額;顯示但不納入總額的帳戶不計入', () => {
    const rows = [
      acc({ id: 'a1', account_type: 'cash', balance: 100, hidden: true, include_in_total: true }),
      acc({ id: 'a2', account_type: 'cash', balance: 200, hidden: false, include_in_total: false })
    ]
    const s = computeCurrencySummary(totalIncluded(rows))
    expect(s.assetTotal).toBe(100) // a1(隱藏但納入)算進去,a2(顯示但不納入)被排除
  })

  it('底部分組列表(computeTypeGroups)不受 include_in_total 影響 —— 帳戶仍正常顯示', () => {
    const rows = [
      acc({ id: 'a1', account_type: 'cash', balance: 100, include_in_total: false })
    ]
    const groups = computeTypeGroups(rows, (k: string) => k)
    const cashGroup = groups.find((g) => g.type === 'cash')!
    expect(cashGroup).toBeDefined()
    expect(cashGroup.rows.map((r) => r.id)).toEqual(['a1'])
  })
})
