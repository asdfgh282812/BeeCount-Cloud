/**
 * 跟 mobile `lib/utils/currencies.dart` 保持同一份货币 code 列表(151 个,
 * 覆盖通行 ISO 4217;全部在汇率源 fawaz currency-api 有报价)。新增货币只需在
 * 此追加,前端两端都得同步更新。
 *
 * 币种符号見下方 `currencySymbol()`(全站唯一來源)。币种名称走
 * [Intl.DisplayNames](见 [currencyDisplayName]),主流币种由 i18n key
 * `currency.<CODE>` 覆盖。
 */

const CURRENCY_GROUPS: Array<{ region: string; codes: string[] }> = [
  { region: 'eastAsia', codes: ['CNY', 'JPY', 'KRW', 'HKD', 'TWD', 'MOP', 'MNT', 'KPW'] },
  { region: 'southeastAsia', codes: ['SGD', 'MYR', 'THB', 'IDR', 'PHP', 'VND', 'MMK', 'KHR', 'LAK', 'BND'] },
  { region: 'southAsia', codes: ['INR', 'PKR', 'BDT', 'LKR', 'NPR', 'BTN', 'MVR', 'AFN'] },
  { region: 'centralAsia', codes: ['KZT', 'UZS', 'TJS', 'TMT', 'KGS'] },
  { region: 'middleEast', codes: ['AED', 'SAR', 'ILS', 'TRY', 'QAR', 'KWD', 'BHD', 'OMR', 'JOD', 'LBP', 'IQD', 'IRR', 'YER', 'SYP', 'GEL', 'AMD', 'AZN'] },
  { region: 'europe', codes: ['EUR', 'GBP', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'CZK', 'HUF', 'RUB', 'BYN', 'UAH', 'RON', 'BGN', 'RSD', 'ISK', 'MDL', 'ALL', 'MKD', 'BAM', 'GIP'] },
  { region: 'northAmerica', codes: ['USD', 'CAD', 'MXN'] },
  { region: 'centralAmericaCaribbean', codes: ['GTQ', 'HNL', 'NIO', 'CRC', 'PAB', 'DOP', 'CUP', 'JMD', 'TTD', 'BSD', 'BBD', 'BZD', 'HTG', 'XCD', 'KYD', 'AWG', 'ANG', 'BMD'] },
  { region: 'southAmerica', codes: ['BRL', 'ARS', 'CLP', 'COP', 'PEN', 'UYU', 'PYG', 'BOB', 'VES', 'GYD', 'SRD'] },
  { region: 'oceania', codes: ['AUD', 'NZD', 'FJD', 'PGK', 'SBD', 'TOP', 'VUV', 'WST', 'XPF'] },
  { region: 'africa', codes: ['ZAR', 'EGP', 'NGN', 'KES', 'GHS', 'MAD', 'DZD', 'TND', 'LYD', 'ETB', 'UGX', 'TZS', 'RWF', 'XAF', 'XOF', 'MUR', 'BWP', 'NAD', 'ZMW', 'MWK', 'MZN', 'AOA', 'CDF', 'GMD', 'GNF', 'LRD', 'SLE', 'SDG', 'SSP', 'SOS', 'DJF', 'ERN', 'BIF', 'CVE', 'STN', 'SCR', 'KMF', 'LSL', 'SZL', 'MGA', 'MRU'] },
]

export const CURRENCY_CODES: readonly string[] = CURRENCY_GROUPS.flatMap((g) => g.codes)

export const CURRENCY_REGION_GROUPS = CURRENCY_GROUPS

/**
 * 用 [Intl.DisplayNames] 按 locale 本地化任意 ISO 货币名(en→"US Dollar",
 * zh-CN→"美元")。环境不支持 / 未知 code 时回退 code 本身。
 *
 * 组件层优先用 i18n key `currency.<CODE>` 覆盖(主流币种保留人工译名),
 * 仅在无覆盖时调用本函数 —— 这样长尾币种也能自动按当前语言显示名称。
 */
