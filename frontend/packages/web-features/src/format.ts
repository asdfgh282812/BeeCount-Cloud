import type { ReadLedger } from '@beecount/api-client'

import { currencySymbol } from './lib/currencies'

/** 去掉小数点后多余的尾随 0(657.00 → 657、657.50 → 657.5),用户不想在没有
 *  小数的金额上一律拖一条 ".00"(见「所有显示金额」需求)。 */
function trimZero(s: string): string {
  return s.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
}

/** 供 `.toFixed(2)` 手写格式化的调用点直接换用:整数不带小数点,有小数才
 *  保留(最多两位,去尾随 0)。 */
export function formatAmountTrimmed(value: number): string {
  return trimZero(value.toFixed(2))
}

export function formatAmountCny(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `CNY ${trimZero(value.toFixed(2))}`
}

/**
 * 紧凑金额格式化（原本对齐 mobile `utils/format_utils.dart#formatBalance`,
 * 2026-08-12 使用者反馈「金额太快被折成'X万'看不出实际数字」后,中文环境的
 * 折算门槛从 1 万上调到 10 万 —— web 端自此与 mobile 端門檻不同,是刻意分歧,
 * 不是遗漏同步）。
 *
 * - 中文环境：< 10 万完整显示数值（含千分位分组）；≥ 10 万才按 1-2 位小数
 *   折算成 "X.X万"。万字单位由 `wanUnit` 指定 —— zh-CN「万」/ zh-TW「萬」,
 *   默认「万」。
 * - 其他环境：≥ 100 万折算 M、≥ 1 千折算 k，< 1 千保留两位（未变动）。
 * - currencyCode 传 null 时不带币种符号（BankCardTile 独立展示 currency pill，
 *   不想在金额字符串里再重复一次）。
 */
export function formatBalanceCompact(
  value: number | null | undefined,
  currencyCode?: string | null,
  opts?: { chinese?: boolean; wanUnit?: string }
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const chinese = opts?.chinese ?? true
  const absVal = Math.abs(value)
  const symbol = currencyCode ? currencySymbol(currencyCode) : ''
  const sign = value >= 0 ? symbol : `-${symbol}`

  if (chinese) {
    if (absVal < 100000) {
      const formatted = absVal.toLocaleString('zh-CN', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2
      })
      return `${sign}${formatted}`
    }
    const wan = absVal / 10000
    const r1 = Number(wan.toFixed(1))
    const err1 = Math.abs(r1 * 10000 - absVal)
    const threshold = wan >= 10 ? 100 : 50
    const formatted = err1 > threshold ? wan.toFixed(2) : wan.toFixed(1)
    return `${sign}${trimZero(formatted)}${opts?.wanUnit ?? '万'}`
  }

  if (absVal >= 1_000_000) {
    const m = absVal / 1_000_000
    const r1 = Number(m.toFixed(1))
    const formatted = Math.abs(r1 * 1_000_000 - absVal) > 1000 ? m.toFixed(2) : m.toFixed(1)
    return `${sign}${trimZero(formatted)}M`
  }
  if (absVal >= 1000) {
    const k = absVal / 1000
    const r1 = Number(k.toFixed(1))
    const formatted = Math.abs(r1 * 1000 - absVal) > 100 ? k.toFixed(2) : k.toFixed(1)
    return `${sign}${trimZero(formatted)}k`
  }
  return `${sign}${trimZero(absVal.toFixed(2))}`
}

/**
 * 紧凑「轴刻度」数字格式化 —— 图表 Y 轴 / 日历格这类不带币种、且小数值要求取整
 * 的场景。和 `formatBalanceCompact` 的区别:小于缩写阈值时直接取整(轴刻度不需要
 * 跟金额一样保留 .00)。
 *
 * - 中文环境(`chinese: true`):≥ 1 万 → "X.X<wanUnit>",单位文案由调用方传入
 *   (zh-CN 用「万」、zh-TW 用「萬」,默认「万」)。
 * - 英文环境(`chinese: false`):≥ 100 万 → "X.XM"、≥ 1 千 → "X.Xk",否则取整 ——
 *   符合英文区习惯,不再出现「万」。
 */
export function formatCompactTick(
  value: number,
  opts: { chinese: boolean; wanUnit?: string }
): string {
  const abs = Math.abs(value)
  if (opts.chinese) {
    if (abs < 10000) return value.toFixed(0)
    return `${(value / 10000).toFixed(1)}${opts.wanUnit ?? '万'}`
  }
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}k`
  return value.toFixed(0)
}

/**
 * 分期付款年利率顯示轉換(需求 #17,2026-08 Phase 12)。後端/表單狀態一律
 * 儲存小數分數(`0.06` = 6%/年,`services/installment_amortization.py`
 * 既有數學不變),UI 輸入框改成讓使用者直接打整數百分比(`6`),這兩個函式
 * 只做「顯示 ⇄ 儲存」的來回換算,不影響任何送出/計算邏輯。四捨五入到固定
 * 精度是為了避免 `0.06 * 100` 這類二進位浮點運算殘留 `6.000000000000001`
 * 這種雜訊字元。四個呼叫點(`TransactionsPanel.tsx`/`InstallmentPlansPanel.tsx`
 * 建立表單 + 重新分期彈窗/`AccountDetailDialog.tsx`)共用這一份,不要各自
 * 複製一份轉換邏輯。
 */
export function interestRateToPercentDisplay(fraction: string): string {
  const trimmed = fraction.trim()
  if (trimmed === '') return ''
  const n = Number(trimmed)
  if (!Number.isFinite(n)) return ''
  return String(Number((n * 100).toFixed(4)))
}

export function percentDisplayToInterestRate(percent: string): string {
  const trimmed = percent.trim()
  if (trimmed === '') return ''
  const n = Number(trimmed)
  if (!Number.isFinite(n)) return ''
  return String(Number((n / 100).toFixed(6)))
}

export function formatIsoDateTime(value: string | null | undefined): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toISOString().slice(0, 19).replace('T', ' ')
}

export function formatLedgerLabel(ledger: ReadLedger, roleLabel: string): string {
  return `${ledger.ledger_name} [${roleLabel}]`
}
