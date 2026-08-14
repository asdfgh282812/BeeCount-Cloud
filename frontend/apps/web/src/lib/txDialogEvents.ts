import type {
  WorkspaceAccount,
  WorkspaceCategory,
  WorkspaceTag,
  WorkspaceTransaction,
} from '@beecount/api-client'

/**
 * 跨组件触发实体详情/编辑弹窗的轻量事件总线 — 让 CommandPalette 等不在
 * 各 *Page 内的组件也能命令性地打开弹窗。
 *
 * 事件分两类:
 *   1) detail:在对应 Page 上展示「详情弹窗」(只读 + 编辑/删除入口)。
 *   2) edit:跳过详情直接进编辑(交易行的「编辑」按钮、详情里点编辑)。
 *
 * 用 window CustomEvent 而不是 React Context,因为:
 *  1) 触发方(CommandPalette / List 行)与接收方(各 *Page)不在同一棵子树;
 *  2) 事件是命令式("现在打开"),用 Context 反而要造 trigger counter 绕一道。
 *
 * 接收方在 *Page 用 useEffect + addEventListener 监听。
 * 如果用户当前不在对应页面,先 navigate 再 dispatch(调用方处理 50ms 延迟)。
 */

// ========== Transaction ==========
const NEW_TX_EVENT = 'bee:open-new-tx'
const EDIT_TX_EVENT = 'bee:open-edit-tx'
const DETAIL_TX_EVENT = 'bee:open-detail-tx'

// 监听器计数 — 让派发方判断"目标页是否挂载",决定是否展示「请到对应页编辑」
// 提示。比 dispatchEvent 自己的回执机制简单。
let editTxHandlerCount = 0
let editCategoryHandlerCount = 0

/**
 * 打开"新建交易" dialog 的可选预填项。
 * - happenedAt:ISO 8601 字符串,日历视图选中某一天后调用,prefill 到 happened_at 字段
 * - ledgerId:目标账本(留空 = 用 active ledger)
 * - refundOf:§2.12.3,从原交易明细点「退款」发起时带上,预填金额/备注/
 *   分类/账户 + 把 refund_of_id 指向原交易。§2.6:income 也能被退款后,
 *   退款交易的 tx_type 不再固定 income,而是原交易 origTxType 的反向类型
 *   (expense 被退→income 退款交易;income 被退→expense 退款交易)。
 * - duplicateOf:2026-08 使用者回饋,从交易明细点「複製」发起 —— 把備註/
 *   帳戶/分類/回饋項目/專案/標籤/拆帳等「屬性」欄位複製一份帶進新建表單,
 *   時間欄位不跟著複製(維持 txDefaults() 的「現在時間」),讓使用者自己
 *   改時間或其它內容再存成一筆新交易。故意不帶 refund_of_id/debt_id/
 *   attachments/reconciled_at/deferred_posting_at 這類「綁定特定原始事件」
 *   的欄位 —— 複製出來的是一筆全新、不relates 到原交易的獨立記錄。
 */
export type NewTxPrefill = {
  happenedAt?: string
  ledgerId?: string
  refundOf?: {
    id: string
    amount: number
    note: string | null
    categoryName: string | null
    accountName: string | null
    /** 原交易的类型 —— 决定退款交易反向应该是 income 还是 expense。 */
    origTxType: 'expense' | 'income'
  }
  duplicateOf?: WorkspaceTransaction
}

export function dispatchOpenNewTx(prefill?: NewTxPrefill) {
  window.dispatchEvent(new CustomEvent(NEW_TX_EVENT, { detail: prefill }))
}

/**
 * Phase 24(docs/PH17_USER_FEEDBACK_2026-08_SD.md 需求 #5):週期性收支產生
 * 的交易差異化編輯 —— 'single' 存檔時呼叫 `PATCH .../occurrences/{tx_id}`
 * (標記 overridden,只影響這一筆);'future' 呼叫
 * `POST .../update-from/{tx_id}`(套用到這期以後所有未 overridden 的已生成
 * 交易)。undefined = 一般交易,走既有 `updateTransaction`。
 */
export type EditTxOptions = {
  recurringEditMode?: 'single' | 'future'
}

export function dispatchOpenEditTx(tx: WorkspaceTransaction, options?: EditTxOptions) {
  window.dispatchEvent(new CustomEvent(EDIT_TX_EVENT, { detail: { tx, options } }))
}

export function dispatchOpenDetailTx(tx: WorkspaceTransaction) {
  window.dispatchEvent(new CustomEvent(DETAIL_TX_EVENT, { detail: tx }))
}

export function hasEditTxHandler(): boolean {
  return editTxHandlerCount > 0
}

export function onOpenNewTx(handler: (prefill?: NewTxPrefill) => void): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<NewTxPrefill | undefined>).detail
    handler(detail)
  }
  window.addEventListener(NEW_TX_EVENT, wrapped)
  return () => window.removeEventListener(NEW_TX_EVENT, wrapped)
}

export function onOpenEditTx(
  handler: (tx: WorkspaceTransaction, options?: EditTxOptions) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<{ tx: WorkspaceTransaction; options?: EditTxOptions } | undefined>).detail
    if (detail?.tx) handler(detail.tx, detail.options)
  }
  window.addEventListener(EDIT_TX_EVENT, wrapped)
  editTxHandlerCount += 1
  return () => {
    window.removeEventListener(EDIT_TX_EVENT, wrapped)
    editTxHandlerCount -= 1
  }
}

export function onOpenDetailTx(
  handler: (tx: WorkspaceTransaction) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceTransaction>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(DETAIL_TX_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_TX_EVENT, wrapped)
}

