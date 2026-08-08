import { useCallback, type FocusEvent, type InputHTMLAttributes, type KeyboardEvent } from 'react'

import { cn } from '../lib/cn'
import { Input } from './input'

/**
 * 手寫的四則運算算式求值器(+ - * / 與括號,支援小數與一元負號)。刻意不用
 * `eval()`/`Function()`——這裡是使用者在自己瀏覽器裡打的金額算式,雖然沒有
 * 外部注入風險,但手寫 parser 本來就比動態求值安全、可預期,遇到無法解析的
 * 輸入時能明確回傳 `null` 而不是拋出例外或執行任意字串。
 */
function evaluateAmountExpression(raw: string): number | null {
  const src = raw.trim()
  if (!src) return null
  // 只允許數字、+ - * / ( ) . 與空白,避免奇怪字元進到 parser。
  if (!/^[0-9+\-*/().\s]+$/.test(src)) return null

  let pos = 0

  const peek = (): string | undefined => src[pos]
  const skipSpace = () => {
    while (pos < src.length && /\s/.test(src[pos])) pos += 1
  }

  const parseNumber = (): number | null => {
    skipSpace()
    const start = pos
    while (pos < src.length && /[0-9.]/.test(src[pos])) pos += 1
    if (pos === start) return null
    const text = src.slice(start, pos)
    if ((text.match(/\./g) || []).length > 1) return null
    const value = Number(text)
    return Number.isFinite(value) ? value : null
  }

  const parseUnary = (): number | null => {
    skipSpace()
    if (peek() === '+') {
      pos += 1
      return parseUnary()
    }
    if (peek() === '-') {
      pos += 1
      const value = parseUnary()
      return value === null ? null : -value
    }
    if (peek() === '(') {
      pos += 1
      const value = parseExpression()
      skipSpace()
      if (peek() !== ')') return null
      pos += 1
      return value
    }
    return parseNumber()
  }

  const parseTerm = (): number | null => {
    let value = parseUnary()
    if (value === null) return null
    for (;;) {
      skipSpace()
      const op = peek()
      if (op !== '*' && op !== '/') break
      pos += 1
      const rhs = parseUnary()
      if (rhs === null) return null
      if (op === '*') value *= rhs
      else {
        if (rhs === 0) return null
        value /= rhs
      }
    }
    return value
  }

  const parseExpression = (): number | null => {
    let value = parseTerm()
    if (value === null) return null
    for (;;) {
      skipSpace()
      const op = peek()
      if (op !== '+' && op !== '-') break
      pos += 1
      const rhs = parseTerm()
      if (rhs === null) return null
      value = op === '+' ? value + rhs : value - rhs
    }
    return value
  }

  const result = parseExpression()
  skipSpace()
  if (result === null || pos !== src.length || !Number.isFinite(result)) return null
  return result
}

/** 四捨五入到 2 位小數,並去除多餘的尾端零(1.50 → 1.5,3.00 → 3)。 */
function formatAmountResult(value: number): string {
  const rounded = Math.round(value * 100) / 100
  return String(rounded)
}

export type AmountInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> & {
  value: string
  onChange: (value: string) => void
}

/**
 * 比照 Moze(record/introduction)的金額輸入:可以直接打算式(`100+50`),
 * 按 `=` 鍵求值取代成結果。`Enter`/`onBlur` 也觸發同樣求值當保險(使用者
 * 忘記按 `=` 直接切走欄位);算式無效時不理會,保留原始輸入文字讓使用者
 * 自己修正。
 */
export function AmountInput({ value, onChange, onKeyDown, onBlur, ...props }: AmountInputProps) {
  const tryEvaluate = useCallback(() => {
    const result = evaluateAmountExpression(value)
    if (result !== null) {
      onChange(formatAmountResult(result))
    }
  }, [value, onChange])

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === '=' || event.key === 'Enter') {
        event.preventDefault()
        tryEvaluate()
      }
      onKeyDown?.(event)
    },
    [tryEvaluate, onKeyDown]
  )

  const handleBlur = useCallback(
    (event: FocusEvent<HTMLInputElement>) => {
      tryEvaluate()
      onBlur?.(event)
    },
    [tryEvaluate, onBlur]
  )

  return (
    <Input
      {...props}
      className={cn(props.className)}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={handleBlur}
      inputMode="decimal"
    />
  )
}
