import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  deleteInstallmentPlan,
  fetchWorkspaceTags,
  fetchWorkspaceTransactions,
  refundInstallmentPeriod,
  type WorkspaceAccount,
  type WorkspaceCategory,
  type WorkspaceTag,
  type WorkspaceTransaction,
} from '@beecount/api-client'
import { useT, useToast } from '@beecount/ui'
import { ConfirmDialog } from '@beecount/web-features'

import { useAttachmentCache } from '../context/AttachmentCacheContext'
import { useAuth } from '../context/AuthContext'
import { useLedgers } from '../context/LedgersContext'
import { useLedgerWrite } from '../app/useLedgerWrite'
import { localizeError } from '../i18n/errors'
import {
  dispatchOpenEditCategory,
  dispatchOpenEditTx,
  dispatchOpenNewTx,
  onOpenDetailAccount,
  onOpenDetailCategory,
  onOpenDetailTag,
  onOpenDetailTx,
  type DetailScope,
} from '../lib/txDialogEvents'
import { AccountDetailDialog } from './dialogs/AccountDetailDialog'
import { CategoryDetailDialog } from './dialogs/CategoryDetailDialog'
import { InstallmentRefundChoiceDialog } from './dialogs/InstallmentRefundChoiceDialog'
import { TagDetailDialog } from './dialogs/TagDetailDialog'
import { TransactionDetailDialog } from './dialogs/TransactionDetailDialog'

const DETAIL_PAGE_SIZE = 50
// CategoryDetail 的 stats batch 上限。单账本单分类的交易量上限,950 以下场景
// 拉一次就能拿全;超过则在 UI 提示用户精度被截断。1000 是 server 的硬上限,
// 留 50 缓冲避免边界 off-by-one。
const CATEGORY_STATS_LIMIT = 1000

/**
 * 全局实体详情弹窗容器 — 监听 4 类 detail 事件,在 AppShell 顶层渲染对应弹窗。
 *
 * 之前各 *Page 自己管自己的详情弹窗,导致跨页打开(例如 health 页点 sample
 * 想看交易详情)必须先跳到目标页才能渲染,体验割裂。
 *
 * 集中管理后:
 *  - 任意页点 dispatchOpenDetailX(entity) → 弹窗在当前页面就开
 *  - tx detail 是轻量(只展示字段)直接渲染
 *  - account / category / tag 的弹窗内部带交易列表,这里在事件触发时
 *    懒拉对应交易数据
 *  - 详情 → 编辑链路:点编辑按钮 → 跳到对应 page + 派发 openEditX 事件,
 *    page 端的编辑弹窗接管
 */
