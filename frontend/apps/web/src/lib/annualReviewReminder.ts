/**
 * 首頁「年度回顧出爐了」提醒卡的狀態,跟 App 的
 * `lib/providers/annual_review_reminder_providers.dart` 同一套規則:
 *
 * - 新的一年開始(1/1 起)就提醒「去年」的年度回顧,**沒有期限**;
 * - 使用者打開那一年的年度回顧(從提醒卡、或從頭像選單 / 命令面板選到那一年)
 *   或按 ✕ 之前會一直留在首頁;
 * - 年中先看今年的不算(還沒過完,隔年 1 月照樣提醒完整版)。
 *
 * 看過的年份只存這個瀏覽器的 localStorage,不跟帳號同步(跟 App 一樣只存本機):
 * 在 App 看過、Web 還是會提醒一次。
 */
const STORAGE_KEY = 'beecount.annualReview.seenYears'

/** 「看過的年份變了」(同一個分頁內通知首頁卡片收起來)。 */
export const ANNUAL_REVIEW_SEEN_EVENT = 'beecount:annual-review-seen'

/** 「打開某一年的年度回顧」(首頁卡片 → AppHeader 的 launcher)。detail = { year } */
export const OPEN_ANNUAL_REPORT_EVENT = 'beecount:open-annual-report'

export function readSeenYears(): Set<number> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    const arr = raw ? (JSON.parse(raw) as unknown) : []
    return new Set(Array.isArray(arr) ? arr.filter((y): y is number => Number.isInteger(y)) : [])
  } catch {
    return new Set()
  }
}

/** 提醒哪一年;已經看過就不提醒(null)。純函式,方便測試。 */
export function annualReviewReminderYear(now: Date, seen: ReadonlySet<number>): number | null {
  const year = now.getFullYear() - 1
  return seen.has(year) ? null : year
}

/** 記下「這一年的回顧看過了」。還沒過完的年份(今年 / 未來)不記。 */
export function markAnnualReviewSeen(year: number, now: Date = new Date()): void {
  if (year >= now.getFullYear()) return
  const seen = readSeenYears()
  if (seen.has(year)) return
  seen.add(year)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...seen].sort()))
  } catch {
    // 無痕模式 / 儲存空間滿:這次 session 內照樣收起來,下次重整會再出現
  }
  window.dispatchEvent(new CustomEvent(ANNUAL_REVIEW_SEEN_EVENT, { detail: { year } }))
}

export function dispatchOpenAnnualReport(year: number): void {
  window.dispatchEvent(new CustomEvent(OPEN_ANNUAL_REPORT_EVENT, { detail: { year } }))
}
