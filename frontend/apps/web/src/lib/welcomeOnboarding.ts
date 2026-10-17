import type { ProfileAppearance } from '@beecount/api-client'
import type { Locale } from '@beecount/ui'

/** 歡迎引導的步數。順序:外觀 → 質感與造型 → 顯示偏好 → AI → 帳本 → 帳戶。 */
export const WELCOME_STEP_COUNT = 6

/** 歡迎引導只提供這兩種語言(對齊 App 的歡迎頁);其他語系(簡中)仍可從頁首語言切換選。 */
export const WELCOME_LOCALES: Locale[] = ['zh-TW', 'en']

/**
 * 要不要顯示首次登入歡迎引導。
 *
 * 條件是「全新帳號」:profile 與帳本清單都載入完成、雲端一本帳本也沒有,
 * 已完成偏好卻沒有帳本的帳號仍需建立帳本，直接從帳本步驟開始。
 * 已有帳本的舊帳號不會再被問一次；
 * 載入未完成時一律不顯示,避免舊帳號在資料回來前閃一下歡迎畫面。
 *
 * 跟 App 的判斷一致(App 端見 lib/pages/auth/welcome_page.dart `_needsPersonalize`),
 * 設計見 App repo docs/changes/2026-10-17-onboarding-personalize.md。
 */
export function shouldShowWelcome(input: {
  profileLoaded: boolean
  ledgersLoaded: boolean
  ledgerCount: number
  appearance: ProfileAppearance | null | undefined
}): boolean {
  if (!input.profileLoaded || !input.ledgersLoaded) return false
  if (input.ledgerCount > 0) return false
  return true
}

/** 每週起始日:appearance 有設定就照它,沒設定時沿用原本的語系推斷(中文週一、其餘週日)。 */
export function resolveWeekStartsMonday(
  appearance: ProfileAppearance | null | undefined,
  browserLanguage: string,
): boolean {
  const saved = appearance?.week_starts_monday
  if (typeof saved === 'boolean') return saved
  return browserLanguage.toLowerCase().startsWith('zh')
}
