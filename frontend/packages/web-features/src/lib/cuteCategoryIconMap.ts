/**
 * 「可愛類別圖示」手繪 SVG 素材對照表。
 *
 * 素材直接從 mobile 專案 `assets/icons/categories_cute/*.svg` 複製過來(289
 * 個檔案,含 `_fallback.svg`),key 就是 `category.icon` 存的原始字串(跟
 * mobile `CategoryService.getCategoryIcon` switch 的 case 名一致 —— 注意這
 * 跟 `categoryIconMap.ts::FLUTTER_RENAMES` 的「渲染後」Material 圖示名是兩回
 * 事,cute 素材不套用那份重命名表,直接用 stored 值當檔名查)。
 *
 * 每個 SVG 都用 `stroke="currentColor"`/`fill="currentColor"` 畫線稿,搭配 1-2
 * 個寫死 hex 的裝飾色塊,`viewBox="0 0 48 48"`。用 raw 字串 inline 渲染(而不是
 * `<img>`)才能讓 `currentColor` 跟著呼叫端的文字顏色換色。
 *
 * 找不到對應 key(自訂分類、或素材尚未覆蓋的生僻字串)一律退回 `_fallback`,
 * 跟 mobile `CuteCategoryIcon.maybeBuild` 現在的行為一致 —— 開啟可愛圖示後
 * 不會再看到 Material 圖示。
 */

const rawModules = import.meta.glob('../assets/categories_cute/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const CUTE_ICON_SVGS: Record<string, string> = {}
for (const [path, content] of Object.entries(rawModules)) {
  const key = path.split('/').pop()?.replace(/\.svg$/, '')
  if (key) CUTE_ICON_SVGS[key] = content
}

const FALLBACK_SVG = CUTE_ICON_SVGS['_fallback'] ?? ''

/** 解析 `category.icon` 存值 → 手繪 SVG 原始碼(找不到就回退 `_fallback`)。 */
export function resolveCuteIconSvg(icon: string | null | undefined): string {
  const key = (icon || '').trim()
  return CUTE_ICON_SVGS[key] ?? FALLBACK_SVG
}
