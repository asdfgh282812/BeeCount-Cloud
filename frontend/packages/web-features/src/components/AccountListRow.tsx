import { useState } from 'react'

import { useT } from '@beecount/ui'

import type { ExchangeRateOverride, ExchangeRatesResponse, ReadAccount } from '@beecount/api-client'

import { Amount, type AmountSize } from './Amount'
import {
  effectiveRateToBase,
  hasCreditBillingFields,
  LIABILITY_TYPES,
  resolveAccountGroupDisplayType,
  type AssetGroup
} from '../lib/assetAggregation'

export const VALUATION_TYPES_SET = new Set([
  'real_estate',
  'vehicle',
  'investment',
  'insurance',
  'social_fund',
  'loan'
])

export type AccountStats = {
  balance?: number | null
  income_total?: number | null
  expense_total?: number | null
  /** account_group 底下有跨幣別子帳戶、且至少一筆因缺匯率被剔除時為 true,
   *  詳見 `src/schemas.py::WorkspaceAccountOut.balance_fx_incomplete`。 */
  balance_fx_incomplete?: boolean | null
}

// 账户类型 → 品牌 SVG 图标路径。SVG 已从 BeeCount (mobile) `assets/icons/*.svg`
// 拷到 `web/public/icons/account/`，公共资源目录直接通过 URL 访问即可（不用
// 打包到 bundle）。`other` 回退到 `other_account.svg`，其它直接同名。
export const TYPE_ICON_URL: Record<string, string> = {
  cash: '/icons/account/cash.svg',
  bank_card: '/icons/account/bank_card.svg',
  credit_card: '/icons/account/credit_card.svg',
  // 主帳戶(合併帳單,§2.9 Phase 4)是純管理容器,沒有专属图标素材,借用
  // bank_card 的图标(视觉上都是"机构/账户群组"的意象)。
  account_group: '/icons/account/bank_card.svg',
  alipay: '/icons/account/alipay.svg',
  wechat: '/icons/account/wechat.svg',
  other: '/icons/account/other_account.svg',
  real_estate: '/icons/account/real_estate.svg',
  vehicle: '/icons/account/vehicle.svg',
  investment: '/icons/account/investment.svg',
  insurance: '/icons/account/insurance.svg',
  social_fund: '/icons/account/social_fund.svg',
  loan: '/icons/account/loan.svg'
}

// 每种账户类型对应的品牌色，用于卡片边框/渐变。与 AssetCompositionDonut
// 的配色保持一致，这样 overview 的饼图和这里的分组颜色呼应。
export const TYPE_COLORS: Record<string, string> = {
  cash: '#10b981',
  bank_card: '#3b82f6',
  credit_card: '#ef4444',
  account_group: '#6366f1',
  alipay: '#06b6d4',
  wechat: '#22c55e',
  other: '#64748b',
  real_estate: '#8b5cf6',
  vehicle: '#f59e0b',
  investment: '#ec4899',
  insurance: '#14b8a6',
  social_fund: '#84cc16',
  loan: '#dc2626'
}

export function TypeIcon({ type, size = 28 }: { type: string; size?: number }) {
  const src = TYPE_ICON_URL[type] || TYPE_ICON_URL.other
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      className="block select-none"
      draggable={false}
    />
  )
}

/** 账户类型 label i18n 查找:先看 accountType.<value> key,回退到原始 value。
 *  参数 tt 是 useT() 返回的查找函数。 */
export function accountTypeLabel(tt: (k: string) => string, value?: string | null): string {
  if (!value) return '-'
  const key = `accountType.${value}`
  const translated = tt(key)
  // useT 没命中的 key 会把 key 原样返回,说明当前 locale 没定义
  if (translated === key) return value
  return translated
}

/**
 * 帳戶群組巢狀(需求 #8,2026-08):把 `listGroups` 裡帶 `parent_account_id`
 * 的子帳戶抽出來,按父帳戶 id 分桶——用於在父帳戶列下方縮排渲染,而不是跟其它
 * 同類型帳戶並列在同一層。`AccountsPanel`(資產頁列表)與 `AccountPickerDialog`
 * (交易表單帳戶選擇彈窗)共用同一份分桶邏輯,保持巢狀規則一致。
 */
