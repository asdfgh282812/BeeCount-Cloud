import { useEffect, useRef, useState } from 'react'

import type {
  CardRewardInterval,
  CardRewardRateType,
  CardRewardRounding,
  CardRewardSettlementType,
  ReadAccount,
  ReadCardRewardRule,
  ReadCardRewardRuleTransactions,
  ReadCardRewards
} from '@beecount/api-client'
import {
  createCardRewardRule,
  deleteCardRewardRule,
  fetchAllCardRewardRules,
  fetchCardRewardRules,
  fetchCardRewardRuleTransactions,
  fetchCardRewards,
  fetchReadAccounts,
  manualCardRewardPayout,
  updateCardRewardRule
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
  useToast
} from '@beecount/ui'
import { Copy, Gift, Pencil, Trash2 } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { localizeError } from '../../i18n/errors'

type RetryOnConflict = <T,>(ledgerId: string, submit: (baseChangeId: number) => Promise<T>) => Promise<T>

function isoToDateInput(iso: string): string {
  // 後端部分欄位(card_reward_rule.starts_at/ends_at、qualifying tx 的
  // happened_at)回傳的是不帶時區位移的 naive datetime 字串(SQLite 讀回來
  // 沒有 tzinfo)。`new Date("2026-08-02T00:00:00")` 在沒有位移標記時會被
  // JS 當成「本地時間」解析,UTC+8 使用者會少算一天(2026-08-02 本地
  // 被換成 2026-08-01T16:00:00Z)。缺位移標記時補上 "Z" 強制當 UTC 解析,
  // 跟後端「這串日期本來就是 UTC」的語意對齊。
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  const d = new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

function dateInputToIso(value: string): string {
  return `${value}T00:00:00+00:00`
}

/** 跟 `isoToDateInput` 同一個理由:naive datetime 字串缺位移標記時強制當
 *  UTC 解析,不然 UTC+8 使用者這裡會把 `ends_at` 提早 8 小時判定過期
 *  (2026-08-07 補上,原本 `isExpired`/`activePeriodText` 都直接用
 *  `new Date(iso)`,跟 `isoToDateInput` 修過的瑕疵是同一類)。 */
function forceUtcTimestamp(iso: string): number {
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  return new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso).getTime()
}

function isExpired(rule: ReadCardRewardRule): boolean {
  if (!rule.ends_at) return false
  return forceUtcTimestamp(rule.ends_at) < Date.now()
}

/** 規則列表行顯示活動期間(2026-08-07 使用者反饋:設定了活動開始/結束日,
 *  但規則清單完全沒有顯示,只有點進編輯表單才看得到)——只有 starts_at/
 *  ends_at 任一有值時才顯示,兩個都沒設就回傳 null 讓呼叫端跳過整行。 */
function activePeriodText(
  rule: ReadCardRewardRule,
  t: (key: string, params?: Record<string, string | number>) => string,
): string | null {
  if (!rule.starts_at && !rule.ends_at) return null
  const start = rule.starts_at ? isoToDateInput(rule.starts_at) : null
  const end = rule.ends_at ? isoToDateInput(rule.ends_at) : null
  if (start && end) return t('cardRewards.summary.activePeriodRange', { start, end })
  if (start) return t('cardRewards.summary.activePeriodFrom', { start })
  return t('cardRewards.summary.activePeriodUntil', { end: end! })
}

/**
 * 信用卡紅利回饋(§2.9.5 Phase 4.5,2026-08-07 補強:活動期間 + 隱藏過期 +
 * 複製 + 跨卡共用上限群組挑選 + 交易明細彈窗 + 自動入帳結算欄位)—— 規則
 * 管理(CRUD)+ 當期回饋預覽,掛在帳戶詳情彈窗裡,緊接著
 * `CreditCardBillingSection`。規則綁定的是一張真實的 `credit_card` 帳戶
 * (不管是獨立卡還是掛靠群組的子卡),不是 `account_group` 本身 —— 群組
 * 純管理容器自己不會被刷卡,見 `write/card_reward_rules.py`
 * `_assert_account_is_credit_card`。
 *
 * 2026-08-04 使用者反饋修復:主帳戶(account_group)詳情彈窗打開時這個區塊
 * 整個不見了——舊版只在 `accountType === 'credit_card'` 才渲染,account_
 * group 本身不是信用卡帳戶所以直接跳過,連它底下掛靠的子卡各自設定的回饋
 * 規則都完全看不到。修法:外層 `CardRewardRulesSection` 改成依 accountType
 * 分派——`credit_card` 沿用單卡渲染;`account_group` 查它底下所有
 * `account_type === 'credit_card'` 的子帳戶,每張子卡各自渲染一份(標題帶
 * 卡片名稱區分),沒有任何子卡設過回饋規則時才整塊不渲染;其它帳戶類型
 * (cash/bank_card 等)維持原樣不渲染。實際的規則 CRUD + 當期回饋計算邏輯
 * 完全不變,抽進 `SingleCardCardRewards` 內部元件,對單卡場景零行為差異。
 */
