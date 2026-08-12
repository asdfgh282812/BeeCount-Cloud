import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'

/**
 * 资产页多币种聚合的纯逻辑核心。
 *
 * 铁律:**资产统计绝不跨币种相加** —— $1000 不是 ¥1000。没有汇率基建,也不做换算,
 * 所以净值/资产/负债都先按币种切分再各算各的(单币种就退化成 1 组,展示维持原样)。
 * 这块逻辑抽到 lib 是为了能脱离 React 组件单测,锁住"不跨币种合并"这个契约 ——
 * 历史上这页就是因为裸加 balance 把多币种加错了。
 */

export type AssetSummary = {
  assetTotal: number
  /** 带符号:欠款为负、溢缴为正。展示"总负债"时取 Math.abs,绝不在聚合层提前 abs。 */
  liabilityTotal: number
  netWorth: number
}

/**
 * 一个账户类型分组(构成饼图 / 分组列表的最小单元)。subtotals 按币种拆 ——
 * 单币种入参时每组只有 1 条,跨币种(底部混合列表)时各币种独立累计、绝不相加。
 * 定义放 lib 是为了让 {@link mergeGroupsToBase} 这类纯聚合脱离 React 组件单测。
 */
export type AssetGroup = {
  type: string
  label: string
  color: string
  isLiability: boolean
  rows: ReadAccount[]
  subtotals: { currency: string; value: number }[]
}

/** 负债类账户类型:余额带符号计入净值(欠款为负扣减、溢缴为正增加),展示欠款时才取 abs。 */
export const LIABILITY_TYPES = new Set(['credit_card', 'loan'])

/** 账户展示余额:优先 server 聚合后的 balance(含所有交易),否则回退 initial_balance。 */
export function accountBalance(row: ReadAccount): number {
  const stats = row as ReadAccount & { balance?: number | null }
  return typeof stats.balance === 'number' && stats.balance !== null
    ? stats.balance
    : row.initial_balance ?? 0
}

/** `account_group`(主帳戶)自身有沒有掛信用卡帳單欄位(額度/帳單日/還款日
 *  任一有值)—— 判斷"这个群组具体是信用卡群组还是别的"的既有推断法(区别于
 *  真正的 account_type)。单一来源,供 {@link resolveAccountGroupDisplayType}
 *  (分组归属)与 AccountListRow(单列信用卡样式渲染)共用,避免各自维护一份。 */
export function hasCreditBillingFields(row: ReadAccount): boolean {
  return (
    typeof row.credit_limit === 'number' ||
    Boolean(row.billing_day) ||
    Boolean(row.payment_due_day)
  )
}

/**
 * 需求 #1(2026-08 Phase 17):按 `parent_account_id` 建立「父帳戶 id → 子帳戶
 * 列表」的桶,供 {@link resolveRowDisplayType} 判断某个 `account_group` 底下
 * 挂了哪些子帐户。入参通常是同一批要一起分组/汇总的 rows(例如某个币种内的
 * 全量账户),顺序不影响结果。
 */
export function buildParentChildrenMap(rows: ReadAccount[]): Map<string, ReadAccount[]> {
  const map = new Map<string, ReadAccount[]>()
  for (const row of rows) {
    if (!row.parent_account_id) continue
    const arr = map.get(row.parent_account_id)
    if (arr) arr.push(row)
    else map.set(row.parent_account_id, [row])
  }
  return map
}

/**
 * 需求 #1(2026-08 Phase 17):`account_group`(主帳戶,合併帳單容器)分组归属
 * 判断 —— 底下挂的子帐户类型决定它该归到「信用卡」分组还是「银行」分组等,
 * 而不是永远自成一个独立的「主帐户」分组(对齐 Moze:主帳戶按其子帳戶內容歸類,
 * 使用者截图里"玉山信用卡"主帐户留在信用卡分组正是这个预期)。
 *
 * - 已有子帐户:全部同一种 account_type → 用该类型;混合多种类型 → 保守
 *   fallback 回 'account_group' 独立分组(避免猜错,混挂视为边界情况)。
 * - 还没有子帐户(刚建立):退回既有的 credit-billing 字段推断(见
 *   {@link hasCreditBillingFields})—— 有额度/帐单日/还款日任一有值 →
 *   'credit_card';否则维持 'account_group',等挂上子帐户后才会正确归类
 *   (过渡状态,不影响资料正确性,只影响新建当下的视觉分组)。
 */
