import { useCallback, useEffect, useState } from 'react'

import type {
  AccountStatement,
  ReadCardRewardQualifyingTx,
  ReadCardRewardRuleTransactions,
  StatementTransaction
} from '@beecount/api-client'
import {
  balanceAdjustment,
  clearStatementConfirmations,
  fetchAccountStatement,
  fetchCardRewardRuleTransactions,
  fetchWorkspaceTransactions,
  updateTransaction
} from '@beecount/api-client'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Input,
  Label,
  useT,
  useToast
} from '@beecount/ui'
import { ConfirmDialog } from '@beecount/web-features'
import { ArrowDownUp, Check, ChevronRight, Clock3, ListChecks, MoreVertical, Pencil, X } from 'lucide-react'

import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { useLedgerWrite } from '../../app/useLedgerWrite'
import { useSyncRefresh } from '../../context/SyncSocketContext'
import { localizeError } from '../../i18n/errors'
import { dispatchOpenEditTx, dispatchOpenNewTx } from '../../lib/txDialogEvents'

type RetryOnConflict = <T,>(ledgerId: string, submit: (baseChangeId: number) => Promise<T>) => Promise<T>

// 跟 CardRewardRulesSection.tsx 的 isoToDateInput/dateInputToIso 同一个理由:
// 后端回傳的是不带时区位移的 naive datetime 字串,`new Date(...)` 缺位移
// 标记时会被当成本地时间解析,UTC+8 使用者会少算一天。缺位移标记时补 "Z"
// 强制当 UTC 解析。
function isoToDateInput(iso: string): string {
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  const d = new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

function dateInputToIso(value: string): string {
  return `${value}T00:00:00+00:00`
}

/** `cycle_end`(YYYY-MM-DD)往後推一天,當「延後入帳」對話框的預設建議值
 *  ——對應 Moze 原文「延後入帳到下期帳單第一天」。 */
function addOneDay(dateOnly: string): string {
  const d = new Date(`${dateOnly.slice(0, 10)}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + 1)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
}

type AccountLike = {
  id: string
  account_type?: string | null
  name: string
  balance?: number | null
}

/**
 * 餘額調整(§2.10 Phase 5)。獨立於下面的 `AccountStatementSection` 掛載
 * ——餘額調整對任何非群組帳戶都有意義(現金/銀行/信用卡皆可),對帳模式
 * (逐筆核對「這期帳單」)只對信用卡/信用卡群組有意義(Moze 原文
 * `doc.moze.app/reconciliation/statement-mode` 的入口本來就限定在「信用卡
 * 交易明細頁」,不是任意帳戶),由 `AccountDetailDialog` 分別用不同條件
 * (`account.account_type !== 'account_group'` vs `billing.canViewBilling`)
 * 決定要不要掛載。不做任何前端角色判斷:餘額調整走一般交易寫權限
 * (owner/editor 都行),寫失敗交給 server 403/404 + toast 提示。
 */
export function BalanceAdjustmentButton({ account }: { account: AccountLike }) {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const [open, setOpen] = useState(false)

  if (!activeLedgerId) return null

  return (
    <>
      <button
        type="button"
        className="rounded-md border border-border px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted/50"
        onClick={() => setOpen(true)}
      >
        {t('balanceAdjustment.button')}
      </button>
      {open ? (
        <BalanceAdjustmentDialog
          token={token || ''}
          ledgerId={activeLedgerId}
          account={account}
          retryOnConflict={retryOnConflict}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </>
  )
}

function BalanceAdjustmentDialog({
  token,
  ledgerId,
  account,
  retryOnConflict,
  onClose
}: {
  token: string
  ledgerId: string
  account: AccountLike
  retryOnConflict: RetryOnConflict
  onClose: () => void
}) {
  const t = useT()
  const toast = useToast()
  const currentBalance = account.balance ?? 0
  const [targetBalance, setTargetBalance] = useState(String(currentBalance))
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const canSubmit = targetBalance.trim().length > 0 && Number.isFinite(Number(targetBalance))

  const handleSubmit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        balanceAdjustment(token, ledgerId, account.id, base, {
          target_balance: Number(targetBalance),
          note: note.trim() || null
        })
      )
      toast.success(t('balanceAdjustment.notice.success'), t('notice.success'))
      onClose()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSubmitting(false)
    }
  }

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>{t('balanceAdjustment.dialog.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>{t('balanceAdjustment.dialog.currentBalance')}</Label>
            <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm font-mono tabular-nums">
              {fmt(currentBalance)}
            </div>
          </div>
          <div className="space-y-1">
            <Label>{t('balanceAdjustment.dialog.targetBalance')}</Label>
            <Input
              type="number"
              inputMode="decimal"
              value={targetBalance}
              onChange={(e) => setTargetBalance(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>{t('balanceAdjustment.dialog.note')}</Label>
            <Input value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
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
            {t('balanceAdjustment.dialog.submit')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 對帳模式(§2.10 Phase 5,2026-08-09 改版):對齊
 * `doc.moze.app/reconciliation/statement-mode` —— 進入對帳模式看到的是
 * 「這期帳單」的交易清單本身(不是輸入一個對帳單餘額數字去比對)。每筆
 * 交易可以勾選確認(對應原文右滑)或延後入帳到下期帳單(對應原文左滑)
 * ——網頁版用點擊按鈕取代滑動手勢。掛在 `AccountDetailDialog` 裡,只在
 * `billing.canViewBilling`(account_group 或沒掛靠群組的獨立信用卡)時
 * 掛載,`billingAccountId`/`cycleOffset` 直接沿用彈窗頂部既有的帳單週期
 * 選擇器狀態(跟 `CardRewardRulesSection` 的 `periodOffset` 同一個「共用
 * 同一份週期狀態,不要自己另外维护一份」教训,見 CLAUDE.md §2.9.5.4)。
 * 選單的「新增繳款」項目故意省略——`CreditCardBillingSection` 已經有一個
 * 更醒目的「繳款」主按鈕在這個區塊正上方,重複做一份會是兩處要同步維護
 * 的重複實作。
 */
export function AccountStatementSection({
  billingAccountId,
  cycleOffset
}: {
  billingAccountId: string
  cycleOffset: number
}) {
  const t = useT()
  const { token } = useAuth()
  const { activeLedgerId } = useLedgers()
  const { retryOnConflict } = useLedgerWrite()
  const toast = useToast()

  // 2026-08-05 使用者反饋:原地展開的清單即使收合也會把下面的交易明細往下
  // 推,改成點標題開一個獨立彈出視窗(跟 CardRewardRulesSection 同款改法)。
  const [dialogOpen, setDialogOpen] = useState(false)
  const [statement, setStatement] = useState<AccountStatement | null>(null)
  const [loading, setLoading] = useState(false)
  const [sortDesc, setSortDesc] = useState(false)
  const [busyTxId, setBusyTxId] = useState<string | null>(null)
  const [postponeTarget, setPostponeTarget] = useState<StatementTransaction | null>(null)
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [rewardDetailTarget, setRewardDetailTarget] = useState<StatementTransaction | null>(null)

  const reload = () => {
    if (!token || !activeLedgerId || !billingAccountId) return
    setLoading(true)
    fetchAccountStatement(token, activeLedgerId, billingAccountId, cycleOffset, sortDesc)
      .then(setStatement)
      .catch(() => setStatement(null))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [billingAccountId, cycleOffset, sortDesc, token, activeLedgerId])

  useEffect(() => {
    setDialogOpen(false)
  }, [billingAccountId])

  // 使用者反饋(2026-08,對帳明細可編輯既有交易):編輯彈窗是全局的
  // (GlobalEditDialogs 監聽 dispatchOpenEditTx),存檔成功後不會直接通知這裡
  // ——跟其它 *Page 同款訂閱 sync_change,寫入落地後自動重新拉一次對帳清單,
  // 不需要另外設計一條 callback 通道。
  useSyncRefresh(reload)

  if (!activeLedgerId) return null

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  const handleEdit = async (tx: StatementTransaction) => {
    if (!token) return
    try {
      const page = await fetchWorkspaceTransactions(token, { ledgerId: activeLedgerId, txSyncId: tx.id, limit: 1 })
      const full = page.items[0]
      if (!full) {
        toast.error(t('statement.error'), t('notice.error'))
        return
      }
      dispatchOpenEditTx(full)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    }
  }

  const toggleConfirmed = async (tx: StatementTransaction) => {
    if (!token) return
    setBusyTxId(tx.id)
    try {
      // 合併後的回饋方案列(需求 #7 改版):確認/取消確認要對這一列包含的
      // 每一筆原始回饋交易(member_tx_ids)各自呼叫既有的 PATCH,不是新增
      // 批次 write endpoint。非合併列時 member_tx_ids 只含自己這一筆。
      const targetIds = tx.member_tx_ids && tx.member_tx_ids.length > 0 ? tx.member_tx_ids : [tx.id]
      const nextReconciledAt = tx.reconciled_at ? null : new Date().toISOString()
      await Promise.all(
        targetIds.map((id) =>
          retryOnConflict(activeLedgerId, (base) =>
            updateTransaction(token, activeLedgerId, id, base, { reconciled_at: nextReconciledAt })
          )
        )
      )
      toast.success(
        tx.reconciled_at ? t('statement.notice.unconfirmed') : t('statement.notice.confirmed'),
        t('notice.success')
      )
      reload()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setBusyTxId(null)
    }
  }

  const handleClear = async () => {
    if (!token) return
    setClearing(true)
    try {
      await retryOnConflict(activeLedgerId, (base) =>
        clearStatementConfirmations(token, activeLedgerId, billingAccountId, base, {
          cycle_offset: cycleOffset
        })
      )
      toast.success(t('statement.notice.cleared'), t('notice.success'))
      setClearConfirmOpen(false)
      reload()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setClearing(false)
    }
  }

  const showAccountName = Boolean(statement && statement.accounts.length > 1)

  return (
    <div className="space-y-3 border-b border-border/60 bg-muted/10 px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-foreground"
          onClick={() => setDialogOpen(true)}
        >
          <ListChecks className="h-3.5 w-3.5" />
          {t('statement.title')}
          {statement ? (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
              {statement.confirmed_count}/{statement.statement_count}
            </span>
          ) : null}
        </button>
      </div>

      {/* 2026-08-05 使用者反饋:對帳清單改到彈出視窗裡顯示,不再原地展開
          （原地展開即使收合仍會把下面的交易明細往下推)。 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="flex max-h-[80vh] max-w-lg flex-col gap-0 p-0">
          <DialogHeader className="flex flex-row items-center justify-between gap-2 border-b border-border/60 px-5 py-3">
            <DialogTitle className="flex items-center gap-1.5 text-base">
              <ListChecks className="h-4 w-4" />
              {t('statement.title')}
              {statement ? (
                <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground">
                  {statement.confirmed_count}/{statement.statement_count}
                </span>
              ) : null}
            </DialogTitle>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  aria-label={t('statement.menu.label')}
                  className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-muted/50"
                >
                  <MoreVertical className="h-3.5 w-3.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() =>
                    dispatchOpenNewTx({
                      happenedAt: statement ? `${statement.cycle_end}T12:00:00+00:00` : undefined,
                      ledgerId: activeLedgerId
                    })
                  }
                >
                  {t('statement.menu.addRecord')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setSortDesc((v) => !v)}>
                  <ArrowDownUp className="mr-2 h-3.5 w-3.5" />
                  {sortDesc ? t('statement.menu.sortOldestFirst') : t('statement.menu.sortNewestFirst')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  disabled={!statement || statement.confirmed_count === 0}
                  onClick={() => setClearConfirmOpen(true)}
                >
                  {t('statement.menu.clearConfirmations')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </DialogHeader>
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
            {loading ? (
              <div className="text-xs text-muted-foreground">{t('cardRewards.loading')}</div>
            ) : !statement ? (
              <div className="text-xs text-muted-foreground">{t('statement.error')}</div>
            ) : (
              <>
                <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                  <SummaryTile label={t('statement.summary.count')} value={String(statement.statement_count)} />
                  <SummaryTile label={t('statement.summary.total')} value={fmt(statement.statement_total)} />
                  <SummaryTile
                    label={t('statement.summary.confirmedCount')}
                    value={String(statement.confirmed_count)}
                    tone="income"
                  />
                  <SummaryTile
                    label={t('statement.summary.confirmedTotal')}
                    value={fmt(statement.confirmed_total)}
                    tone="income"
                  />
                </div>

                {statement.transactions.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border/60 py-4 text-center text-xs text-muted-foreground">
                    {t('statement.empty')}
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {statement.transactions.map((tx) => (
                      <StatementRow
                        key={tx.id}
                        tx={tx}
                        showAccountName={showAccountName}
                        busy={busyTxId === tx.id}
                        fmt={fmt}
                        t={t}
                        onToggleConfirm={() => void toggleConfirmed(tx)}
                        onPostpone={() => setPostponeTarget(tx)}
                        onEdit={() => void handleEdit(tx)}
                        onOpenRewardDetail={tx.reward_rule_id ? () => setRewardDetailTarget(tx) : undefined}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {postponeTarget ? (
        <PostponeDialog
          token={token || ''}
          ledgerId={activeLedgerId}
          tx={postponeTarget}
          suggestedDate={statement ? addOneDay(statement.cycle_end) : null}
          retryOnConflict={retryOnConflict}
          onClose={() => setPostponeTarget(null)}
          onSaved={() => {
            setPostponeTarget(null)
            reload()
          }}
        />
      ) : null}

      {rewardDetailTarget && rewardDetailTarget.reward_rule_id ? (
        <RewardGroupDetailDialog
          token={token || ''}
          ledgerId={activeLedgerId}
          // 回饋規則綁定的是實際刷卡那張子卡(rewardDetailTarget.account_id),
          // 不是這個對帳彈窗查詢用的 billingAccountId(account_group 場景下
          // 可能是群組本身)——後端 card-reward-rules/{id}/transactions 端點
          // 會校驗 account_id 必須等於 rule.account_sync_id,傳群組 id 會
          // 400(rule_id does not belong to this account)。
          accountId={rewardDetailTarget.account_id}
          ruleId={rewardDetailTarget.reward_rule_id}
          ruleLabel={rewardDetailTarget.reward_rule_label || t('statement.row.rewardBadge')}
          periodOffset={cycleOffset - 1}
          retryOnConflict={retryOnConflict}
          onClose={() => setRewardDetailTarget(null)}
          onChanged={reload}
        />
      ) : null}

      <ConfirmDialog
        open={clearConfirmOpen}
        title={t('statement.confirmClear.title')}
        description={t('statement.confirmClear.description')}
        confirmText={t('statement.menu.clearConfirmations')}
        cancelText={t('dialog.cancel')}
        loading={clearing}
        onCancel={() => setClearConfirmOpen(false)}
        onConfirm={() => void handleClear()}
      />
    </div>
  )
}

function SummaryTile({
  label,
  value,
  tone
}: {
  label: string
  value: string
  tone?: 'income'
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`font-mono tabular-nums ${tone === 'income' ? 'font-semibold text-income' : ''}`}>
        {value}
      </div>
    </div>
  )
}

function StatementRow({
  tx,
  showAccountName,
  busy,
  fmt,
  t,
  onToggleConfirm,
  onPostpone,
  onEdit,
  onOpenRewardDetail
}: {
  tx: StatementTransaction
  showAccountName: boolean
  busy: boolean
  fmt: (v: number) => string
  t: (key: string, params?: Record<string, string | number>) => string
  onToggleConfirm: () => void
  onPostpone: () => void
  onEdit: () => void
  onOpenRewardDetail?: () => void
}) {
  const confirmed = Boolean(tx.reconciled_at)
  const postponed = Boolean(tx.deferred_posting_at)
  const isTransfer = tx.tx_type === 'transfer'
  const memberCount = tx.member_tx_ids?.length ?? 1
  // 使用者反饋(2026-08,對帳明細可編輯既有交易):合併後的回饋方案列
  // (memberCount > 1)沒有單一一筆真正的交易可以編輯——金額是這一列底下
  // 好幾筆回饋交易加總出來的,要編輯只能點進明細彈窗逐筆改
  // (RewardGroupDetailDialog)。只有這一列本身就對應唯一一筆真交易時
  // (memberCount <= 1,不管是不是回饋交易)才顯示編輯按鈕。
  const editable = memberCount <= 1
  // 對齊 compute_cycle_period_billing.new_spend 的正負號口徑:expense 為正
  // (增加應繳),income/退款為負(減少應繳)。轉入這張卡(Phase 6,SD 需求
  // #1)比照 income 記為負值(還款/預繳,減少應繳)。
  const signed = tx.tx_type === 'expense' ? tx.amount : -tx.amount

  return (
    <div
      className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${
        confirmed ? 'border-income/40 bg-income/5' : 'border-border/50 bg-background/40'
      }`}
    >
      <button
        type="button"
        disabled={busy}
        aria-pressed={confirmed}
        aria-label={confirmed ? t('statement.action.unconfirm') : t('statement.action.confirm')}
        onClick={onToggleConfirm}
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors disabled:opacity-40 ${
          confirmed
            ? 'border-income bg-income text-income-foreground'
            : 'border-border text-transparent hover:border-income/60'
        }`}
      >
        <Check className="h-3 w-3" />
      </button>
      <div
        className={`min-w-0 flex-1 ${onOpenRewardDetail ? 'cursor-pointer' : ''}`}
        onClick={onOpenRewardDetail}
        role={onOpenRewardDetail ? 'button' : undefined}
      >
        <div className="flex items-center gap-1.5">
          <span className="truncate font-medium">
            {tx.reward_rule_label || tx.category_name || tx.note || t('statement.row.uncategorized')}
          </span>
          {isTransfer ? (
            <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
              {t('statement.row.transferBadge')}
            </span>
          ) : null}
          {tx.is_reward ? (
            <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-income/10 px-1 py-0.5 text-[10px] text-income">
              {t('statement.row.rewardBadge')}
            </span>
          ) : null}
          {onOpenRewardDetail && memberCount > 1 ? (
            <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-income/10 px-1 py-0.5 text-[10px] text-income">
              {t('statement.row.rewardCount', { count: memberCount })}
            </span>
          ) : null}
          {postponed ? (
            <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-muted px-1 py-0.5 text-[10px] text-muted-foreground">
              <Clock3 className="h-2.5 w-2.5" />
              {t('statement.row.postponedBadge')}
            </span>
          ) : null}
          {onOpenRewardDetail ? <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" /> : null}
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {showAccountName && tx.account_name ? `${tx.account_name} · ` : ''}
          {tx.happened_at.slice(0, 10)}
          {tx.note && tx.category_name ? ` · ${tx.note}` : ''}
        </div>
      </div>
      <span className={`shrink-0 font-mono font-semibold tabular-nums ${signed >= 0 ? 'text-expense' : 'text-income'}`}>
        {fmt(Math.abs(signed))}
      </span>
      {editable ? (
        <button
          type="button"
          onClick={onEdit}
          aria-label={t('statement.action.edit')}
          title={t('statement.action.edit')}
          className="shrink-0 rounded-md border border-dashed border-border p-1 text-muted-foreground hover:bg-muted/60"
        >
          <Pencil className="h-3 w-3" />
        </button>
      ) : null}
      <button
        type="button"
        onClick={onPostpone}
        className="shrink-0 rounded-md border border-dashed border-border px-1.5 py-1 text-[10px] text-muted-foreground hover:bg-muted/60"
      >
        {t('statement.action.postpone')}
      </button>
    </div>
  )
}

function PostponeDialog({
  token,
  ledgerId,
  tx,
  suggestedDate,
  retryOnConflict,
  onClose,
  onSaved
}: {
  token: string
  ledgerId: string
  tx: StatementTransaction
  suggestedDate: string | null
  retryOnConflict: RetryOnConflict
  onClose: () => void
  onSaved: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [date, setDate] = useState(
    tx.deferred_posting_at ? isoToDateInput(tx.deferred_posting_at) : suggestedDate || isoToDateInput(tx.happened_at)
  )
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!date) return
    setSubmitting(true)
    try {
      // 合併後的回饋方案列(需求 #7 改版):延後入帳要對這一列包含的每一筆
      // 原始回饋交易各自呼叫既有的 PATCH。非合併列時 member_tx_ids 只含
      // 自己這一筆。
      const targetIds = tx.member_tx_ids && tx.member_tx_ids.length > 0 ? tx.member_tx_ids : [tx.id]
      await Promise.all(
        targetIds.map((id) =>
          retryOnConflict(ledgerId, (base) =>
            updateTransaction(token, ledgerId, id, base, { deferred_posting_at: dateInputToIso(date) })
          )
        )
      )
      toast.success(t('statement.notice.postponed'), t('notice.success'))
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
          <DialogTitle>{t('statement.postpone.dialog.title')}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t('statement.postpone.dialog.hint')}</p>
          <div className="space-y-1">
            <Label>{t('statement.postpone.dialog.date')}</Label>
            <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="rounded-md border border-border px-3 py-1.5 text-sm" onClick={onClose}>
            {t('dialog.cancel')}
          </button>
          <button
            type="button"
            disabled={submitting || !date}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
            onClick={() => void handleSubmit()}
          >
            {t('statement.postpone.dialog.submit')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 使用者反饋(2026-08-XX,需求 #7 改版):對帳明細裡的回饋方案合併列,點擊
 * 才展開看「這個方案這期賺到哪些消費的回饋」——重用既有的
 * `fetchCardRewardRuleTransactions`(§2.9.5.3 交易明細彈窗同一支端點),
 * 不新增後端 endpoint。`periodOffset` 換算跟 `AccountDetailDialog` 同一個
 * 既有慣例:`period_offset = cycleOffset - 1`(對帳的 `cycleOffset` 是「已
 * 結束週期」口徑,回饋規則的 `period_offset=0` 是「still-open 當期」口徑,
 * 兩者定義差一期)。確認/延後入帳操作維持在合併列本身(對這個方案底下所有
 * 回饋交易批次生效),這裡不逐筆做。
 *
 * 2026-08 第二輪使用者反饋(明細可逐筆編輯回饋金額):每一項只有
 * `payout_tx_id` 非空(逐筆結算已到期入帳)才顯示編輯按鈕——`period_end`
 * 整期一次性入帳沒有逐筆對應的真實交易可以改,見 schemas.
 * ReadCardRewardQualifyingTxOut docstring。編輯直接 PATCH 那筆回饋交易的
 * `amount`(既有的 updateTransaction 端點,不需要新的後端 write 邏輯),
 * 存檔後重新拉一次這個彈窗自己的明細 + 呼叫 `onChanged` 讓上層對帳清單也
 * 一起重新整理(合併列的總額要跟著變)。
 */
function RewardGroupDetailDialog({
  token,
  ledgerId,
  accountId,
  ruleId,
  ruleLabel,
  periodOffset,
  retryOnConflict,
  onClose,
  onChanged
}: {
  token: string
  ledgerId: string
  accountId: string
  ruleId: string
  ruleLabel: string
  periodOffset: number
  retryOnConflict: RetryOnConflict
  onClose: () => void
  onChanged: () => void
}) {
  const t = useT()
  const toast = useToast()
  const [detail, setDetail] = useState<ReadCardRewardRuleTransactions | null>(null)
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState(false)
  const [editingTxId, setEditingTxId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)

  const loadDetail = useCallback(() => {
    setLoading(true)
    setFetchError(false)
    return fetchCardRewardRuleTransactions(token, ledgerId, accountId, ruleId, periodOffset)
      .then(setDetail)
      .catch(() => setFetchError(true))
      .finally(() => setLoading(false))
  }, [token, ledgerId, accountId, ruleId, periodOffset])

  useEffect(() => {
    void loadDetail()
  }, [loadDetail])

  const fmt = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })

  const startEdit = (item: ReadCardRewardQualifyingTx) => {
    setEditingTxId(item.tx_id)
    setEditValue(String(item.reward_amount))
  }

  const handleSaveEdit = async (item: ReadCardRewardQualifyingTx) => {
    if (!item.payout_tx_id) return
    const amountNum = Number(editValue)
    if (!Number.isFinite(amountNum) || amountNum < 0) {
      toast.error(t('transactions.error.amountInvalid'), t('notice.error'))
      return
    }
    setSavingEdit(true)
    try {
      await retryOnConflict(ledgerId, (base) =>
        updateTransaction(token, ledgerId, item.payout_tx_id!, base, { amount: amountNum })
      )
      toast.success(t('statement.rewardDetail.notice.updated'), t('notice.success'))
      setEditingTxId(null)
      await loadDetail()
      onChanged()
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setSavingEdit(false)
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="flex max-h-[80vh] max-w-md flex-col gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-5 py-3">
          <DialogTitle className="text-base">{ruleLabel}</DialogTitle>
        </DialogHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="text-xs text-muted-foreground">{t('cardRewards.loading')}</div>
          ) : fetchError || !detail ? (
            <div className="text-xs text-muted-foreground">{t('statement.error')}</div>
          ) : detail.items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border/60 py-4 text-center text-xs text-muted-foreground">
              {t('statement.empty')}
            </div>
          ) : (
            <div className="space-y-1.5">
              {detail.items.map((item) => (
                <div
                  key={item.tx_id}
                  className="flex items-center gap-2 rounded-lg border border-border/50 bg-background/40 px-3 py-2 text-xs"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">
                      {item.category_name || item.note || t('statement.row.uncategorized')}
                    </div>
                    <div className="truncate text-[11px] text-muted-foreground">
                      {item.happened_at.slice(0, 10)} · {fmt(item.amount)}
                    </div>
                  </div>
                  {editingTxId === item.tx_id ? (
                    <div className="flex shrink-0 items-center gap-1">
                      <Input
                        type="number"
                        autoFocus
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        className="h-6 w-20 px-1.5 py-0.5 text-right font-mono text-xs tabular-nums"
                      />
                      <button
                        type="button"
                        disabled={savingEdit}
                        aria-label={t('common.save')}
                        onClick={() => void handleSaveEdit(item)}
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-income hover:bg-income/10 disabled:opacity-40"
                      >
                        <Check className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        disabled={savingEdit}
                        aria-label={t('dialog.cancel')}
                        onClick={() => setEditingTxId(null)}
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted disabled:opacity-40"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ) : (
                    <div className="flex shrink-0 items-center gap-1">
                      <span className="font-mono font-semibold tabular-nums text-income">
                        +{fmt(item.reward_amount)}
                      </span>
                      {item.payout_tx_id ? (
                        <button
                          type="button"
                          aria-label={t('statement.action.edit')}
                          title={t('statement.action.edit')}
                          onClick={() => startEdit(item)}
                          className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted/60"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                      ) : null}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
