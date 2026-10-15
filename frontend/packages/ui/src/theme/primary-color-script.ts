/** Local storage key for user's custom primary color (hex).
 *  ⚠️ 4.0 改名帳好嗎／TallyCheck 後**刻意保留** `beecount.` 前綴:改了所有人的
 *  主題色都會掉回預設(見 App repo docs/roadmap/4.0.0/01-rebrand.md §3)。 */
export const PRIMARY_COLOR_STORAGE_KEY = 'beecount.primary-color'

/** 預設主色:新芽綠,對齊 App `BeeTheme.sproutGreen` / `BeePalette.sprout`。 */
export const DEFAULT_PRIMARY_COLOR = '#2E9E66'

/** 暗色模式下的預設主色(App `BeePalette.sproutDark`)。只有「預設新芽綠」在暗色
 *  換成這個較亮的綠(深底上 #2E9E66 太悶);使用者自選的顏色原樣使用,對齊 App
 *  `BeeTheme.primaryFor`。 */
export const DEFAULT_PRIMARY_COLOR_DARK = '#4CC788'

/** 農場夜色與 App BeePalette / styles.css --farm-* 對齊。
 * 日夜由農場天氣決定，不跟隨介面 .dark 或使用者主色；Web 農場頁屬 M4。 */
export const FARM_NIGHT_PALETTE = {
  nightBlue: '#101C64',
  nightViolet: '#38256E',
  moonlight: '#829EEB',
  lampGlow: '#FFAD57',
  lampCore: '#FFF2C4',
  lampWood: '#493454',
} as const

/**
 * 預設色板,順序與色碼對齊 App `personalize_page.dart` 的主題色清單(新芽綠第一、
 * 蜂蜜黃 #F8C91C 保留讓 3.x 老使用者選得回來),再補上 Web 3.x 的預設「蜂蜜金」
 * #F59E0B —— 舊 Web 使用者看慣的那個金色也能選回來。
 * color picker 里先摆这些，用户也可以用 `<input type="color">` 自定义。
 */
export const PRIMARY_COLOR_PRESETS: string[] = [
  '#2E9E66', // 新芽綠(預設)
  '#F8C91C', // 蜂蜜黃(App 3.x 預設)
  '#F59E0B', // 蜂蜜金(Web 3.x 預設)
  '#FF7043', // 橙
  '#26A69A', // 青綠
  '#7E57C2', // 紫
  '#E91E63', // 粉
  '#2196F3', // 藍
  '#80CBC4', // 薄荷
  '#FFCC80', // 沙
  '#B39DDB', // 薰衣草
  '#90CAF9', // 天空
  '#FF8A65', // 暖橙
  '#4DB6AC', // 薄荷綠
  '#AD7A99', // 玫瑰金
  '#1565C0', // 深藍
  '#D32F2F', // 楓紅
  '#388E3C', // 翡翠
  '#9575CD', // 薰衣紫
  '#FFA726', // 琥珀
  '#C2185B', // 胭脂
  '#3F51B5', // 靛藍
  '#689F38', // 橄欖
  '#FF8A80', // 珊瑚
  '#2E7D32', // 墨綠
  '#673AB7', // 紫羅蘭
  '#FF5722', // 夕陽
  '#00ACC1', // 孔雀藍
  '#CDDC39' // 萊姆(Flutter Colors.lime)
]

/** App `BeePalette` 裡 primary 衍生色會用到的幾個原值。 */
const INK = '#1F2A24' // 墨綠黑:淺色主色上的前景色
const PAPER = '#FFFFFF' // 亮色卡片底
const NIGHT1 = '#1B211E' // 暗色卡片底
const SPROUT_SOFT = '#E3F4EA' // 嫩芽綠(預設主色的亮色 soft 底)
const SPROUT_STRONG = '#23895A' // 深芽綠(預設主色的亮色 strong)

function sameHex(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase()
}

