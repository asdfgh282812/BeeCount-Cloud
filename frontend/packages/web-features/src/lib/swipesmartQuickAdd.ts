/**
 * SwipeSmart 一鍵記帳 deep link(Phase 15,docs/PH15_SWIPESMART_QUICKADD_SD.md
 * §3.4/§3.5):純前端、best-effort 的兩個反查函式,比對不到就回 null,由呼叫方
 * 決定降級文案,不擋記帳流程。
 */

/** 信用卡帳戶反查(§3.4):用 SwipeSmart CardId 找對應的 BeeCount 信用卡帳戶
 * ——只認未隱藏的 credit_card 帳戶,`hidden` 命中視同沒對照到。 */
export function findAccountBySwipesmartCardId<
  T extends { account_type?: string | null; hidden?: boolean; swipesmart_card_id?: string | null }
>(accounts: T[], cardId: string): T | null {
  if (!cardId) return null
  const hit = accounts.find(
    (a) => a.account_type === 'credit_card' && !a.hidden && (a.swipesmart_card_id || '') === cardId
  )
  return hit ?? null
}

function normalizeCategoryName(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, '')
}

/** 分類名稱比對(§3.5):正規化 + 包含式模糊比對,命中剛好一筆才採用,0 筆或
 * 多筆都回 null(無法判斷,不強行猜測)。 */
export function matchCategoryByName<T extends { name: string }>(
  categories: T[],
  swipesmartCategoryName: string | null
): T | null {
  if (!swipesmartCategoryName) return null
  const target = normalizeCategoryName(swipesmartCategoryName)
  if (!target) return null
  const hits = categories.filter((c) => {
    const name = normalizeCategoryName(c.name || '')
    if (!name) return false
    return name.includes(target) || target.includes(name)
  })
  return hits.length === 1 ? hits[0] : null
}
