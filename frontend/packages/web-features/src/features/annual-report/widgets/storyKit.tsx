import { createContext, useContext, useMemo, type CSSProperties, type ReactNode } from 'react'
import { motion } from 'framer-motion'

import type { YearTheme } from '../data/yearTheme'

/**
 * 年度回顧(限動式)的視覺基礎:每一章的配色 + 共用狀態。
 * 跟 App `lib/pages/report/annual_story_kit.dart` 用同一組設計色。
 *
 * **刻意不走主題色 / 深淺色 token**:這是全螢幕、由設計決定配色的沉浸式畫面
 * (像 Spotify Wrapped),每一章換一組深色漸層製造節奏——背景永遠深色、字永遠
 * 白色,淺色 / 深色模式都不會出現對比問題。顏色集中在這個檔案,頁面與海報共用。
 *
 * 每一章的重點色透過 CSS 變數 `--story-accent` 往下傳,頁面用
 * `text-[color:var(--story-accent)]` / `ACCENT` 取用,不用各自寫死。
 */
export type StoryPalette = { from: string; to: string; accent: string }

export const PALETTES = {
  cover: { from: '#1E1B4B', to: '#5B21B6', accent: '#FCD34D' },
  records: { from: '#042F2E', to: '#0F766E', accent: '#5EEAD4' },
  firstRecord: { from: '#431407', to: '#C2410C', accent: '#FDBA74' },
  money: { from: '#0B1E3F', to: '#1D4ED8', accent: '#93C5FD' },
  categories: { from: '#4C0519', to: '#BE123C', accent: '#FDA4AF' },
  months: { from: '#2E1065', to: '#7C3AED', accent: '#C4B5FD' },
  habits: { from: '#451A03', to: '#B45309', accent: '#FCD34D' },
  daily: { from: '#082F49', to: '#0369A1', accent: '#7DD3FC' },
  funFacts: { from: '#3B0764', to: '#A21CAF', accent: '#F0ABFC' },
  moments: { from: '#111827', to: '#3730A3', accent: '#A5B4FC' },
  yoy: { from: '#134E4A', to: '#0E7490', accent: '#67E8F9' },
  tags: { from: '#1A2E05', to: '#4D7C0F', accent: '#D9F99D' },
  stockOverview: { from: '#022C22', to: '#047857', accent: '#6EE7B7' },
  stockHighlights: { from: '#052E16', to: '#166534', accent: '#BEF264' },
  achievements: { from: '#3F2305', to: '#92400E', accent: '#FDE68A' },
  finale: { from: '#0F172A', to: '#1E293B', accent: '#FCD34D' },
} satisfies Record<string, StoryPalette>

export type PaletteKey = keyof typeof PALETTES

/** 頁面裡用重點色的 class / style。 */
export const ACCENT = 'text-[color:var(--story-accent)]'
export const ACCENT_VAR = 'var(--story-accent)'

// ---------------------------------------------------------------------------
// 色相旋轉(年度主題)
// ---------------------------------------------------------------------------

function hexToHsl(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  const r = ((n >> 16) & 255) / 255
  const g = ((n >> 8) & 255) / 255
  const b = (n & 255) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h: number
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  return [h * 60, s, l]
}

function hslToHex(h: number, s: number, l: number): string {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  const to = (v: number) =>
    Math.round((v + m) * 255)
      .toString(16)
      .padStart(2, '0')
  return `#${to(r)}${to(g)}${to(b)}`.toUpperCase()
}

export function rotateHue(hex: string, degrees: number): string {
  if (degrees % 360 === 0) return hex
  const [h, s, l] = hexToHsl(hex)
  return hslToHex((((h + degrees) % 360) + 360) % 360, s, l)
}

/** 整組色相旋轉 `degrees`(年度主題用,見 `YearTheme.hueShift`)。 */
export function shiftPalette(p: StoryPalette, degrees: number): StoryPalette {
  if (degrees % 360 === 0) return p
  return { from: rotateHue(p.from, degrees), to: rotateHue(p.to, degrees), accent: rotateHue(p.accent, degrees) }
}

export function themedPalette(key: PaletteKey, theme: YearTheme | null | undefined): StoryPalette {
  return shiftPalette(PALETTES[key], theme?.hueShift ?? 0)
}

// ---------------------------------------------------------------------------
// 共用狀態:年度主題 + 猜謎答案(翻回去看不會再問一次)
// ---------------------------------------------------------------------------

export type StoryState = {
  theme: YearTheme
  /** 猜謎答案:題目 id → 選了第幾個選項 */
  quizAnswers: Record<string, number>
  answerQuiz: (id: string, index: number) => void
}

const StoryContext = createContext<StoryState | null>(null)

export function StoryProvider({ value, children }: { value: StoryState; children: ReactNode }) {
  return <StoryContext.Provider value={value}>{children}</StoryContext.Provider>
}

export function useStory(): StoryState | null {
  return useContext(StoryContext)
}

// ---------------------------------------------------------------------------
// 背景:漸層 + 兩顆柔和的光暈
// ---------------------------------------------------------------------------