export function GlobalEntityDialogs() {
  const navigate = useNavigate()
  const t = useT()
  const toast = useToast()
  const { token } = useAuth()
  const { activeLedgerId, currentLedger, currency: activeCurrency } = useLedgers()
  const { previewMap: iconPreviewByFileId } = useAttachmentCache()
  const { retryOnConflict } = useLedgerWrite()

  // 4 个独立 state — 互不影响,可同时打开(不太可能但理论支持)
  const [tx, setTx] = useState<WorkspaceTransaction | null>(null)

  // §2.6:分期交易的退款发起点 —— 先问「只退这一期」还是「整笔退款」。
  // step === 'confirmWhole' 时展示第二个 destructive 二次确认(删整个计划
  // 不可复原,跟本应用其它破坏性分期操作的既有模式一致)。
  const [installmentRefund, setInstallmentRefund] = useState<
    { tx: WorkspaceTransaction; step: 'choice' | 'confirmWhole' } | null
  >(null)
  const [installmentRefundBusy, setInstallmentRefundBusy] = useState(false)

  const [account, setAccount] = useState<WorkspaceAccount | null>(null)
  const [accountScope, setAccountScope] = useState<DetailScope>('current')
  const [accountTxs, setAccountTxs] = useState<WorkspaceTransaction[]>([])
  const [accountTotal, setAccountTotal] = useState(0)
  const [accountOffset, setAccountOffset] = useState(0)
  const [accountLoading, setAccountLoading] = useState(false)

  const [category, setCategory] = useState<WorkspaceCategory | null>(null)
  const [categoryScope, setCategoryScope] = useState<DetailScope>('current')
  const [categoryTxs, setCategoryTxs] = useState<WorkspaceTransaction[]>([])
  const [categoryTotal, setCategoryTotal] = useState(0)
  const [categoryOffset, setCategoryOffset] = useState(0)
  const [categoryLoading, setCategoryLoading] = useState(false)
  // Stats 批量数据 — 跟 categoryScope 联动:current 限当前账本,all 跨账本。
  const [categoryStatsTxs, setCategoryStatsTxs] = useState<WorkspaceTransaction[]>([])
  const [categoryStatsLoading, setCategoryStatsLoading] = useState(false)
  const [categoryStatsTruncated, setCategoryStatsTruncated] = useState(false)

  const [tag, setTag] = useState<WorkspaceTag | null>(null)
  const [tagScope, setTagScope] = useState<DetailScope>('current')
  const [tagTxs, setTagTxs] = useState<WorkspaceTransaction[]>([])
  const [tagTotal, setTagTotal] = useState(0)
  const [tagOffset, setTagOffset] = useState(0)
  const [tagLoading, setTagLoading] = useState(false)

  // 共享 tags 字典 — 4 个详情弹窗里 TransactionList 渲染 tag chip 都要
  const [tagsDict, setTagsDict] = useState<WorkspaceTag[]>([])

  // 监听 tx detail
  useEffect(() => {
    return onOpenDetailTx((next) => {
      setTx(next)
      // 顺手拉一份 tags 字典,详情弹窗里的 tag chip 要按 color 渲染
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 })
          .then(setTagsDict)
          .catch(() => undefined)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const loadAccountTxs = useCallback(
    async (accountName: string, scope: DetailScope, offset: number) => {
      setAccountLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          accountName,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: DETAIL_PAGE_SIZE,
          offset,
        })
        setAccountTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setAccountTotal(page.total)
        setAccountOffset(offset + page.items.length)
      } catch {
        // 静默,弹窗里展示空 list 即可
      } finally {
        setAccountLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 account detail
  useEffect(() => {
    return onOpenDetailAccount((acc, defaultScope) => {
      setAccount(acc)
      setAccountScope(defaultScope)
      setAccountTxs([])
      setAccountTotal(0)
      setAccountOffset(0)
      void loadAccountTxs(acc.name, defaultScope, 0)
      // 同时拉一份 tags 字典(如还没拉)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadAccountTxs, token, tagsDict.length])

  const handleAccountScopeChange = useCallback(
    (next: DetailScope) => {
      if (!account || next === accountScope) return
      setAccountScope(next)
      setAccountTxs([])
      setAccountTotal(0)
      setAccountOffset(0)
      void loadAccountTxs(account.name, next, 0)
    },
    [account, accountScope, loadAccountTxs],
  )

  const loadCategoryTxs = useCallback(
    async (categorySyncId: string, scope: DetailScope, offset: number) => {
      setCategoryLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: DETAIL_PAGE_SIZE,
          offset,
        })
        setCategoryTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setCategoryTotal(page.total)
        setCategoryOffset(offset + page.items.length)
      } catch {
        // ignore
      } finally {
        setCategoryLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  /** 拉一次大批量用于客户端聚合 KPI / 趋势 / Top。scope=current 限定当前账本,
   *  scope=all 跨账本。cap=1000 — 超过显示截断提示,KPI 仍可用但精度下降。 */
  const loadCategoryStats = useCallback(
    async (categorySyncId: string, scope: DetailScope) => {
      setCategoryStatsLoading(true)
      setCategoryStatsTruncated(false)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          categorySyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: CATEGORY_STATS_LIMIT,
          offset: 0,
        })
        setCategoryStatsTxs(page.items)
        setCategoryStatsTruncated(page.total > page.items.length)
      } catch {
        setCategoryStatsTxs([])
      } finally {
        setCategoryStatsLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 category detail
  useEffect(() => {
    return onOpenDetailCategory((cat, defaultScope) => {
      setCategory(cat)
      setCategoryScope(defaultScope)
      setCategoryTxs([])
      setCategoryTotal(0)
      setCategoryOffset(0)
      setCategoryStatsTxs([])
      setCategoryStatsTruncated(false)
      void loadCategoryTxs(cat.id, defaultScope, 0)
      void loadCategoryStats(cat.id, defaultScope)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadCategoryTxs, loadCategoryStats, token, tagsDict.length])

  const handleCategoryScopeChange = useCallback(
    (next: DetailScope) => {
      if (!category || next === categoryScope) return
      setCategoryScope(next)
      setCategoryTxs([])
      setCategoryTotal(0)
      setCategoryOffset(0)
      setCategoryStatsTxs([])
      setCategoryStatsTruncated(false)
      void loadCategoryTxs(category.id, next, 0)
      void loadCategoryStats(category.id, next)
    },
    [category, categoryScope, loadCategoryTxs, loadCategoryStats],
  )

  const loadTagTxs = useCallback(
    async (tagSyncId: string, scope: DetailScope, offset: number) => {
      setTagLoading(true)
      try {
        const page = await fetchWorkspaceTransactions(token, {
          tagSyncId,
          ledgerId: scope === 'current' ? activeLedgerId || undefined : undefined,
          limit: DETAIL_PAGE_SIZE,
          offset,
        })
        setTagTxs((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
        setTagTotal(page.total)
        setTagOffset(offset + page.items.length)
      } catch {
        // ignore
      } finally {
        setTagLoading(false)
      }
    },
    [token, activeLedgerId],
  )

  // 监听 tag detail
  useEffect(() => {
    return onOpenDetailTag((nextTag, defaultScope) => {
      setTag(nextTag)
      setTagScope(defaultScope)
      setTagTxs([])
      setTagTotal(0)
      setTagOffset(0)
      void loadTagTxs(nextTag.id, defaultScope, 0)
      if (tagsDict.length === 0) {
        void fetchWorkspaceTags(token, { limit: 500 }).then(setTagsDict).catch(() => undefined)
      }
    })
  }, [loadTagTxs, token, tagsDict.length])

  const handleTagScopeChange = useCallback(
    (next: DetailScope) => {
      if (!tag || next === tagScope) return
      setTagScope(next)
      setTagTxs([])
      setTagTotal(0)
      setTagOffset(0)
      void loadTagTxs(tag.id, next, 0)
    },
    [tag, tagScope, loadTagTxs],
  )

  // 详情 → 编辑:派发事件到 GlobalEditDialogs(任何页都挂载,事件总能被
  // 接住,无需跳页)。Category 编辑暂时还要 fallback 跳页(分类编辑表单
  // 依赖 inline icon picker,数据流复杂,后续单独全局化)。
  const handleEditTx = useCallback(
    (target: WorkspaceTransaction) => {
      setTx(null)
      dispatchOpenEditTx(target)
    },
    [],
  )

  // §2.12.3:退款发起点 —— 关掉详情,开「新建交易」表单并带上原支出的
  // 金额/备注/分类/账户预填 + refund_of_id 关联,tx_type 强制 income
  // (GlobalEditDialogs 的 onOpenNewTx 监听器负责实际预填逻辑)。
  const handleRefundTx = useCallback(
    (target: WorkspaceTransaction) => {
      setTx(null)
      // §2.6:这笔交易是某个分期计划自动生成的一期 —— 先问用户要「只退这
      // 一期」还是「整笔退款」,不直接走下面的一般退款预填表单(那条路径
      // 不知道分期期数的存在,也不会把该期标记成 refunded)。
      if (target.installment_plan_id) {
        setInstallmentRefund({ tx: target, step: 'choice' })
        return
      }
      dispatchOpenNewTx({
        ledgerId: target.ledger_id,
        refundOf: {
          id: target.id,
          amount: target.amount,
          note: target.note,
          // 原交易的分类是 expense kind,退款交易是 income kind —— 两边分类
          // 树互不相通,直接带名字过去只会指向一个不存在的 income 分类选项,
          // 故意留空让用户自己选。
          categoryName: null,
          accountName: target.account_name,
        },
      })
    },
    [],
  )

  const handleSingleInstallmentPeriodRefund = useCallback(async () => {
    if (!installmentRefund) return
    const target = installmentRefund.tx
    const planId = target.installment_plan_id
    if (!planId) return
    setInstallmentRefundBusy(true)
    try {
      await retryOnConflict(target.ledger_id, (base) =>
        refundInstallmentPeriod(token, target.ledger_id, planId, base, { tx_id: target.id }),
      )
      toast.success(t('installmentPlans.notice.periodRefunded'), t('notice.success'))
      setInstallmentRefund(null)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setInstallmentRefundBusy(false)
    }
  }, [installmentRefund, retryOnConflict, token, toast, t])

  const handleConfirmWholeInstallmentPlanRefund = useCallback(async () => {
    if (!installmentRefund) return
    const target = installmentRefund.tx
    const planId = target.installment_plan_id
    if (!planId) return
    setInstallmentRefundBusy(true)
    try {
      await retryOnConflict(target.ledger_id, (base) =>
        deleteInstallmentPlan(token, target.ledger_id, planId, base),
      )
      toast.success(t('installmentPlans.notice.deleted'), t('notice.success'))
      setInstallmentRefund(null)
    } catch (err) {
      toast.error(localizeError(err, t), t('notice.error'))
    } finally {
      setInstallmentRefundBusy(false)
    }
  }, [installmentRefund, retryOnConflict, token, toast, t])

  const handleEditCategory = useCallback(
    (cat: WorkspaceCategory) => {
      setCategory(null)
      dispatchOpenEditCategory(cat)
    },
    [],
  )

  const handleJumpToTransactions = useCallback(
    (cat: WorkspaceCategory) => {
      const scopeAtJump = categoryScope
      setCategory(null)
      // 跳过去的 TransactionsPage ledger filter 跟当前 scope 对齐:current
      // 时带 ledger=...,all 时不限制账本,避免用户切到「全部账本」却跳进单
      // 账本列表造成迷惑。
      const params = new URLSearchParams()
      params.set('q', cat.name)
      if (scopeAtJump === 'current' && activeLedgerId) {
        params.set('ledger', activeLedgerId)
      }
      navigate(`/app/transactions?${params.toString()}`)
    },
    [navigate, activeLedgerId, categoryScope],
  )

  return (
    <>
      <TransactionDetailDialog
        tx={tx}
        tags={tagsDict}
        onClose={() => setTx(null)}
        onEdit={handleEditTx}
        onRefund={handleRefundTx}
      />
      <InstallmentRefundChoiceDialog
        open={installmentRefund?.step === 'choice'}
        loading={installmentRefundBusy}
        onCancel={() => setInstallmentRefund(null)}
        onChooseSingle={() => void handleSingleInstallmentPeriodRefund()}
        onChooseWhole={() =>
          setInstallmentRefund((prev) => (prev ? { ...prev, step: 'confirmWhole' } : prev))
        }
      />
      <ConfirmDialog
        open={installmentRefund?.step === 'confirmWhole'}
        title={t('installmentPlans.refundChoice.wholeConfirmTitle')}
        description={t('installmentPlans.refundChoice.wholeConfirmDescription')}
        confirmText={t('installmentPlans.refundChoice.whole')}
        cancelText={t('dialog.cancel')}
        loading={installmentRefundBusy}
        onCancel={() => setInstallmentRefund(null)}
        onConfirm={() => void handleConfirmWholeInstallmentPlanRefund()}
      />
      <AccountDetailDialog
        account={account}
        scope={accountScope}
        onScopeChange={handleAccountScopeChange}
        transactions={accountTxs}
        total={accountTotal}
        offset={accountOffset}
        loading={accountLoading}
        tags={tagsDict}
        onClose={() => setAccount(null)}
        onLoadMore={(name, off) => void loadAccountTxs(name, accountScope, off)}
      />
      <CategoryDetailDialog
        category={category}
        scope={categoryScope}
        onScopeChange={handleCategoryScopeChange}
        currency={activeCurrency}
        statsTransactions={categoryStatsTxs}
        statsLoading={categoryStatsLoading}
        statsTruncated={categoryStatsTruncated}
        transactions={categoryTxs}
        total={categoryTotal}
        offset={categoryOffset}
        loading={categoryLoading}
        tags={tagsDict}
        iconPreviewUrlByFileId={iconPreviewByFileId}
        ledgerMonthStartDay={currentLedger?.month_start_day ?? 1}
        onClose={() => setCategory(null)}
        onLoadMore={(syncId, off) => void loadCategoryTxs(syncId, categoryScope, off)}
        onEdit={handleEditCategory}
        onJumpToTransactions={handleJumpToTransactions}
      />
      <TagDetailDialog
        tag={tag}
        scope={tagScope}
        onScopeChange={handleTagScopeChange}
        transactions={tagTxs}
        total={tagTotal}
        offset={tagOffset}
        loading={tagLoading}
        tags={tagsDict}
        tagStatsById={{}}
        onClose={() => setTag(null)}
        onLoadMore={(syncId, off) => void loadTagTxs(syncId, tagScope, off)}
      />
    </>
  )
}