/**
 * Account/Category/Tag 详情弹窗的"账本作用域"开关。
 * - 'all'    : 跨账本聚合(默认从一级页面 资产/分类/标签 进入)
 * - 'current': 当前账本(从首页图表 / Top 卡片等场景进入)
 * 弹窗 UI 顶部有 segmented toggle 让用户切换,这里只是"打开时的默认值"。
 */
export type DetailScope = 'all' | 'current'

export type DetailOpenOptions = {
  defaultScope?: DetailScope
  /** 從交易詳情彈窗點「使用回饋」規則跳轉過來時(2026-08-04 新增):打開帳戶
   *  詳情彈窗後,自動展開紅利回饋區塊並直接彈出這條規則的交易明細彈窗 ——
   *  見 `AccountDetailDialog` / `CardRewardRulesSection` 的 `highlightRuleId`。 */
  highlightRewardRuleId?: string
}

// ========== Account ==========
const DETAIL_ACCOUNT_EVENT = 'bee:open-detail-account'

type AccountDetailPayload = {
  account: WorkspaceAccount
  defaultScope: DetailScope
  highlightRewardRuleId?: string
}

export function dispatchOpenDetailAccount(
  account: WorkspaceAccount,
  options?: DetailOpenOptions,
) {
  const payload: AccountDetailPayload = {
    account,
    defaultScope: options?.defaultScope ?? 'current',
    highlightRewardRuleId: options?.highlightRewardRuleId,
  }
  window.dispatchEvent(new CustomEvent(DETAIL_ACCOUNT_EVENT, { detail: payload }))
}

export function onOpenDetailAccount(
  handler: (account: WorkspaceAccount, defaultScope: DetailScope, highlightRewardRuleId?: string) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<AccountDetailPayload>).detail
    if (detail?.account) handler(detail.account, detail.defaultScope, detail.highlightRewardRuleId)
  }
  window.addEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_ACCOUNT_EVENT, wrapped)
}

const EDIT_ACCOUNT_EVENT = 'bee:open-edit-account'

/**
 * 从账户详情弹窗(可能是从任意页面通过 dispatchOpenDetailAccount 打开的,
 * 不一定在 /app/accounts)直接跳到"编辑这个账户"—— 比如信用卡帐单卡片的
 * 「前往帳戶設定」按钮。跟 dispatchOpenEditCategory 同款模式:AccountsPage
 * 是唯一监听者,自己决定怎么把 WorkspaceAccount 转成编辑表单的字段。
 */
export function dispatchOpenEditAccount(account: WorkspaceAccount) {
  window.dispatchEvent(new CustomEvent(EDIT_ACCOUNT_EVENT, { detail: account }))
}

export function onOpenEditAccount(
  handler: (account: WorkspaceAccount) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceAccount>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(EDIT_ACCOUNT_EVENT, wrapped)
  return () => window.removeEventListener(EDIT_ACCOUNT_EVENT, wrapped)
}

// ========== Tag ==========
const DETAIL_TAG_EVENT = 'bee:open-detail-tag'

type TagDetailPayload = {
  tag: WorkspaceTag
  defaultScope: DetailScope
}

export function dispatchOpenDetailTag(
  tag: WorkspaceTag,
  options?: DetailOpenOptions,
) {
  const payload: TagDetailPayload = {
    tag,
    defaultScope: options?.defaultScope ?? 'current',
  }
  window.dispatchEvent(new CustomEvent(DETAIL_TAG_EVENT, { detail: payload }))
}

export function onOpenDetailTag(
  handler: (tag: WorkspaceTag, defaultScope: DetailScope) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<TagDetailPayload>).detail
    if (detail?.tag) handler(detail.tag, detail.defaultScope)
  }
  window.addEventListener(DETAIL_TAG_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_TAG_EVENT, wrapped)
}

// ========== Category ==========
const DETAIL_CATEGORY_EVENT = 'bee:open-detail-category'
const EDIT_CATEGORY_EVENT = 'bee:open-edit-category'

type CategoryDetailPayload = {
  category: WorkspaceCategory
  defaultScope: DetailScope
}

export function dispatchOpenDetailCategory(
  category: WorkspaceCategory,
  options?: DetailOpenOptions,
) {
  const payload: CategoryDetailPayload = {
    category,
    defaultScope: options?.defaultScope ?? 'current',
  }
  window.dispatchEvent(
    new CustomEvent(DETAIL_CATEGORY_EVENT, { detail: payload }),
  )
}

export function dispatchOpenEditCategory(category: WorkspaceCategory) {
  window.dispatchEvent(
    new CustomEvent(EDIT_CATEGORY_EVENT, { detail: category }),
  )
}

export function onOpenDetailCategory(
  handler: (category: WorkspaceCategory, defaultScope: DetailScope) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<CategoryDetailPayload>).detail
    if (detail?.category) handler(detail.category, detail.defaultScope)
  }
  window.addEventListener(DETAIL_CATEGORY_EVENT, wrapped)
  return () => window.removeEventListener(DETAIL_CATEGORY_EVENT, wrapped)
}

export function onOpenEditCategory(
  handler: (category: WorkspaceCategory) => void,
): () => void {
  const wrapped = (e: Event) => {
    const detail = (e as CustomEvent<WorkspaceCategory>).detail
    if (detail) handler(detail)
  }
  window.addEventListener(EDIT_CATEGORY_EVENT, wrapped)
  editCategoryHandlerCount += 1
  return () => {
    window.removeEventListener(EDIT_CATEGORY_EVENT, wrapped)
    editCategoryHandlerCount -= 1
  }
}

export function hasEditCategoryHandler(): boolean {
  return editCategoryHandlerCount > 0
}