export function StoryBackground({ palette }: { palette: StoryPalette }) {
  return (
    <div
      className="pointer-events-none absolute inset-0 overflow-hidden"
      style={{ background: `linear-gradient(135deg, ${palette.from} 0%, ${palette.to} 100%)` }}
    >
      <div
        className="absolute -right-40 -top-32 h-[70vmin] w-[70vmin] rounded-full blur-3xl"
        style={{ background: `radial-gradient(circle, ${palette.accent}55, transparent 70%)` }}
      />
      <div
        className="absolute -bottom-40 -left-40 h-[60vmin] w-[60vmin] rounded-full blur-3xl"
        style={{ background: `radial-gradient(circle, ${palette.to}cc, transparent 70%)` }}
      />
    </div>
  )
}

/** 套在一章外層:提供 `--story-accent`。 */
export function paletteVars(p: StoryPalette): CSSProperties {
  return { ['--story-accent' as string]: p.accent }
}

// ---------------------------------------------------------------------------
// 彩帶(稀有稱號揭曉用)。種子固定 = 年份,同一年每次都一樣;減少動畫時不畫。
// ---------------------------------------------------------------------------

export function StoryConfetti({ seed, colors }: { seed: number; colors: string[] }) {
  const pieces = useMemo(() => {
    let s = seed >>> 0
    const rand = () => {
      s = (s * 1664525 + 1013904223) >>> 0
      return s / 4294967296
    }
    return Array.from({ length: 36 }, (_, i) => ({
      left: rand() * 100,
      delay: rand() * 0.6,
      duration: 2.2 + rand() * 1.6,
      rotate: (rand() - 0.5) * 720,
      drift: (rand() - 0.5) * 120,
      size: 6 + rand() * 6,
      color: colors[i % colors.length],
      round: rand() > 0.6,
    }))
  }, [seed, colors])

  if (typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    return null
  }
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {pieces.map((p, i) => (
        <motion.div
          key={i}
          className="absolute top-0"
          style={{
            left: `${p.left}%`,
            width: p.size,
            height: p.round ? p.size : p.size * 0.45,
            background: p.color,
            borderRadius: p.round ? '50%' : 2,
          }}
          initial={{ y: '-5vh', x: 0, rotate: 0, opacity: 1 }}
          animate={{ y: '105vh', x: p.drift, rotate: p.rotate, opacity: [1, 1, 0] }}
          transition={{ duration: p.duration, delay: 0.3 + p.delay, ease: 'easeIn' }}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 先猜再揭曉
// ---------------------------------------------------------------------------

export type QuizOption = { label: string; emoji?: string }

/**
 * 題目卡:選完才翻答案。答案記在 StoryProvider(翻回這一章不會再問);
 * 點題目卡以外的地方照樣換章(等於略過)。
 */
export function StoryQuiz({
  kicker,
  question,
  hint,
  options,
  onAnswer,
}: {
  kicker: string
  question: string
  hint: string
  options: QuizOption[]
  onAnswer: (index: number) => void
}) {
  return (
    <div className="mx-auto flex w-full max-w-xl flex-col items-center text-center">
      <div className={`mb-3 text-xs font-bold uppercase tracking-[0.3em] ${ACCENT}`}>{kicker}</div>
      <motion.h2
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="mb-10 font-serif text-3xl font-bold leading-snug text-white sm:text-4xl"
      >
        {question}
      </motion.h2>
      <div className="flex w-full flex-col gap-3">
        {options.map((o, i) => (
          <motion.button
            key={o.label}
            type="button"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.25 + i * 0.1 }}
            onClick={() => onAnswer(i)}
            className="flex items-center gap-3 rounded-2xl border border-white/20 bg-white/10 px-5 py-4 text-left text-lg font-semibold text-white backdrop-blur-sm transition hover:scale-[1.02] hover:border-white/50 hover:bg-white/20"
          >
            {o.emoji ? <span className="text-2xl">{o.emoji}</span> : null}
            <span>{o.label}</span>
          </motion.button>
        ))}
      </div>
      <div className="mt-8 text-xs text-white/50">{hint}</div>
    </div>
  )
}

/** 答案頁頂端的猜對 / 猜錯徽章。 */
export function QuizVerdict({ right, text }: { right: boolean; text: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ type: 'spring', stiffness: 260, damping: 16 }}
      className={`mb-6 inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-sm font-bold ${
        right ? 'bg-emerald-400/20 text-emerald-200' : 'bg-rose-400/20 text-rose-200'
      }`}
    >
      <span>{right ? '🎉' : '🙈'}</span>
      {text}
    </motion.div>
  )
}

// ---------------------------------------------------------------------------
// 深色背景上可讀的收支色(比一般頁面亮一階),跟隨「外觀設定 → 收支顏色」:
// AppShell 把 `income_is_red` 寫到 `<html data-income-color="red|green">`。
// ---------------------------------------------------------------------------

export const STORY_RED = '#FF8A80'
export const STORY_GREEN = '#6EE7A0'

export function incomeTone() {
  const incomeIsRed =
    typeof document === 'undefined' || document.documentElement.dataset.incomeColor !== 'green'
  return {
    income: incomeIsRed ? STORY_RED : STORY_GREEN,
    expense: incomeIsRed ? STORY_GREEN : STORY_RED,
  }
}