export function buildAccountChildrenMap(groups: AssetGroup[]): {
  childrenByParent: Map<string, ReadAccount[]>
  childIds: Set<string>
} {
  const childrenByParent = new Map<string, ReadAccount[]>()
  for (const group of groups) {
    for (const row of group.rows) {
      if (!row.parent_account_id) continue
      const arr = childrenByParent.get(row.parent_account_id)
      if (arr) arr.push(row)
      else childrenByParent.set(row.parent_account_id, [row])
    }
  }
  const childIds = new Set<string>()
  for (const rows of childrenByParent.values()) {
    for (const row of rows) childIds.add(row.id)
  }
  return { childrenByParent, childIds }
}

/**
 * 緊湊清單列(需求 #8,2026-08):取代舊的漸層銀行卡網格,對齊 Moze 截圖「圖示 +
 * 名稱 + 金額」單行列表樣式。`childRows` 有值時渲染成可收合的主帳戶列(名稱旁
 * 徽章顯示子帳戶數),子帳戶縮排在下方巢狀清單裡渲染,不再各自佔一整張卡片、
 * 也不再跟其它同類型帳戶並列在同一層。
 *
 * `selectMode`(Phase 11 新增,帳戶選擇彈窗重用):隱藏編輯/刪除按鈕(不傳
 * `onEdit` 即可,行為零改動);`account_group`(純管理容器)類型的列在此模式
 * 下點擊只會展開/收合子帳戶,不會觸發 `onClick` 選中(容器本身不能被記帳
 * 選中,呼叫方仍可放心把它當一般 row 傳進來渲染巢狀結構)。
 */
