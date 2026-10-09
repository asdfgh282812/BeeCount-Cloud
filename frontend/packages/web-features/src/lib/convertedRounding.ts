/**
 * 外幣折算成本位幣之後的取整方式(以帳本為單位,跟著雲端帳號同步)。
 * 同 App `ConvertedRounding`(lib/services/currency/rate_math.dart);設定存在 profile
 * `appearance.converted_rounding`(`{帳本 ledger_id: 'floor'|'round'|'none'}`)+
 * `converted_rounding_updated_at`,整份 last-write-wins。
 */
export type ConvertedRounding = 'floor' | 'round' | 'none'

export const CONVERTED_ROUNDING_MODES: readonly ConvertedRounding[] = ['floor', 'round', 'none']

/** 沒設定時的預設:本位幣是 TWD/JPY/KRW(沒有小數的幣別)→ 無條件捨去;其它不取整。 */
export function defaultConvertedRounding(base: string | null | undefined): ConvertedRounding {
  const code = (base || '').toUpperCase()
  return code === 'TWD' || code === 'JPY' || code === 'KRW' ? 'floor' : 'none'
}

/** 依 mode 取整折算後的金額:floor = 絕對值往 0 捨去(負數不往下捨);先清掉浮點殘渣。 */
export function applyConvertedRounding(value: number, mode: ConvertedRounding): number {
  if (mode === 'none') return value
  const cleaned = Number(value.toFixed(6))
  return mode === 'floor' ? Math.trunc(cleaned) : Math.round(cleaned)
}

/** 目前帳本實際使用的取整方式:使用者設定 > 本位幣預設。 */
export function resolveConvertedRounding(
  stored: Record<string, string> | null | undefined,
  ledgerId: string | null | undefined,
  base: string | null | undefined,
): ConvertedRounding {
  const raw = ledgerId ? stored?.[ledgerId] : undefined
  return (CONVERTED_ROUNDING_MODES as readonly string[]).includes(raw ?? '')
    ? (raw as ConvertedRounding)
    : defaultConvertedRounding(base)
}
