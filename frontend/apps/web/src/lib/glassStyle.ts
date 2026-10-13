import type { ProfileAppearance } from '@beecount/api-client'

export type GlassStyle = NonNullable<ProfileAppearance['glass_style']>

export function resolveGlassStyle(value: unknown): GlassStyle {
  return value === 'balanced' || value === 'tinted' ? value : 'clear'
}

/** 只更新材質，不更動主色或其他外觀設定；缺鍵預設清透 A。 */
export function applyGlassStyle(value: unknown) {
  document.documentElement.dataset.glassStyle = resolveGlassStyle(value)
}