/**
 * 全站唯一的币种符号來源(需求 #12,2026-08 使用者回饋改善 Phase 12)。
 * 之前有 4 套各自獨立的實作（`format.ts`/`Amount.tsx` 各自的 switch、這裡
 * 原本用 Intl.NumberFormat 派生的版本、annual-report 5 個檔案各自內嵌的
 * 對照表），現在收斂成這一份，其餘全部改成直接 import。
 *
 * 固定字面量對照（不吃 locale）—— 沿用原本 `format.ts`/`Amount.tsx` 的
 * 「純數字符號」行為，不像 Intl.NumberFormat 那樣依 locale 帶出
 * "US$"/"JP¥" 這種國家碼前綴（那樣會讓多數幣別的顯示比現況多長一截）。
 * 使用者明確要求「拿掉¥符號」：CNY/JPY 一律回傳空字串（只顯示數字），
 * 其它幣別維持原本符號不變。未知幣別回傳空字串。
 */
export function currencySymbol(code: string): string {
  switch (code.toUpperCase()) {
    case 'CNY':
    case 'JPY':
      return ''
    case 'USD':
      return '$'
    case 'EUR':
      return '€'
    case 'HKD':
      return 'HK$'
    case 'GBP':
      return '£'
    default:
      return ''
  }
}

/** 币种码 → ISO 国家码(国旗用)。前两位派生;欧盟/台币/区域货币特例。
 *  与 App lib/utils/currencies.dart countryCodeForCurrency 对齐。 */
const _CURRENCY_COUNTRY: Record<string, string | null> = {
  EUR: 'EU',
  TWD: 'TW', // 新台币显示台湾旗
  XAF: null, XOF: null, XCD: null, XPF: null,
  XDR: null, XAU: null, XAG: null, XPT: null, XPD: null,
}

export function countryCodeForCurrency(code: string): string | null {
  const c = (code || '').trim().toUpperCase()
  if (c in _CURRENCY_COUNTRY) return _CURRENCY_COUNTRY[c]
  if (c.length < 2) return null
  return c.slice(0, 2)
}

export function currencyDisplayName(code: string, locale: string): string {
  const upper = code.toUpperCase()
  try {
    const dn = new Intl.DisplayNames([locale], { type: 'currency' })
    return dn.of(upper) || upper
  } catch {
    return upper
  }
}

// ---------------------------------------------------------------------------
// v30 交易折算(记账/编辑提交用)。规则与 App 端对齐:
//   - 有效汇率 = 手动 override > 自动源(fawaz,1 base = x quote → 除)
//   - override 方向是「1 quote = rate base」→ 乘
//   - 编辑模式币种未变 → 返回 null(两字段都不发,金额变化由 server L14 按
//     该笔隐含汇率联动 —— 避免「只改备注折算被今日汇率重算」的快照漂移)
//   - 改回本位币 → 显式发 currency_code=base + native=amount(server 语义
//     None=不变,不发就改不回来)
//   - 缺汇率 → throw,调用方阻断保存(绝不静默 1:1)
// ---------------------------------------------------------------------------

import { fetchExchangeRateOverrides, fetchExchangeRates } from '@beecount/api-client'

export type CurrencyFields = { currency_code: string; native_amount: number }

type RatesEntry = {
  at: number
  auto: Record<string, unknown>
  /** quote(大写) → rate(1 quote = rate base) */
  overrides: Map<string, number>
}
const _ratesCache = new Map<string, RatesEntry>()
const _RATES_TTL_MS = 5 * 60 * 1000

async function _effectiveRates(token: string, base: string): Promise<RatesEntry> {
  const hit = _ratesCache.get(base)
  if (hit && Date.now() - hit.at < _RATES_TTL_MS) return hit
  const [auto, allOverrides] = await Promise.all([
    fetchExchangeRates(token, base),
    fetchExchangeRateOverrides(token).catch(() => []),
  ])
  const overrides = new Map<string, number>()
  for (const o of allOverrides) {
    if ((o.base_currency || '').toUpperCase() !== base) continue
    const r = Number(o.rate)
    if (Number.isFinite(r) && r > 0) overrides.set((o.quote_currency || '').toUpperCase(), r)
  }
  const entry: RatesEntry = { at: Date.now(), auto: auto.rates || {}, overrides }
  _ratesCache.set(base, entry)
  return entry
}

