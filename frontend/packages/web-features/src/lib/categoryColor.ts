import { tagTextColorOn } from './tagColorPalette'

type ColorableCategory = {
  color?: string | null
  parent_name?: string | null
  kind?: string | null
}

/**
 * 分类圆形图标的有效底色。
 *
 * 只有一级分类能自己设色,二级分类颜色继承自父分类(跟 app 端
 * `category_selector.dart::_CategoryItem.build` 的 `resolvedColor` 逻辑对齐,
 * 见 CategoriesPanel.tsx 里颜色 picker 旁的说明)。这里的 `rows` 需要传同一份
 * 完整列表(不能只传当前 kind 过滤后的),否则父级查找会漏。
 */
export function resolveCategoryColor(
  category: ColorableCategory,
  rows: ReadonlyArray<ColorableCategory & { name?: string | null; level?: number | null }>
): string | null {
  const own = category.color?.trim()
  if (own) return own
  const parentName = category.parent_name?.trim().toLowerCase()
  if (!parentName) return null
  const parent = rows.find(
    (row) =>
      row.kind === category.kind &&
      Number(row.level) === 1 &&
      (row.name || '').trim().toLowerCase() === parentName
  )
  return parent?.color?.trim() || null
}

/** 有色底色 → 白字/白图标;没色 → undefined,呼叫方用预设灰底文字色。 */
export function categoryIconStyle(color: string | null): { background: string; color: string } | undefined {
  if (!color) return undefined
  return { background: color, color: tagTextColorOn(color) }
}
