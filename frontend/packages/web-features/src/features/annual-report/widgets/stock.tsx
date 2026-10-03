import type { StockCurrencySummary } from '../data'
import { currencySymbol } from '../../../lib/currencies'

/**
 * 股票頁共用小工具。漲跌色跟 App/Web 設定「股票漲跌色」一致:
 * AppShell 會把 `appearance.stock_up_is_red` 寫到 `<html data-stock-color="red|green">`
 * (預設紅漲綠跌,台股習慣),這裡直接讀,不另外存一份設定。
 */
export function stockTone() {
  const upIsRed =
    typeof document === 'undefined' || document.documentElement.dataset.stockColor !== 'green'
  const gainHex = upIsRed ? '#FB7185' : '#34D399'
  const lossHex = upIsRed ? '#34D399' : '#FB7185'
  return {
    gainHex,
    lossHex,
    gainClass: upIsRed ? 'text-rose-300' : 'text-emerald-300',
    lossClass: upIsRed ? 'text-emerald-300' : 'text-rose-300',
    pick: (v: number) => (v > 0 ? gainHex : v < 0 ? lossHex : '#A8A29E'),
    pickClass: (v: number) =>
      v > 0
        ? upIsRed ? 'text-rose-300' : 'text-emerald-300'
        : v < 0
          ? upIsRed ? 'text-emerald-300' : 'text-rose-300'
          : 'text-white/80',
  }
}

/** 帶正負號的金額:前綴(+¥ / -$)與絕對值分開,方便丟給 BigNumber。 */
export function signedParts(v: number, currency: string): { prefix: string; abs: number } {
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  return { prefix: `${sign}${currencySymbol(currency)}`, abs: Math.abs(v) }
}

export function fmtMoney(v: number, currency: string): string {
  return `${currencySymbol(currency)}${Math.round(v).toLocaleString()}`
}

export function fmtSignedMoney(v: number, currency: string): string {
  const { prefix, abs } = signedParts(v, currency)
  return `${prefix}${Math.round(abs).toLocaleString()}`
}

/** 多幣別時的切換 chip(只有一種幣別時不渲染)。 */
export function CurrencyChips({
  currencies,
  index,
  onChange,
}: {
  currencies: StockCurrencySummary[]
  index: number
  onChange: (i: number) => void
}) {
  if (currencies.length < 2) return null
  return (
    <div className="mb-6 flex flex-wrap gap-2">
      {currencies.map((c, i) => (
        <button
          key={c.currency}
          type="button"
          onClick={() => onChange(i)}
          className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
            i === index
              ? 'border-amber-300/60 bg-amber-300/20 text-amber-200'
              : 'border-white/15 bg-white/5 text-white/60 hover:bg-white/10'
          }`}
        >
          {c.currency || '—'}
        </button>
      ))}
    </div>
  )
}

export const MARKET_FLAG: Record<string, string> = {
  TW: '🇹🇼', TWO: '🇹🇼', US: '🇺🇸', HK: '🇭🇰', JP: '🇯🇵', SS: '🇨🇳', SZ: '🇨🇳', KS: '🇰🇷', KQ: '🇰🇷', LSE: '🇬🇧',
}