export function CardRewardRulesSection({
  accountId,
  accountType,
  periodOffset = 0,
  highlightRuleId,
}: {
  accountId: string
  accountType: string | null | undefined
  /** 跟著上面 CreditCardBillingSection 的帳單週期選擇器走(§2.9.5.4),
   *  非 billing-root 帳戶固定 0(目前還在累積的那期)。 */
  periodOffset?: number
  /** 從交易詳情彈窗「使用回饋」chip 跳過來時帶(2026-08-04 補強):比對到
   *  哪張子卡的規則清單裡有這個 id,就自動展開那張子卡的區塊 + 打開這條
   *  規則的交易明細彈窗 —— 主帳戶(account_group)場景下每張子卡都會收到
   *  同一個 id,只有真的命中的那張會有反應。 */
  highlightRuleId?: string
}) {
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const [groupChildren, setGroupChildren] = useState<ReadAccount[]>([])

  const isGroup = (accountType || '') === 'account_group'

  useEffect(() => {
    if (!isGroup || !token || !activeLedgerId) {
      setGroupChildren([])
      return
    }
    let cancelled = false
    fetchReadAccounts(token, activeLedgerId)
      .then((accounts) => {
        if (cancelled) return
        setGroupChildren(
          accounts.filter((a) => a.account_type === 'credit_card' && a.parent_account_id === accountId),
        )
      })
      .catch(() => {
        if (!cancelled) setGroupChildren([])
      })
    return () => {
      cancelled = true
    }
  }, [isGroup, token, activeLedgerId, accountId])

  if ((accountType || '') === 'credit_card') {
    return (
      <SingleCardCardRewards accountId={accountId} periodOffset={periodOffset} highlightRuleId={highlightRuleId} />
    )
  }
  if (!isGroup || groupChildren.length === 0) return null
  return (
    <div className="space-y-3">
      {groupChildren.map((child) => (
        <SingleCardCardRewards
          key={child.id}
          accountId={child.id}
          accountLabel={child.name}
          periodOffset={periodOffset}
          highlightRuleId={highlightRuleId}
        />
      ))}
    </div>
  )
}

