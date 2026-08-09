/**
 * SwipeSmart2 信用卡刷卡建議整合(Phase 14,docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md)。
 *
 * 後端對應 `src/routers/swipesmart.py`(Personal API Key CRUD + 卡片目錄)與
 * `src/routers/read/ledgers.py::get_card_recommendation`(推薦查詢)。
 *
 * **關鍵**:Personal API Key 明文只在使用者貼上那次的 POST 回應裡以遮罩形式
 * 確認已存(連建立當下都不回顯明文),GET 永遠只回遮罩,不像 PAT 那樣有
 * "只顯示一次的完整明文"這件事——這把 Key 是使用者從別處複製貼上的,不是
 * 這裡生成的。
 */
import { authedDelete, authedGet, authedPost } from './http'

export interface SwipeSmartKeyStatus {
  has_key: boolean
  masked: string | null
  /** 只有貼上/更換 Key 那次的回應有意義:本次順帶自動比對配對成功的信用卡
   *  帳戶數(名稱相近/相同自動對應,不用每個使用者都手動一一勾選)。 */
  auto_mapped: number
}

export interface SwipeSmartCard {
  card_id: string
  bank_name: string
  card_name: string
}

export interface SwipeSmartCardRecommendation {
  card_id: string
  bank_name: string
  card_name: string
  rule_name: string | null
  estimated_reward: number
  effective_rate: number
  note: string | null
  alert_messages: string[]
  /** 有值 = 使用者已把這張 SwipeSmart 卡對照到自己的 BeeCount 信用卡帳戶,
   *  前端渲染成可點擊 badge;null = 純文字說明,不可點擊。 */
  account_id: string | null
  account_name: string | null
}

export async function fetchSwipeSmartKeyStatus(token: string): Promise<SwipeSmartKeyStatus> {
  return authedGet<SwipeSmartKeyStatus>('/profile/swipesmart', token)
}

export async function saveSwipeSmartKey(
  token: string,
  apiKey: string
): Promise<SwipeSmartKeyStatus> {
  return authedPost<SwipeSmartKeyStatus>('/profile/swipesmart', token, { api_key: apiKey })
}

export async function deleteSwipeSmartKey(token: string): Promise<void> {
  await authedDelete<void>('/profile/swipesmart', token)
}

export async function fetchSwipeSmartCards(token: string): Promise<SwipeSmartCard[]> {
  return authedGet<SwipeSmartCard[]>('/profile/swipesmart/cards', token)
}

export async function fetchCardRecommendation(
  token: string,
  ledgerId: string,
  options: { amount: number; merchant: string }
): Promise<SwipeSmartCardRecommendation[]> {
  const query = new URLSearchParams()
  query.set('amount', String(options.amount))
  query.set('merchant', options.merchant)
  return authedGet<SwipeSmartCardRecommendation[]>(
    `/read/ledgers/${encodeURIComponent(ledgerId)}/card-recommendation?${query.toString()}`,
    token
  )
}