export function resolveAccountGroupDisplayType(group: ReadAccount, childRows: ReadAccount[]): string {
  if (childRows.length > 0) {
    const types = new Set(childRows.map((c) => c.account_type || 'other'))
    if (types.size === 1) return [...types][0]
    return 'account_group'
  }
  return hasCreditBillingFields(group) ? 'credit_card' : 'account_group'
}

/** 单行的「显示用类型」:非 `account_group` 原样返回 `account_type`;
 *  `account_group` 则走 {@link resolveAccountGroupDisplayType}。分组
 *  (`computeTypeGroups`)与净值统计(`computeCurrencySummary`)都要用这个显示
 *  用类型而非原始 `account_type`,才能让信用卡主帳戶群組正确歸類且计入负债。 */
export function resolveRowDisplayType(
  row: ReadAccount,
  childrenByParent: Map<string, ReadAccount[]>
): string {
  if (row.account_type !== 'account_group') return row.account_type || 'other'
  return resolveAccountGroupDisplayType(row, childrenByParent.get(row.id) || [])
}

/**
 * 按币种切分账户 —— 所有跨币种聚合的第一步。币种缺省按 CNY,统一大写归一
 * (`usd` / `USD` 视作同一种)。返回的 Map 保持插入顺序。
 */
export function splitByCurrency(rows: ReadAccount[]): Map<string, ReadAccount[]> {
  const map = new Map<string, ReadAccount[]>()
  for (const row of rows) {
    const cur = (row.currency || 'CNY').toUpperCase()
    const arr = map.get(cur)
    if (arr) arr.push(row)
    else map.set(cur, [row])
  }
  return map
}

/**
 * 找出「父帳戶(account_group)也在同一批 rows 裡」的子帳戶 id 集合。後端
 * (`routers/read/workspace.py`)已經把子帳戶的 `balance`/`income_total`/
 * `expense_total` 加總回填到 `account_group` 主帳戶自己身上(見該檔案
 * "主帳戶…子帳戶的統計要加總回填到群組自己身上"註解),所以主帳戶的
 * `accountBalance()` 本身就已經是「自己 + 全部子帳戶」的合計。前端任何對
 * 一批 rows 做金額加總(淨值 / 分組小計)時,若同時把主帳戶跟它的子帳戶都算
 * 進去,子帳戶的錢就會被重複計一次 —— 這正是使用者回報「掛了子帳戶的分組
 * 小計變兩倍」的根因。呼叫方應該用這個集合把子帳戶從「加總」裡剔除(仍保留
 * 在 `rows` 清單裡供巢狀渲染),只在父帳戶身上算一次。
 */
export function buildDoubleCountedChildIds(rows: ReadAccount[]): Set<string> {
  const idSet = new Set(rows.map((r) => r.id))
  const result = new Set<string>()
  for (const row of rows) {
    if (row.parent_account_id && idSet.has(row.parent_account_id)) {
      result.add(row.id)
    }
  }
  return result
}

/**
 * 单币种净值汇总。净值 = 资产 + 负债,跟 mobile `local_account_repository.getNetWorthBreakdown`
 * 口径一致。负债类账户(信用卡/贷款)逐账户按正负拆开算,而不是先加总再看整组净额的正负
 * (2026-08-12 使用者回报:同一张卡溢缴 500,顶部「负债」方块却仍算成负债、没被当资产)——
 * 欠款(负)留在 liabilityTotal 扣减净值;溢缴(正,还多缴的钱,经济上是应收/资产头寸)记进
 * assetTotal。两个账户一张欠 300、一张溢缴 800 这种混合场景,也要先各自拆完再加总,不能
 * 把两张卡的余额先加成净额 500 才拆 —— 那样欠款的 300 会凭空消失。
 * 历史教训:这里曾逐账户 `Math.abs` 再做减法,正常数据(欠款为负)下结果碰巧相同,
 * 直到溢缴/正余额负债账户出现才暴露 —— 把多打进去的钱又反向当欠款扣了一遍。
 *
 * 入参**必须是同一币种**的账户(由 {@link splitByCurrency} 保证)—— 传混币种进来
 * 得到的就是那个错的合并数字,这正是本模块要避免的。
 */