/**
 * 币种选择弹窗展示用:各币种对 base 的汇率 map(key=quote 大写,value=1 quote ≈ value base)。
 * 复用 _effectiveRates(5min 缓存 + 手动 override 合并)。fawaz auto 方向是
 * 1 base = rate quote,取倒数;手动 override 本就是 1 quote = rate base,直接覆盖 auto。
 * 账本币种弹窗 / 记账币种选择共用,替代各处手写的 fetchExchangeRates + 1/y。
 */
export async function loadRatesToBase(
  token: string,
  base: string
): Promise<Record<string, number>> {
  const entry = await _effectiveRates(token, base.trim().toUpperCase())
  const out: Record<string, number> = {}
  for (const [q, v] of Object.entries(entry.auto)) {
    const y = Number(v)
    if (Number.isFinite(y) && y > 0) out[q.toUpperCase()] = 1 / y
  }
  for (const [q, r] of entry.overrides) {
    if (Number.isFinite(r) && r > 0) out[q] = r // 手动汇率优先
  }
  return out
}

export async function resolveCurrencyFields(opts: {
  token: string
  ledgerBase: string
  /** 交易币种(表单所选;'' 视作本位币) */
  currency: string
  amount: number
  /** 编辑模式必传:该笔原币种(''=本位币)。币种未变 → 返回 null。
   *  新建传 undefined。 */
  originalCurrency?: string | null
}): Promise<CurrencyFields | null> {
  const base = opts.ledgerBase.trim().toUpperCase()
  const eff = (opts.currency || base).trim().toUpperCase()
  if (opts.originalCurrency !== undefined) {
    const orig = (opts.originalCurrency || base).trim().toUpperCase()
    if (eff === orig) return null // 币种未变:金额联动交给 server L14,防漂移
  }
  if (eff === base) {
    // 本位币(含「改回本位币」):隐含汇率 1
    return { currency_code: base, native_amount: opts.amount }
  }
  const entry = await _effectiveRates(opts.token, base)
  const manual = entry.overrides.get(eff)
  if (manual !== undefined) {
    return { currency_code: eff, native_amount: opts.amount * manual }
  }
  const raw = entry.auto[eff] ?? entry.auto[eff.toLowerCase()]
  const rate = Number(raw)
  if (!Number.isFinite(rate) || rate <= 0) throw new Error('rate missing')
  // fawaz 方向 1 base = rate quote → quote 金额折 base 要除
  return { currency_code: eff, native_amount: opts.amount / rate }
}

/**
 * 跨幣別自動換算(2026-08):任兩個幣別之間換算,透過 `ratesToBase`(見
 * {@link loadRatesToBase},已含手動 override 合併)以帳本本位幣當 pivot ——
 * `fromCode`/`toCode` 任一邊等於 `base` 就省一次除法,兩邊都不是 base 則
 * 先折成 base 金額再折成目標幣別。缺其中一邊的匯率回傳 `null`,呼叫端要
 * 阻斷送出(比照 {@link resolveCurrencyFields} 既有「拉不到匯率絕不靜默
 * 1:1」原則),不是這個函式自己 throw——因為呼叫端(表單即時預覽)通常
 * 需要區分「還沒選好帳戶/幣別」跟「真的缺匯率」兩種情境。
 */
export function convertBetween(
  amount: number,
  fromCode: string,
  toCode: string,
  ratesToBase: Record<string, number>,
  base: string
): number | null {
  const from = fromCode.trim().toUpperCase()
  const to = toCode.trim().toUpperCase()
  const b = base.trim().toUpperCase()
  if (from === to) return amount
  const fromRate = from === b ? 1 : ratesToBase[from]
  if (!Number.isFinite(fromRate) || (fromRate as number) <= 0) return null
  const amountInBase = amount * (fromRate as number)
  if (to === b) return amountInBase
  const toRate = ratesToBase[to]
  if (!Number.isFinite(toRate) || toRate <= 0) return null
  return amountInBase / toRate
}

/**
 * 跨幣別自動換算(2026-08-14 補充)的「有效匯率」判定優先序:使用者需求
 * 是「換算金額有誤時,應該以直接改換算後金額為主要修正手段,而非先反推
 * 該改多少匯率(雖然改匯率一樣能連動改變換算後金額)」,所以優先序是
 * `amountOverrideStr`(換算後金額) > `rateOverrideStr`(匯率) > 自動匯率
 * ({@link convertBetween})。`baseAmountNum`(換算前/from 幣別金額)<= 0
 * 時 amountOverride 無法反推匯率(除以 0),視為不可用、退回下一順位。
 */