export function AccountListRow({
  row,
  color,
  isLiability,
  canManage,
  onEdit,
  onDelete,
  onClick,
  avatarPreviewUrlByFileId,
  childRows,
  depth = 0,
  selectMode = false,
  hiddenBadge = false,
  baseCurrency,
  fxRates,
  fxOverrides,
  amountSize = 'sm'
}: {
  row: ReadAccount & AccountStats
  color: string
  isLiability: boolean
  canManage: boolean
  onEdit?: (row: ReadAccount) => void
  onDelete?: (row: ReadAccount) => void
  onClick?: (row: ReadAccount) => void
  avatarPreviewUrlByFileId?: Record<string, string>
  childRows?: ReadAccount[]
  depth?: number
  selectMode?: boolean
  hiddenBadge?: boolean
  /** 外幣列原幣顯示 + 快速換算按鈕(2026-08-12):使用者主幣種 + 已 fetch 好的
   *  匯率/手動 override。三者缺一,或 row 幣別跟 baseCurrency 相同,就不出現
   *  幣別代碼標籤/換算按鈕(維持原樣零影響)。 */
  baseCurrency?: string
  fxRates?: ExchangeRatesResponse | null
  fxOverrides?: ExchangeRateOverride[]
  /** 主要金額字號(2026-08-13 使用者回報資產頁列表金額太小):預設 `sm`(帳戶
   *  選擇彈窗等緊湊情境維持原樣),`AccountsPanel` 資產頁列表傳大一號的字級。
   *  子帳戶巢狀渲染時不繼續往下傳,保持子列比主列小一號的既有視覺層級。 */
  amountSize?: AmountSize
}) {
  const t = useT()
  const [expanded, setExpanded] = useState(true)
  // 換算按鈕本地開關:預設顯示原幣(對齊使用者需求「外幣戶應該預設顯示原始
  // 外幣」),點按鈕才切成主幣種金額——單純本地 state,換算本身是同步純函式
  // (effectiveRateToBase),不用另外 fetch。
  const [showConverted, setShowConverted] = useState(false)
  const currency = row.currency || 'CNY'
  const upperBase = (baseCurrency || '').trim().toUpperCase()
  const isForeign = Boolean(upperBase) && currency.toUpperCase() !== upperBase
  const fxEff = isForeign && fxRates
    ? effectiveRateToBase(currency.toUpperCase(), upperBase, fxRates, fxOverrides || [])
    : null
  const canConvert = isForeign && fxEff !== null
  const showConvertedNow = canConvert && showConverted
  const accountType = row.account_type || 'other'
  const isValuation = VALUATION_TYPES_SET.has(accountType)
  const hasStats =
    row.balance !== null &&
    row.balance !== undefined &&
    typeof row.balance === 'number'
  // 展示余额：优先用 stats.balance（考虑所有交易后的结果），否则 initial_balance。
  const displayBalance = hasStats ? (row.balance as number) : row.initial_balance ?? 0
  // 估值账户：负债显示绝对值欠款，资产显示当前估值。
  const valuationValue = isLiability ? Math.abs(displayBalance) : displayBalance
  // 信用卡：已用(欠款) = max(0, -balance),可用 = 额度 - 已用(对齐 mobile）。
  // account_group(主帳戶)未来也会挂靠银行帐户群组，不是天生就该走信用卡样式——
  // 「自己身上是否设了额度/帳單日/還款日」(hasCreditBillingFields)或「底下子帳戶
  // 是否全是信用卡」(resolveAccountGroupDisplayType,跟分組歸類同一份判斷) 任一
  // 成立就算信用卡群組;只看前者會漏掉「額度/帳單日設在群組上但這次沒填、純粹
  // 靠子帳戶類型歸類」的情況,導致主帳戶列跟子帳戶列的樣式判斷不一致(2026-08-12
  // 使用者回報:子帳戶顯示溢繳餘額,主帳戶卻走一般帳戶樣式)。
  const isCreditStyleGroup =
    accountType === 'account_group' &&
    (hasCreditBillingFields(row) ||
      resolveAccountGroupDisplayType(row, childRows || []) === 'credit_card')
  const isCreditCard = accountType === 'credit_card' || isCreditStyleGroup
  const ccLimit = typeof row.credit_limit === 'number' ? row.credit_limit : null
  // 欠款金額(負債方向);溢繳金額(balance 為正,卡片欠使用者錢,經濟上是資產頭寸)。
  const ccOwed = Math.max(0, -displayBalance)
  const ccOverpaid = Math.max(0, displayBalance)
  const ccAvailable = ccLimit !== null ? Math.max(0, ccLimit - ccOwed) : null
  // 「可繳款」提醒(§2.9 補強,2026-08-02):server 只在 billing-root 且真的
  // 欠款(> 0)時才回這两个字段,不用在这里重新判断是不是信用卡/群组。
  const dueBadgeDate =
    typeof row.billing_remaining_due === 'number' && row.billing_remaining_due > 0 && row.billing_due_date
      ? row.billing_due_date.slice(5, 10).replace('-', '/')
      : null

  // 帳戶頭像(2026-08-02 補強):使用者反饋光靠文字看不出是哪張卡 —— 有自訂
  // 頭像時圓形頭像取代類型圖示;没有头像时维持原本的类型 icon。
  const avatarFileId = row.avatar_cloud_file_id?.trim() || ''
  const avatarUrl = avatarFileId ? avatarPreviewUrlByFileId?.[avatarFileId] : undefined

  const hasChildren = Boolean(childRows && childRows.length > 0)
  // 一行只留一个「主要金额」:估值账户看估值/欠款,信用卡(含主帳戶群组)看
  // 欠款或溢繳金額的絕對值(不能只顯示 ccOwed——溢繳時 ccOwed 恆為 0,會把
  // 使用者真實存在的溢繳餘額顯示成「0」,2026-08-12 使用者回報的根因),
  // 其余可交易账户看余额——跟旧 BankCardTile 的主要数字口径一致。
  const primaryValue = isValuation ? valuationValue : isCreditCard ? Math.abs(displayBalance) : displayBalance
  const primaryTone: 'default' | 'negative' | 'positive' = isCreditCard
    ? ccOwed > 0
      ? 'negative'
      : ccOverpaid > 0
        ? 'positive'
        : 'default'
    : isLiability && displayBalance !== 0
      ? displayBalance < 0
        ? 'negative'
        : 'positive'
      : 'default'

  // 外幣列換算(2026-08-12):預設顯示原幣金額 + 幣別代碼標籤;點按鈕切成主
  // 幣種金額時,標籤跟著換成「≈主幣種代碼」提示這是換算後的近似值,不是原始
  // 記帳金額。fxEff 是純函式算出來的匯率,永遠跟 baseCurrency/fxRates/
  // fxOverrides 同步,不需要額外 loading state。
  const amountValue = showConvertedNow ? primaryValue * fxEff!.rate : primaryValue
  const amountCurrency = showConvertedNow ? upperBase : currency
  const currencyBadgeLabel = showConvertedNow ? `≈${upperBase}` : isForeign ? currency.toUpperCase() : null

  // 主帳戶列(有子帳戶且設了額度)顯示「可用額度」;子帳戶顯示卡號末四碼 ——
  // 两者互斥,不会同一列都出现。
  const showAvailableCredit = hasChildren && ccLimit !== null && ccAvailable !== null
  const showLastFour = !showAvailableCredit && Boolean(row.card_last_four)

  // 選擇模式下,account_group(純管理容器)不可被選中——只能點右側箭頭展開
  // 看子帳戶,對齊後端 `_assert_account_not_group` 的既有限制。
  const isSelectableBlocked = selectMode && accountType === 'account_group'
  const handleRowClick = onClick && !isSelectableBlocked ? () => onClick(row) : undefined

  return (
    <div>
      <div
        className="group flex items-center justify-between gap-3 py-2.5 pr-3 transition-colors hover:bg-muted/20"
        style={{ paddingLeft: `${14 + depth * 22}px` }}
      >
        <button
          type="button"
          onClick={handleRowClick}
          disabled={!handleRowClick}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left disabled:cursor-default"
        >
          {avatarUrl ? (
            <img
              src={avatarUrl}
              alt=""
              className="h-8 w-8 shrink-0 rounded-full object-cover ring-1 ring-border/50"
            />
          ) : (
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full"
              style={{ background: `${color}18`, border: `1px solid ${color}40` }}
            >
              <TypeIcon type={accountType} size={18} />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{row.name}</span>
              {hasChildren ? (
                <span className="shrink-0 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                  {childRows!.length}
                </span>
              ) : null}
              {hiddenBadge && row.hidden ? (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {t('accounts.hidden.optionSuffix')}
                </span>
              ) : null}
              {dueBadgeDate ? (
                <span className="shrink-0 rounded-full bg-amber-400/90 px-1.5 py-[1px] text-[9px] font-semibold text-amber-950">
                  {t('cardBilling.dueBadge', { date: dueBadgeDate })}
                </span>
              ) : null}
              {hasChildren && row.balance_fx_incomplete ? (
                <span
                  className="shrink-0 text-[10px] text-amber-500"
                  title={t('accounts.row.fxIncomplete')}
                  aria-label={t('accounts.row.fxIncomplete')}
                >
                  ⚠
                </span>
              ) : null}
            </div>
            {showAvailableCredit ? (
              <div className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                <span>{t('accounts.bankcard.availableCreditInline')}</span>
                <Amount
                  value={ccAvailable as number}
                  currency={currency}
                  showCurrency={false}
                  size="xs"
                  className="text-muted-foreground"
                />
              </div>
            ) : showLastFour ? (
              <div className="mt-0.5 text-[11px] text-muted-foreground">•••• {row.card_last_four}</div>
            ) : null}
          </div>
        </button>
        <div className="flex shrink-0 items-center gap-1.5">
          {currencyBadgeLabel ? (
            <span className="shrink-0 text-[10px] font-medium text-muted-foreground">
              {currencyBadgeLabel}
            </span>
          ) : null}
          <Amount value={amountValue} currency={amountCurrency} tone={primaryTone} bold size={amountSize} />
          {canConvert ? (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                setShowConverted((prev) => !prev)
              }}
              title={t('accounts.row.convertToggle')}
              aria-label={t('accounts.row.convertToggle')}
              className="shrink-0 rounded-full border border-border/50 px-1.5 py-0.5 text-[10px] leading-none text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              ⇄
            </button>
          ) : null}
          {hasChildren ? (
            <button
              type="button"
              onClick={() => setExpanded((prev) => !prev)}
              className="text-base text-muted-foreground"
            >
              <span
                className={`inline-block transition-transform ${expanded ? 'rotate-90' : ''}`}
                aria-hidden
              >
                ›
              </span>
            </button>
          ) : null}
          {!selectMode && onEdit ? (
            <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
              <button
                type="button"
                disabled={!canManage}
                onClick={(event) => {
                  event.stopPropagation()
                  onEdit(row)
                }}
                className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
              >
                {t('common.edit')}
              </button>
              {onDelete ? (
                <button
                  type="button"
                  disabled={!canManage}
                  onClick={(event) => {
                    event.stopPropagation()
                    onDelete(row)
                  }}
                  className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:pointer-events-none disabled:opacity-40"
                >
                  {t('common.delete')}
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
      {hasChildren && expanded ? (
        <div className="divide-y divide-border/30 border-t border-dashed border-border/30">
          {childRows!.map((child) => (
            <AccountListRow
              key={child.id}
              row={child}
              color={TYPE_COLORS[child.account_type || 'other'] || color}
              isLiability={LIABILITY_TYPES.has(child.account_type || '')}
              canManage={canManage}
              onEdit={onEdit}
              onDelete={onDelete}
              onClick={onClick}
              avatarPreviewUrlByFileId={avatarPreviewUrlByFileId}
              depth={depth + 1}
              selectMode={selectMode}
              hiddenBadge={hiddenBadge}
              baseCurrency={baseCurrency}
              fxRates={fxRates}
              fxOverrides={fxOverrides}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}