export function computeCurrencySummary(rows: ReadAccount[]): AssetSummary {
  // 掛信用卡子帳戶的 account_group 主帳戶要计入负债(需求 #1,Phase 17)——
  // 用显示用类型(resolveRowDisplayType)而非原始 account_type 判断,否则这类
  // 主帳戶群組的小计仍会被当资产而非负债计入净值。
  const childrenByParent = buildParentChildrenMap(rows)
  // 主帳戶的 balance 已含子帳戶加總(見 buildDoubleCountedChildIds 註解),
  // 子帳戶自己不能再重複計一次,否則淨值/資產/負債都會膨脹成兩倍。
  const doubleCountedIds = buildDoubleCountedChildIds(rows)
  let assetTotal = 0
  let liabilityTotal = 0
  for (const row of rows) {
    if (doubleCountedIds.has(row.id)) continue
    const raw = accountBalance(row)
    const displayType = resolveRowDisplayType(row, childrenByParent)
    if (LIABILITY_TYPES.has(displayType)) {
      if (raw > 0) assetTotal += raw
      else liabilityTotal += raw
    } else {
      assetTotal += raw
    }
  }
  return { assetTotal, liabilityTotal, netWorth: assetTotal + liabilityTotal }
}

/** 有效汇率:override(1 quote = x base)优先;否则代理自动值(1 base = x quote)取倒数。缺失返回 null,绝不回落 1。 */
export function effectiveRateToBase(
  quote: string, base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[]
): { rate: number; source: 'manual' | 'auto'; date?: string } | null {
  if (quote === base) return { rate: 1, source: 'auto' }
  const ov = overrides.find((o) => o.base_currency === base && o.quote_currency === quote)
  if (ov) {
    const r = Number(ov.rate)
    return Number.isFinite(r) && r > 0 ? { rate: r, source: 'manual' } : null
  }
  const raw = Number(auto?.rates?.[quote])
  if (!Number.isFinite(raw) || raw <= 0) return null
  return { rate: 1 / raw, source: 'auto', date: auto!.rate_date }
}

/**
 * 把「每币种各自的类型分组」按汇率折算到主币种后,合并成一份主币种构成 ——
 * 用于折算汇总视图的合并饼图(`AssetsCompositionMini` currency=base)。
 *
 * 入参 `buckets` 是各币种的分组列表(单币种页里的 `computeTypeGroups` 产物按币种装好)。
 * 对每个币种取 {@link effectiveRateToBase},把该币种每组的 subtotal 求和后 × rate
 * 折进主币种,再按 type 跨币种累加成一份 `AssetGroup[]`。
 *
 * **铁律对齐**:缺失汇率(`effectiveRateToBase` 返回 null)的整币种直接剔除,
 * **绝不按 1 折入** —— 与净资产/资产/负债折算同口径。输出每组只有 1 条 subtotal(主币种),
 * 顺序沿用首次出现的类型顺序(即 `computeTypeGroups` 的 ACCOUNT_ORDER)。
 */
export function mergeGroupsToBase(
  buckets: { currency: string; groups: AssetGroup[] }[],
  base: string,
  auto: ExchangeRatesResponse | null,
  overrides: ExchangeRateOverride[],
): AssetGroup[] {
  // type → 累加器。保留首次出现的 label/color/isLiability 与出现顺序。
  const merged = new Map<string, AssetGroup>()
  for (const bucket of buckets) {
    const eff = effectiveRateToBase(bucket.currency.toUpperCase(), base, auto, overrides)
    if (!eff) continue // 缺失汇率:整币种剔除,绝不按 1 折入
    for (const group of bucket.groups) {
      const sub = group.subtotals.reduce((s, x) => s + x.value, 0) * eff.rate
      const existing = merged.get(group.type)
      if (existing) {
        existing.subtotals[0].value += sub
        existing.rows = existing.rows.concat(group.rows)
      } else {
        merged.set(group.type, {
          type: group.type,
          label: group.label,
          color: group.color,
          isLiability: group.isLiability,
          rows: [...group.rows],
          subtotals: [{ currency: base, value: sub }],
        })
      }
    }
  }
  return [...merged.values()]
}