/** 某個主色在指定亮暗下實際顯示的顏色(App `BeeTheme.primaryFor`)。 */
export function primaryForMode(hex: string, mode: 'light' | 'dark'): string {
  return mode === 'dark' && sameHex(hex, DEFAULT_PRIMARY_COLOR) ? DEFAULT_PRIMARY_COLOR_DARK : hex
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.trim().replace(/^#/, '')
  const full =
    normalized.length === 3
      ? normalized
          .split('')
          .map((c) => c + c)
          .join('')
      : normalized
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16)
  ]
}

function rgbToHex([r, g, b]: [number, number, number]): string {
  return (
    '#' +
    [r, g, b]
      .map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0'))
      .join('')
      .toUpperCase()
  )
}

/** `fg` 以 alpha 疊在不透明的 `bg` 上(App `Color.alphaBlend`)。 */
function alphaBlend(fg: string, alpha: number, bg: string): string {
  const f = hexToRgb(fg)
  const b = hexToRgb(bg)
  return rgbToHex([0, 1, 2].map((i) => f[i] * alpha + b[i] * (1 - alpha)) as [number, number, number])
}

/**
 * 給任意底色挑前景色(白 / 墨綠黑),同 App `BeeTokens.onColor`
 * (Flutter `ThemeData.estimateBrightnessForColor`:(L + 0.05)² > 0.15 視為亮底)。
 * 使用者選了淺色主題(沙色、薄荷、蜂蜜黃)時按鈕字才不會糊掉。
 */
export function onColorHex(hex: string): string {
  const lin = hexToRgb(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  })
  const luminance = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
  return (luminance + 0.05) * (luminance + 0.05) > 0.15 ? INK : '#FFFFFF'
}

/** 主色按下態 / 漸層深端(App `BeeTokens.primaryStrong`):預設新芽綠就是深芽綠,
 *  其他顏色亮色往下壓 6%、暗色往上提 6% 明度。 */
function primaryStrongTriplet(shown: string, mode: 'light' | 'dark'): string {
  if (sameHex(shown, DEFAULT_PRIMARY_COLOR)) return hslTriplet(SPROUT_STRONG)
  const { h, s, l } = hexToHslPrecise(shown)
  const next = Math.round(Math.min(100, Math.max(0, l + (mode === 'dark' ? 6 : -6))) * 10) / 10
  return `${h} ${s}% ${next}%`
}

/** 主色的柔和底(App `BeeTokens.primarySoft`):亮色 14% 疊白卡(預設 = 嫩芽綠),
 *  暗色 20% 疊暗卡。回傳不透明色,疊在任何底上顏色都一致。 */
function primarySoftHex(shown: string, mode: 'light' | 'dark'): string {
  if (mode === 'light' && sameHex(shown, DEFAULT_PRIMARY_COLOR)) return SPROUT_SOFT
  return mode === 'dark' ? alphaBlend(shown, 0.2, NIGHT1) : alphaBlend(shown, 0.14, PAPER)
}

function hexToHslPrecise(hex: string): { h: number; s: number; l: number } {
  const [r, g, b] = hexToRgb(hex).map((v) => v / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  let h = 0
  let s = 0
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0)
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h /= 6
  }
  const round1 = (v: number) => Math.round(v * 10) / 10
  return { h: round1(h * 360), s: round1(s * 100), l: round1(l * 100) }
}

/** #RRGGBB → tailwind CSS 變數用的 `H S% L%`(不帶 hsl() 包裝)。 */
function hslTriplet(hex: string): string {
  const { h, s, l } = hexToHslPrecise(hex)
  return `${h} ${s}% ${l}%`
}