function SingleCardCardRewards({
  accountId,
  accountLabel,
  periodOffset = 0,
  highlightRuleId,
}: {
  accountId: string
  /** 主帳戶(account_group)展開多張子卡時,各自標題帶卡片名稱區分;單卡
   *  detail dialog 直接渲染時不傳,標題維持原樣不變。 */
  accountLabel?: string
  periodOffset?: number
  highlightRuleId?: string
}) {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const toast = useToast()

  const [rules, setRules] = useState<ReadCardRewardRule[]>([])
  const [rewards, setRewards] = useState<ReadCardRewards | null>(null)
  const [loading, setLoading] = useState(false)
  // 2026-08-05 使用者反饋:原本點標題原地展開的清單,即便收合起來也會把
  // 下面的交易明細往下推。改成點標題開一個獨立彈出視窗,詳情彈窗本身的
  // 版面高度不再被這個區塊的展開狀態影響。
  const [dialogOpen, setDialogOpen] = useState(false)
  const [showExpired, setShowExpired] = useState(false)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<ReadCardRewardRule | null>(null)
  const [duplicateSeed, setDuplicateSeed] = useState<ReadCardRewardRule | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [detailRule, setDetailRule] = useState<ReadCardRewardRule | null>(null)
  // 記住「這個 highlightRuleId 已經自動展開/打開過一次」,避免規則清單重新
  // 整理(比如切換帳單週期)時重複強制展開/彈出使用者已經手動關掉的明細
  // 彈窗。帳戶切換時(元件重新掛載或 accountId 變化)重置。
  const appliedHighlightRef = useRef<string | null>(null)

  const reload = () => {
    if (!token || !activeLedgerId) return
    setLoading(true)
    Promise.all([
      fetchCardRewardRules(token, activeLedgerId, accountId),
      fetchCardRewards(token, activeLedgerId, accountId, periodOffset),
    ])
      .then(([r, rw]) => {
        setRules(r)
        setRewards(rw)
      })
      .catch(() => {
        setRules([])
        setRewards(null)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    setDialogOpen(false)
    setShowExpired(false)
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, token, activeLedgerId, periodOffset])

  // 帳戶切換時重置「是否已自動展開過」的標記,不然換一張沒有這條規則的卡
  // 又切回來時不會再觸發。
  useEffect(() => {
    appliedHighlightRef.current = null
  }, [accountId])

  // 從交易詳情彈窗跳過來(highlightRuleId 非空)時,規則清單載入後自動展開
  // + 直接打開這條規則的交易明細彈窗,不用使用者自己再找一次。只在真的命中
  // 這張卡的規則清單時才動作,同一個 highlightRuleId 只自動觸發一次(使用者
  // 手動關掉明細彈窗後不會被重新強制打開)。
  useEffect(() => {
    if (!highlightRuleId || appliedHighlightRef.current === highlightRuleId) return
    const match = rules.find((r) => r.id === highlightRuleId)
    if (!match) return
    appliedHighlightRef.current = highlightRuleId
    setDialogOpen(true)
    setDetailRule(match)
  }, [rules, highlightRuleId])

  if (!activeLedgerId) return null

  const usageByRuleId = new Map((rewards?.items || []).map((item) => [item.rule_id, item]))
  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  const visibleRules = rules.filter((r) => showExpired || !isExpired(r))
  const hiddenExpiredCount = rules.length - visibleRules.length

  const handleDelete = async (ruleId: string) => {
    if (!token || !activeLedgerId) return
    setDeletingId(ruleId)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        deleteCardRewardRule(token, activeLedgerId, accountId, ruleId, base),
      )
      toast.success(t('cardRewards.notice.deleted'), t('notice.success'))
      reload()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-3 border-b border-border/60 bg-muted/10 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground"
          onClick={() => setDialogOpen(true)}
        >
          <Gift className="h-3.5 w-3.5" />
          {t('cardRewards.title')}
          {accountLabel ? (
            <span className="font-normal text-[10px] text-muted-foreground/80">· {accountLabel}</span>
          ) : null}
          {periodOffset !== 0 && rewards ? (
            <span className="font-normal text-[10px] text-muted-foreground/80">
              ({isoToDateInput(rewards.as_of)})
            </span>
          ) : null}
        </button>
        {rewards && rewards.total_reward > 0 ? (
          <span className="font-mono text-xs font-semibold tabular-nums text-income">
            {t('cardRewards.totalReward')}: {fmt(rewards.total_reward)}
          </span>
        ) : null}
      </div>
      {loading ? <div className="text-xs text-muted-foreground">{t('cardRewards.loading')}</div> : null}

      {/* 2026-08-05 使用者反饋:規則清單改到彈出視窗裡顯示,不再原地展開
          （原地展開即使收合仍會把下面的交易明細往下推）。 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="flex max-h-[80vh] max-w-md flex-col gap-0 p-0">
          <DialogHeader className="border-b border-border/60 px-5 py-3">
            <DialogTitle className="flex items-center gap-1.5 text-base">
              <Gift className="h-4 w-4" />
              {t('cardRewards.title')}
              {accountLabel ? (
                <span className="text-xs font-normal text-muted-foreground">· {accountLabel}</span>
              ) : null}
            </DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-4">
            {hiddenExpiredCount > 0 ? (
              <button
                type="button"
                className="text-[11px] text-muted-foreground underline underline-offset-2"
                onClick={() => setShowExpired((v) => !v)}
              >
                {showExpired
                  ? t('cardRewards.hideExpired')
                  : t('cardRewards.showExpired', { count: hiddenExpiredCount })}
              </button>
            ) : null}
            {visibleRules.length === 0 ? (
              <div className="text-xs text-muted-foreground">{t('cardRewards.empty')}</div>
            ) : (
              visibleRules.map((rule) => {
                const usage = usageByRuleId.get(rule.id)
                const expired = isExpired(rule)
                return (
                  <div
                    key={rule.id}
                    className="rounded-lg border border-border/50 bg-background/40 p-3 text-xs"
                  >
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-2 text-left"
                      onClick={() => setDetailRule(rule)}
                    >
                      <div className="flex items-center gap-2 font-medium">
                        <span>{rule.label}</span>
                        {!rule.enabled ? (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {t('cardRewards.disabled')}
                          </span>
                        ) : null}
                        {expired ? (
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            {t('cardRewards.expiredBadge')}
                          </span>
                        ) : null}
                      </div>
                    </button>
                    <div className="mt-1 flex items-center justify-between gap-2">
                      <div className="text-muted-foreground">
                        {rule.rate_type === 'percentage'
                          ? t('cardRewards.summary.percentage', { rate: rule.rate_value })
                          : t('cardRewards.summary.fixedAmount', { amount: rule.rate_value })}
                        {rule.cap_amount ? ` · ${t('cardRewards.summary.cap', { cap: fmt(rule.cap_amount) })}` : ''}
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          type="button"
                          className="rounded-md p-1 text-muted-foreground hover:bg-muted/60"
                          onClick={() => {
                            setDuplicateSeed(rule)
                            setEditing(null)
                            setFormOpen(true)
                          }}
                          aria-label={t('cardRewards.duplicate')}
                        >
                          <Copy className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          className="rounded-md p-1 text-muted-foreground hover:bg-muted/60"
                          onClick={() => {
                            setEditing(rule)
                            setDuplicateSeed(null)
                            setFormOpen(true)
                          }}
                          aria-label={t('cardRewards.edit')}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          disabled={deletingId === rule.id}
                          className="rounded-md p-1 text-muted-foreground hover:bg-muted/60 disabled:opacity-40"
                          onClick={() => {
                            if (window.confirm(t('cardRewards.confirmDelete'))) void handleDelete(rule.id)
                          }}
                          aria-label={t('cardRewards.delete')}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                    {activePeriodText(rule, t) ? (
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {activePeriodText(rule, t)}
                      </div>
                    ) : null}
                    {usage ? (
                      usage.status === 'no_billing_schedule' ? (
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {t('cardRewards.status.noBillingSchedule')}
                        </div>
                      ) : usage.status === 'expired' ? (
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          {t('cardRewards.status.expired')}
                        </div>
                      ) : (
                        <div className="mt-2 flex items-center justify-between">
                          <span className="text-[11px] text-muted-foreground">
                            {t('cardRewards.qualifyingSpend')}: {fmt(usage.qualifying_spend)}
                            {!usage.threshold_met ? ` (${t('cardRewards.thresholdNotMet')})` : ''}
                          </span>
                          <span className="font-mono font-semibold tabular-nums text-income">
                            {fmt(usage.capped_reward)}
                          </span>
                        </div>
                      )
                    ) : null}
                  </div>
                )
              })
            )}
            <button
              type="button"
              className="w-full rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted/50"
              onClick={() => {
                setEditing(null)
                setDuplicateSeed(null)
                setFormOpen(true)
              }}
            >
              {t('cardRewards.addRule')}
            </button>
          </div>
        </DialogContent>
      </Dialog>

      {formOpen ? (
        <CardRewardRuleFormDialog
          token={token || ''}
          ledgerId={activeLedgerId}
          accountId={accountId}
          rule={editing}
          seed={duplicateSeed}
          retryOnConflict={retryOnConflict}
          onClose={() => setFormOpen(false)}
          onSaved={() => {
            setFormOpen(false)
            reload()
          }}
        />
      ) : null}

      {detailRule ? (
        <CardRewardRuleTransactionsDialog
          token={token || ''}
          ledgerId={activeLedgerId}
          accountId={accountId}
          rule={detailRule}
          periodOffset={periodOffset}
          retryOnConflict={retryOnConflict}
          onClose={() => setDetailRule(null)}
          onPaidOut={reload}
        />
      ) : null}
    </div>
  )
}

function CardRewardRuleFormDialog({
  token,
  ledgerId,
  accountId,
  rule,
  seed,
  retryOnConflict,
  onClose,
  onSaved,
}: {
  token: string
  ledgerId: string
  accountId: string
  rule: ReadCardRewardRule | null
  /** 複製規則時的來源(rule 為 null,送出走 create)。 */
  seed: ReadCardRewardRule | null
  retryOnConflict: RetryOnConflict
  onClose: () => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const isEdit = Boolean(rule)
  const source = rule ?? seed

  const [label, setLabel] = useState(source?.label || '')
  const [rateType, setRateType] = useState<CardRewardRateType>(source?.rate_type || 'percentage')
  const [rateValue, setRateValue] = useState(source ? String(source.rate_value) : '')
  const [rounding, setRounding] = useState<CardRewardRounding>(source?.rounding || 'round')
  const [intervalMode, setIntervalMode] = useState<CardRewardInterval>(source?.interval || 'billing_cycle')
  const [minSpendThreshold, setMinSpendThreshold] = useState(
    source?.min_spend_threshold != null ? String(source.min_spend_threshold) : '',
  )
  const [minTxAmount, setMinTxAmount] = useState(
    source?.min_tx_amount != null ? String(source.min_tx_amount) : '',
  )
  const [capAmount, setCapAmount] = useState(source?.cap_amount != null ? String(source.cap_amount) : '')
  const [startsAt, setStartsAt] = useState(source?.starts_at ? isoToDateInput(source.starts_at) : '')
  const [endsAt, setEndsAt] = useState(source?.ends_at ? isoToDateInput(source.ends_at) : '')
  const [settlementType, setSettlementType] = useState<CardRewardSettlementType>(
    source?.settlement_type || 'manual',
  )
  const [settlementDays, setSettlementDays] = useState(
    source?.settlement_days != null ? String(source.settlement_days) : '',
  )
  const [rewardAccountId, setRewardAccountId] = useState(source?.reward_account_id || '')
  const [note, setNote] = useState(source?.note || '')
  const [enabled, setEnabled] = useState(source?.enabled ?? true)
  const [submitting, setSubmitting] = useState(false)

  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [allRules, setAllRules] = useState<ReadCardRewardRule[]>([])
  // 等 fetchAllCardRewardRules 載入後,在下面的 effect 裡依 cap_shared_key
  // 反推選取狀態。
  const [capGroupSelection, setCapGroupSelection] = useState<Set<string>>(new Set())

  useEffect(() => {
    // 兩個 fetch 故意分開 catch(§2.9.5.4 補強)——原本用 Promise.all 共用一個
    // catch,任一個失敗(例如帳戶數量多時 fetchAllCardRewardRules 較慢/偶發
    // 逾時)會連帶把已經成功的 accounts 也清空,導致回饋帳戶下拉選單看起來
    // 「什麼都選不到」,但其實只是共用上限群組那份資料沒抓到而已。
    fetchReadAccounts(token, ledgerId)
      .then(setAccounts)
      .catch(() => setAccounts([]))
    fetchAllCardRewardRules(token, ledgerId)
      .then((rules) => {
        setAllRules(rules)
        if (source?.cap_shared_key) {
          const key = source.cap_shared_key
          const members = new Set(
            rules.filter((r) => r.id !== source.id && r.cap_shared_key === key).map((r) => r.id),
          )
          setCapGroupSelection(members)
        }
      })
      .catch(() => setAllRules([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, ledgerId])

  const accountNameById = new Map(accounts.map((a) => [a.id, a.name]))
  const otherRules = allRules.filter((r) => r.id !== source?.id)

  const isCreditCardAccount = (id: string) =>
    accounts.find((a) => a.id === id)?.account_type === 'credit_card'

  const needsSettlementDays = settlementType === 'immediate_after_tx' || settlementType === 'after_posting_date'
  const needsRewardAccount = settlementType !== 'manual'

  const canSubmit =
    label.trim().length > 0 &&
    Number(rateValue) > 0 &&
    (!needsSettlementDays || settlementDays.trim().length > 0) &&
    (!needsRewardAccount || rewardAccountId.trim().length > 0)

  const toggleCapGroupMember = (ruleId: string) => {
    setCapGroupSelection((prev) => {
      const next = new Set(prev)
      if (next.has(ruleId)) next.delete(ruleId)
      else next.add(ruleId)
      return next
    })
  }

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      // §2.9.5.4:共用上限群組改成從既有規則挑選加入,cap_shared_key 底層
      // 仍是字串,由這裡生成/重用——選中的規則裡如果已經有 cap_shared_key,
      // 沿用那一個;都沒有的話,有勾選才生成新 key;完全沒勾選則清空。
      // 選中規則橫跨兩個以上既有的不同 key 時直接擋掉,避免把兩個群組意外
      // 併在一起。
      const selectedRules = otherRules.filter((r) => capGroupSelection.has(r.id))
      const existingKeys = new Set(selectedRules.map((r) => r.cap_shared_key).filter((k): k is string => !!k))
      if (existingKeys.size > 1) {
        toast.error(t('cardRewards.error.multipleCapGroups'), t('notice.error'))
        setSubmitting(false)
        return
      }
      let effectiveCapKey: string | null = null
      if (existingKeys.size === 1) {
        effectiveCapKey = [...existingKeys][0]
      } else if (capGroupSelection.size > 0) {
        effectiveCapKey = crypto.randomUUID()
      }

      const payload = {
        label: label.trim(),
        category_ids: null,
        rate_type: rateType,
        rate_value: Number(rateValue),
        rounding,
        calc_basis: 'transaction_date' as const,
        interval: intervalMode,
        min_spend_threshold: minSpendThreshold ? Number(minSpendThreshold) : null,
        min_tx_amount: minTxAmount ? Number(minTxAmount) : null,
        cap_amount: capAmount ? Number(capAmount) : null,
        cap_shared_key: effectiveCapKey,
        starts_at: startsAt ? dateInputToIso(startsAt) : null,
        ends_at: endsAt ? dateInputToIso(endsAt) : null,
        settlement_type: settlementType,
        settlement_days: needsSettlementDays ? Number(settlementDays) : null,
        reward_account_id: needsRewardAccount ? rewardAccountId : null,
        note: note.trim() || null,
        enabled,
      }

      if (isEdit && rule) {
        await retryOnConflict(ledgerId, (base) =>
          updateCardRewardRule(token, ledgerId, accountId, rule.id, base, payload),
        )
        toast.success(t('cardRewards.notice.updated'), t('notice.success'))
      } else {
        await retryOnConflict(ledgerId, (base) =>
          createCardRewardRule(token, ledgerId, accountId, base, payload),
        )
        toast.success(t('cardRewards.notice.created'), t('notice.success'))
      }

      // 同步群組成員:新加入的規則 PATCH 成同一個 key,原本屬於這個群組
      // 但這次被取消勾選的規則 PATCH 清空 key。每條規則各自屬於哪張卡,
      // ledger_id 一律用當前這個(card_reward_rule 是 user-global 實體,
      // 寫入端點的 ledger_id 只拿來過 CAS 鎖,不影響歸屬)。
      const previousMembers = new Set(
        source?.cap_shared_key
          ? otherRules.filter((r) => r.cap_shared_key === source.cap_shared_key).map((r) => r.id)
          : [],
      )
      const toAdd = [...capGroupSelection].filter((id) => !previousMembers.has(id))
      const toRemove = [...previousMembers].filter((id) => !capGroupSelection.has(id))
      for (const id of toAdd) {
        const member = allRules.find((r) => r.id === id)
        if (!member) continue
        await retryOnConflict(ledgerId, (base) =>
          updateCardRewardRule(token, ledgerId, member.account_id, id, base, {
            cap_shared_key: effectiveCapKey,
          }),
        )
      }
      for (const id of toRemove) {
        const member = allRules.find((r) => r.id === id)
        if (!member) continue
        await retryOnConflict(ledgerId, (base) =>
          updateCardRewardRule(token, ledgerId, member.account_id, id, base, {
            cap_shared_key: null,
          }),
        )
      }
      onSaved()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>
            {isEdit
              ? t('cardRewards.dialog.editTitle')
              : seed
                ? t('cardRewards.dialog.duplicateTitle')
                : t('cardRewards.dialog.createTitle')}
          </DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] space-y-3 overflow-y-auto pr-1">
          <div className="space-y-1">
            <Label>{t('cardRewards.field.label')}</Label>
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={t('cardRewards.field.labelPlaceholder')}
            />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label>{t('cardRewards.field.rateType')}</Label>
              <Select value={rateType} onValueChange={(v) => setRateType(v as CardRewardRateType)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="percentage">{t('cardRewards.rateType.percentage')}</SelectItem>
                  <SelectItem value="fixed_amount">{t('cardRewards.rateType.fixed_amount')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>
                {rateType === 'percentage'
                  ? t('cardRewards.field.rateValuePercent')
                  : t('cardRewards.field.rateValueAmount')}
              </Label>
              <Input
                type="number"
                inputMode="decimal"
                value={rateValue}
                onChange={(e) => setRateValue(e.target.value)}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label>{t('cardRewards.field.interval')}</Label>
              <Select value={intervalMode} onValueChange={(v) => setIntervalMode(v as CardRewardInterval)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="billing_cycle">{t('cardRewards.interval.billing_cycle')}</SelectItem>
                  <SelectItem value="calendar_month">{t('cardRewards.interval.calendar_month')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('cardRewards.field.rounding')}</Label>
              <Select value={rounding} onValueChange={(v) => setRounding(v as CardRewardRounding)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="round">{t('cardRewards.rounding.round')}</SelectItem>
                  <SelectItem value="floor">{t('cardRewards.rounding.floor')}</SelectItem>
                  <SelectItem value="ceil">{t('cardRewards.rounding.ceil')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label>{t('cardRewards.field.minTxAmount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                value={minTxAmount}
                onChange={(e) => setMinTxAmount(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('cardRewards.field.minSpendThreshold')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                value={minSpendThreshold}
                onChange={(e) => setMinSpendThreshold(e.target.value)}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label>{t('cardRewards.field.startsAt')}</Label>
              <Input type="date" value={startsAt} onChange={(e) => setStartsAt(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label>{t('cardRewards.field.endsAt')}</Label>
              <Input type="date" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} />
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('cardRewards.field.capAmount')}</Label>
            <Input
              type="number"
              inputMode="decimal"
              value={capAmount}
              onChange={(e) => setCapAmount(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('cardRewards.field.capGroup')}</Label>
            <div className="text-[11px] text-muted-foreground">{t('cardRewards.field.capGroupHint')}</div>
            {otherRules.length === 0 ? (
              <div className="text-[11px] text-muted-foreground">{t('cardRewards.field.capGroupEmpty')}</div>
            ) : (
              <div className="flex max-h-32 flex-wrap gap-1.5 overflow-y-auto">
                {otherRules.map((r) => {
                  const checked = capGroupSelection.has(r.id)
                  const accName = accountNameById.get(r.account_id) || r.account_id
                  return (
                    <button
                      key={r.id}
                      type="button"
                      onClick={() => toggleCapGroupMember(r.id)}
                      className={`rounded-full border px-2.5 py-1 text-[11px] ${
                        checked
                          ? 'border-primary bg-primary/10 text-primary'
                          : 'border-border text-muted-foreground'
                      }`}
                    >
                      {accName} - {r.label}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          <div className="space-y-1 rounded-md border border-border/60 p-2">
            <Label>{t('cardRewards.field.settlementType')}</Label>
            <Select
              value={settlementType}
              onValueChange={(v) => setSettlementType(v as CardRewardSettlementType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="manual">{t('cardRewards.settlementType.manual')}</SelectItem>
                <SelectItem value="immediate_after_tx">
                  {t('cardRewards.settlementType.immediate_after_tx')}
                </SelectItem>
                <SelectItem value="after_posting_date">
                  {t('cardRewards.settlementType.after_posting_date')}
                </SelectItem>
                <SelectItem value="period_end">{t('cardRewards.settlementType.period_end')}</SelectItem>
              </SelectContent>
            </Select>
            {needsSettlementDays ? (
              <div className="space-y-1 pt-1">
                <Label>{t('cardRewards.field.settlementDays')}</Label>
                <Input
                  type="number"
                  inputMode="numeric"
                  value={settlementDays}
                  onChange={(e) => setSettlementDays(e.target.value)}
                />
              </div>
            ) : null}
            {needsRewardAccount ? (
              <div className="space-y-1 pt-1">
                <Label>{t('cardRewards.field.rewardAccount')}</Label>
                <Select value={rewardAccountId} onValueChange={setRewardAccountId}>
                  <SelectTrigger>
                    <SelectValue placeholder={t('cardRewards.field.rewardAccountPlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {accounts
                      .filter((a) => a.account_type !== 'account_group')
                      .map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.id === accountId
                            ? `${a.name}${t('cardRewards.field.rewardAccountSelfSuffix')}`
                            : a.name}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                {rewardAccountId && !isCreditCardAccount(rewardAccountId) && rewardAccountId !== accountId ? (
                  <div className="text-[11px] text-muted-foreground">
                    {t('cardRewards.field.rewardAccountWalletHint')}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="space-y-1">
            <Label>{t('cardRewards.field.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            {t('cardRewards.field.enabled')}
          </label>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="rounded-md border border-border px-3 py-1.5 text-sm" onClick={onClose}>
            {t('dialog.cancel')}
          </button>
          <button
            type="button"
            disabled={submitting || !canSubmit}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={() => void handleSubmit()}
          >
            {t('cardRewards.dialog.submit')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** §2.9.5.3:單一規則的交易明細彈窗 —— 命中哪些交易 + 剩餘回饋額度。
 *  §2.9.5.4 補強(2026-08-03):`settlement_type === 'manual'` 的規則在這裡
 *  加一個「手動入帳」按鈕/表單,使用者自己臨時指定金額/目的帳戶。 */
function CardRewardRuleTransactionsDialog({
  token,
  ledgerId,
  accountId,
  rule,
  periodOffset,
  retryOnConflict,
  onClose,
  onPaidOut,
}: {
  token: string
  ledgerId: string
  accountId: string
  rule: ReadCardRewardRule
  periodOffset: number
  retryOnConflict: RetryOnConflict
  onClose: () => void
  onPaidOut: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [detail, setDetail] = useState<ReadCardRewardRuleTransactions | null>(null)
  const [loading, setLoading] = useState(true)
  // 區分「fetch 真的失敗」跟「fetch 成功但這期 status != 'ok'」——原本兩者
  // 都被 catch(() => setDetail(null)) 吞成同一句「帳單日未設定」文案,使用者
  // 帳戶明明已經設好帳單日/繳款日卻看到這則訊息時完全無從判斷真正原因
  // (2026-08-03 使用者反饋)。fetchError 為 true 時改顯示中性的「載入失敗」
  // 文案,不冒充成某個具體業務狀態。
  const [fetchError, setFetchError] = useState(false)

  const [accounts, setAccounts] = useState<ReadAccount[]>([])
  const [payoutOpen, setPayoutOpen] = useState(false)
  const [payoutAmount, setPayoutAmount] = useState('')
  const [payoutAccountId, setPayoutAccountId] = useState('')
  const [payoutSubmitting, setPayoutSubmitting] = useState(false)

  const load = () => {
    setLoading(true)
    setFetchError(false)
    fetchCardRewardRuleTransactions(token, ledgerId, accountId, rule.id, periodOffset)
      .then((d) => setDetail(d))
      .catch(() => {
        setDetail(null)
        setFetchError(true)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, ledgerId, accountId, rule.id, periodOffset])

  useEffect(() => {
    if (rule.settlement_type !== 'manual') return
    fetchReadAccounts(token, ledgerId)
      .then(setAccounts)
      .catch(() => setAccounts([]))
  }, [token, ledgerId, rule.settlement_type])

  useEffect(() => {
    if (detail && detail.status === 'ok' && !payoutAmount) {
      setPayoutAmount(detail.capped_reward > 0 ? String(detail.capped_reward) : '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail])

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  const handleManualPayout = async () => {
    const amount = Number(payoutAmount)
    if (!(amount > 0) || !payoutAccountId) return
    setPayoutSubmitting(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        manualCardRewardPayout(token, ledgerId, accountId, rule.id, base, {
          amount,
          reward_account_id: payoutAccountId,
        }),
      )
      toast.success(t('cardRewards.notice.paidOut'), t('notice.success'))
      setPayoutOpen(false)
      onPaidOut()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setPayoutSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{rule.label}</DialogTitle>
        </DialogHeader>
        {rule.settlement_type === 'manual' ? (
          <div className="rounded-md border border-dashed border-border p-2">
            {!payoutOpen ? (
              <button
                type="button"
                className="w-full text-xs font-medium text-primary"
                onClick={() => setPayoutOpen(true)}
              >
                {t('cardRewards.detail.manualPayoutAction')}
              </button>
            ) : (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <div className="space-y-1">
                    <Label>{t('cardRewards.field.rewardAccount')}</Label>
                    <Select value={payoutAccountId} onValueChange={setPayoutAccountId}>
                      <SelectTrigger>
                        <SelectValue placeholder={t('cardRewards.field.rewardAccountPlaceholder')} />
                      </SelectTrigger>
                      <SelectContent>
                        {accounts
                          .filter((a) => a.account_type !== 'account_group')
                          .map((a) => (
                            <SelectItem key={a.id} value={a.id}>
                              {a.id === accountId
                                ? `${a.name}${t('cardRewards.field.rewardAccountSelfSuffix')}`
                                : a.name}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label>{t('cardRewards.detail.manualPayoutAmount')}</Label>
                    <Input
                      type="number"
                      inputMode="decimal"
                      value={payoutAmount}
                      onChange={(e) => setPayoutAmount(e.target.value)}
                    />
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    className="rounded-md border border-border px-2.5 py-1 text-xs"
                    onClick={() => setPayoutOpen(false)}
                  >
                    {t('dialog.cancel')}
                  </button>
                  <button
                    type="button"
                    disabled={payoutSubmitting || !(Number(payoutAmount) > 0) || !payoutAccountId}
                    className="rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50"
                    onClick={() => void handleManualPayout()}
                  >
                    {t('cardRewards.dialog.submit')}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : null}
        {loading ? (
          <div className="text-xs text-muted-foreground">{t('cardRewards.loading')}</div>
        ) : fetchError ? (
          <div className="space-y-2 text-xs text-muted-foreground">
            <div>{t('cardRewards.detail.loadFailed')}</div>
            <button type="button" className="underline underline-offset-2" onClick={load}>
              {t('cardRewards.detail.retry')}
            </button>
          </div>
        ) : !detail || detail.status !== 'ok' ? (
          <div className="text-xs text-muted-foreground">
            {detail?.status === 'expired'
              ? t('cardRewards.status.expired')
              : t('cardRewards.status.noBillingSchedule')}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between rounded-md bg-muted/40 px-3 py-2 text-xs">
              <span className="text-muted-foreground">
                {t('cardRewards.detail.capped')}: {fmt(detail.capped_reward)}
              </span>
              <span className="font-medium">
                {detail.remaining_reward_room == null
                  ? t('cardRewards.detail.uncapped')
                  : detail.remaining_reward_room <= 0
                    ? t('cardRewards.detail.capReached')
                    : t('cardRewards.detail.remainingRoom', { amount: fmt(detail.remaining_reward_room) })}
              </span>
            </div>
            <div className="max-h-[50vh] space-y-2 overflow-y-auto">
              {detail.items.length === 0 ? (
                <div className="text-xs text-muted-foreground">{t('cardRewards.detail.empty')}</div>
              ) : (
                detail.items.map((item) => (
                  <div
                    key={item.tx_id}
                    className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-3 py-2 text-xs"
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium">
                        {item.note || item.category_name || t('cardRewards.detail.untitled')}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {isoToDateInput(item.happened_at)} · {fmt(item.amount)}
                        {item.settlement_date
                          ? ` · ${t('cardRewards.detail.settlesOn', { date: isoToDateInput(item.settlement_date) })}`
                          : ''}
                      </div>
                    </div>
                    <div className="shrink-0 font-mono font-semibold tabular-nums text-income">
                      {fmt(item.reward_amount)}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
        <div className="mt-4 flex justify-end">
          <button type="button" className="rounded-md border border-border px-3 py-1.5 text-sm" onClick={onClose}>
            {t('dialog.cancel')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