export function resolveEffectiveRate(
  rateOverrideStr: string,
  amountOverrideStr: string,
  baseAmountNum: number,
  fromCode: string,
  toCode: string,
  ratesToBase: Record<string, number>,
  base: string
): number | null {
  const amountOverride = Number(amountOverrideStr)
  if (
    baseAmountNum > 0 &&
    amountOverrideStr.trim() &&
    Number.isFinite(amountOverride) &&
    amountOverride > 0
  ) {
    return amountOverride / baseAmountNum
  }
  const rateOverride = Number(rateOverrideStr)
  if (rateOverrideStr.trim() && Number.isFinite(rateOverride) && rateOverride > 0) {
    return rateOverride
  }
  return convertBetween(1, fromCode, toCode, ratesToBase, base)
}

/** 列表/详情的「外币交易」判定:折算快照存在且 ≠ 原币值。 */
export function isForeignTx(row: {
  currency_code?: string | null
  native_amount?: number | null
  amount: number
}): boolean {
  return Boolean(
    row.currency_code && row.native_amount != null && row.native_amount !== row.amount
  )
}

export interface TransferConversionDisplay {
  /** 转入帐户自身币别的金额,直接来自 `to_amount`(使用者实际输入/确认的
   *  到账数字),不经过任何即时匯率再加工——不管这个币别是不是帐本本位
   *  币,这个数字本身永远精确。 */
  amount: number
  /** 转入帐户的币别代码。 */
  currencyCode: string
  /** true = 这个币别剛好是帐本本位币,沿用既有「≈」语意(已按记帐时匯率
   *  折算为帐本本位币);false = 单纯呈现转入帐户自己币别的到账金额,不
   *  宣称这是本位币换算值,渲染端要换一个不提「本位币」的 tooltip。 */
  isBaseCurrency: boolean
}

/**
 * 转帐交易「≈折算金额」的显示专用换算(2026-09-18 使用者反馈:折算金额跟
 * 实际转入金额对不上,后续测试又发现「本位币转外币」方向完全没有任何换算
 * 提示)。`native_amount` 故意折算「转出方」金额,专供信用卡群组合并帐单
 * (`compute_group_billing`)当「已缴金额」的换算基准用,跟使用者实际转入
 * 多少是两个独立概念,不能直接拿来当这里的显示依据(动 `native_amount`
 * 本身会让帐单重新出现汇差残值,已修过两轮)。
 *
 * 这里改成直接用 `to_amount`(转入帐户自身币别的金额,使用者实际输入/确
 * 认的到账数字,含 fx_rate_override/fx_amount_override 生效后的结果)—— 不
 * 管转入帐户币别是不是帐本本位币,这个数字都是精确值,不需要查即时匯率。
 * 只有转出/转入帐户币别相同(非跨币别转帐,`to_amount === amount` 恆成立)
 * 时才不显示,避免冗余。两边都是外币且互不相同(既不是本位币也彼此不同)
 * 的三币情境仍然显示——`to_amount` 一样是精确值,没有理由跟着限制只准
 * 「其中一边是本位币」才显示。
 */
export function transferConversionDisplay(
  row: {
    tx_type?: string | null
    amount: number
    to_amount?: number | null
    from_account_name?: string | null
    to_account_name?: string | null
  },
  accountCurrencyByName: Map<string, string> | undefined,
  ledgerBaseCurrency: string | undefined
): TransferConversionDisplay | null {
  if (row.tx_type !== 'transfer' || row.to_amount == null || !accountCurrencyByName) return null
  const fromCurrency = accountCurrencyByName.get((row.from_account_name || '').trim().toLowerCase())
  const toCurrency = accountCurrencyByName.get((row.to_account_name || '').trim().toLowerCase())
  if (!fromCurrency || !toCurrency) return null
  const from = fromCurrency.trim().toUpperCase()
  const to = toCurrency.trim().toUpperCase()
  if (from === to) return null
  return {
    amount: row.to_amount,
    currencyCode: to,
    isBaseCurrency: !!ledgerBaseCurrency && to === ledgerBaseCurrency.trim().toUpperCase()
  }
}