/** 把 #RRGGBB 转 HSL（tailwind CSS variable 需要 `H S% L%` 格式，不带 hsl() 包装）。 */
export function hexToHsl(hex: string): { h: number; s: number; l: number } {
  const normalized = hex.trim().replace(/^#/, '')
  const full =
    normalized.length === 3
      ? normalized
          .split('')
          .map((c) => c + c)
          .join('')
      : normalized
  const r = parseInt(full.slice(0, 2), 16) / 255
  const g = parseInt(full.slice(2, 4), 16) / 255
  const b = parseInt(full.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  let h = 0
  let s = 0
  if (max !== min) {
    const d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    switch (max) {
      case r:
        h = (g - b) / d + (g < b ? 6 : 0)
        break
      case g:
        h = (b - r) / d + 2
        break
      case b:
        h = (r - g) / d + 4
        break
    }
    h /= 6
  }
  return {
    h: Math.round(h * 360),
    s: Math.round(s * 100),
    l: Math.round(l * 100)
  }
}

/**
 * 把主色套到 CSS 變數。亮、暗兩組各算一份(`--primary-light` / `--primary-dark`
 * …),styles.css 的 `:root` / `.dark` 各自挑對應那組 —— 所以切換亮暗模式不用
 * 重跑這支,而且「預設新芽綠在暗色換成 #4CC788」(App `BeeTheme.primaryFor`)
 * 純靠 CSS 生效,使用者自選色兩組相同。
 *
 * 一併算出的衍生色(對齊 App `BeeTokens`):
 * - `--primary-foreground-*`:主色上的字色,依亮度挑白或墨綠黑(`onPrimary`)
 * - `--primary-soft-*` / `--primary-strong-*`:選中底 / 按下態
 * - `--accent-light` / `--accent-dark`:shadcn `accent`(全站 `hover:bg-accent/40`
 *   的 hover 底)。沿用 3.x 調好的「同色相 + 固定 80% 90%」淺色方案 —— 實測在
 *   深底上也好看、跨所有色相穩定,這裡不跟 App 的 primarySoft 綁在一起。
 */
export function applyPrimaryColor(hex: string): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  for (const mode of ['light', 'dark'] as const) {
    const shown = primaryForMode(hex, mode)
    root.style.setProperty(`--primary-${mode}`, hslTriplet(shown))
    root.style.setProperty(`--primary-foreground-${mode}`, hslTriplet(onColorHex(shown)))
    root.style.setProperty(`--primary-soft-${mode}`, hslTriplet(primarySoftHex(shown, mode)))
    root.style.setProperty(`--primary-strong-${mode}`, primaryStrongTriplet(shown, mode))
  }
  const { h } = hexToHsl(hex)
  const accentValue = `${h} 80% 90%`
  root.style.setProperty('--accent-light', accentValue)
  root.style.setProperty('--accent-dark', accentValue)
}

/**
 * 節日主題(節日 P3):有節日色就套節日色,沒有(null)就換回使用者的顏色。
 * 只設 CSS 變數,**不寫 localStorage** —— 節日色是當天暫時的,刷新頁面、
 * 隔天打開都要回到使用者自己的顏色。呼叫端是 PrimaryColorProvider。
 */
export function applyFestivalOverride(festivalHex: string | null, userHex: string): void {
  applyPrimaryColor(festivalHex ?? userHex)
}

/** 初次加载时从 localStorage 读；没有则用默认色。不写 style.setProperty
 *  —— 调用方负责触发 applyPrimaryColor（通常放 provider 里）。 */
export function initialPrimaryColor(): string {
  if (typeof window === 'undefined') return DEFAULT_PRIMARY_COLOR
  const raw = window.localStorage.getItem(PRIMARY_COLOR_STORAGE_KEY)
  if (typeof raw === 'string' && /^#[0-9a-fA-F]{6}$/.test(raw)) return raw
  return DEFAULT_PRIMARY_COLOR
}

/** 用户是否在 web 本地显式改过主题色。PrimaryColorProvider 用来决定 server
 *  下发的偏好该不该覆盖当前色：有 override 就尊重本地；没有就跟 server 走。 */
export function hasLocalPrimaryColorOverride(): boolean {
  if (typeof window === 'undefined') return false
  const raw = window.localStorage.getItem(PRIMARY_COLOR_STORAGE_KEY)
  return typeof raw === 'string' && /^#[0-9a-fA-F]{6}$/.test(raw)
}

export function persistPrimaryColor(hex: string): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(PRIMARY_COLOR_STORAGE_KEY, hex)
}
