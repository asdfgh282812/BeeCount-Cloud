import { createContext, useContext, type ReactNode } from 'react'

/** 跟 mobile `CategoryIconStyle` enum 對齊(`theme_providers.dart`)。 */
export type CategoryIconStyle = 'material' | 'cute'

const CategoryIconStyleContext = createContext<CategoryIconStyle>('material')

/**
 * 由 `apps/web` 在拿到 `profileMe.appearance.category_icon_style` 後往下餵值
 * (見 `AppShell.tsx`)——`web-features` 本身不管 auth/profile 資料怎麼來,只
 * 負責照這個 context 的值決定 `CategoryIcon` 要不要走可愛畫風。沒有 Provider
 * 包裹時(例如元件測試)預設 `'material'`,行為等同這次改動前。
 */
export function CategoryIconStyleProvider({
  value,
  children,
}: {
  value: CategoryIconStyle
  children: ReactNode
}) {
  return (
    <CategoryIconStyleContext.Provider value={value}>{children}</CategoryIconStyleContext.Provider>
  )
}

export function useCategoryIconStyle(): CategoryIconStyle {
  return useContext(CategoryIconStyleContext)
}
