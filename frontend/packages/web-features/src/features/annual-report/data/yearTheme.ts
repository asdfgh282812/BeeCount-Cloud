/**
 * 年度回顧的「年度主題」(純資料,不碰 UI):依生肖每年換一套,跟 App
 * `lib/services/report/annual_year_theme.dart` 同一套規則。
 *
 * 同一年永遠是同一套,換一年就換一套,12 年一輪。視覺上做兩件事:
 * - 封面放生肖圖騰 + 主題名(「馬年 · 奔騰」);
 * - 整份報告(頁面 + 分享海報)的章節配色依 `hueShift` 旋轉色相。
 *   2026 馬年 = 0°(設計稿原始配色),其他年份每差一年轉 30°。
 *
 * 生肖以西元年近似,不處理農曆新年前的 1 月——年度報告以整年為單位。
 */
export const ZODIACS = [
  'rat',
  'ox',
  'tiger',
  'rabbit',
  'dragon',
  'snake',
  'horse',
  'goat',
  'monkey',
  'rooster',
  'dog',
  'pig',
] as const

export type Zodiac = (typeof ZODIACS)[number]

const EMOJI: Record<Zodiac, string> = {
  rat: '🐭',
  ox: '🐮',
  tiger: '🐯',
  rabbit: '🐰',
  dragon: '🐲',
  snake: '🐍',
  horse: '🐴',
  goat: '🐑',
  monkey: '🐵',
  rooster: '🐔',
  dog: '🐶',
  pig: '🐷',
}

export type YearTheme = {
  year: number
  zodiac: Zodiac
  emoji: string
  /** 章節配色要轉幾度色相(0 ≤ x < 360) */
  hueShift: number
}

const mod = (n: number, m: number) => ((n % m) + m) % m

/** 2020 是鼠年。 */
export function yearTheme(year: number): YearTheme {
  const idx = mod(year - 2020, 12)
  const zodiac = ZODIACS[idx]
  return {
    year,
    zodiac,
    emoji: EMOJI[zodiac],
    hueShift: mod((idx - ZODIACS.indexOf('horse')) * 30, 360),
  }
}
