import { useEffect, useMemo, useState } from 'react'

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Pagination,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT
} from '@beecount/ui'

import type {
  AttachmentRef,
  ReadAccount,
  ReadCardRewardRule,
  ReadCategory,
  ReadDebt,
  ReadTag,
  ReadTransaction,
  WorkspaceCategory,
} from '@beecount/api-client'

import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { CurrencySelectorTrigger } from '../components/CurrencySelector'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { CategoryIcon } from '../components/CategoryIcon'
import { TagPickerDialog } from '../components/TagPickerDialog'
import { TransactionList } from '../components/TransactionList'
import { tagTextColorOn } from '../lib/tagColorPalette'
import { txSplitItemDefaults, type TxForm } from '../forms'

type TransactionsPanelProps = {
  form: TxForm
  /** 账本本位币(大写 ISO)。币种下拉默认值;选=本位币时 form.currency 存 ''。 */
  baseCurrency?: string
  /** v30 多币种:各币种对 baseCurrency 的汇率(1 quote ≈ x base),透传币种选择弹窗展示。 */
  currencyRates?: Record<string, number>
  rows: ReadTransaction[]
  total: number
  page: number
  pageSize: number
  accounts: ReadAccount[]
  categories: ReadCategory[]
  tags: ReadTag[]
  /** 借還款追蹤(§2.5 體驗補強):主表單掛欠款用,只需要未結案的欠款
   *  (closed/settled 排除,已经没有继续记还款的意义)。 */
  debts?: ReadDebt[]
  /** 借還款追蹤(體驗補強第二輪):是否允許在這個表單直接「建立新欠款」
   *  (而不是只能挂到既有欠款上還款)。新建欠款走 owner-only 的
   *  `POST .../debts`,跟一般記交易的寫權限不是同一條線,所以由呼叫方
   *  按 ledger role === 'owner' 算好再传进来(對齊 DebtsPage.tsx 的
   *  canManage 判斷),不在這個 panel 內部重新猜權限。 */
  canCreateDebt?: boolean
  /** 信用卡紅利回饋(§2.9.5,2026-08-06 改版):目前表單選中帳戶(必須是
   *  credit_card 類型)名下的回饋規則,給「勾選要走哪幾條規則」的多選用。
   *  呼叫方按 `form.account_name` 對應的帳戶單獨拉,非信用卡帳戶传空数组。 */
  rewardRules?: ReadCardRewardRule[]
  /** 表單內直接新增分類/標籤(需求 #10,Phase 11):不傳則分類/標籤選擇彈窗
   *  不顯示「新增 "xxx"」內嵌入口。建立成功後回傳新分類/標籤(用來直接寫回
   *  `form.category_name`/`form.tags`,不必等下一輪 dictionaries 刷新才能
   *  選中它),失敗回傳 null(呼叫方已自行 toast 提示錯誤)。 */
  onCreateCategory?: (name: string, kind: 'expense' | 'income') => Promise<WorkspaceCategory | null>
  onCreateTag?: (name: string) => Promise<{ id: string; name: string } | null>
  ledgerOptions: Array<{ ledger_id: string; ledger_name: string }>
  writeLedgerId: string
  onWriteLedgerIdChange: (ledgerId: string) => void
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  canWrite: boolean
  dictionariesLoading?: boolean
  showCreatorColumn?: boolean
  showLedgerColumn?: boolean
  onFormChange: (next: TxForm) => void
  /** Dialog 显隐由外层控制。新建/编辑入口都靠 parent 在 setOpen(true) 之前
   *  把 form 初始化好,然后 setOpen(true)。这样 page 可以把"新建交易"按钮
   *  跟搜索/筛选放同一行,跟内嵌在 panel 内的 onCreate 解耦。 */
  dialogOpen: boolean
  onDialogOpenChange: (open: boolean) => void
  onSave: () => Promise<boolean> | boolean
  onReset: () => void
  onReload: () => void
  onPreviewAttachment: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  /** 自定义分类图标的预签预览 URL 字典,TransactionList 里每行 CategoryIcon
   *  用来拿 blob URL 显示云端上传的 PNG 图标。material icon 不需要。 */
  iconPreviewUrlByFileId?: Record<string, string>
  onEdit: (row: ReadTransaction) => void
  onDelete: (row: ReadTransaction) => void
  /** 行整体点击 → 打开详情弹窗。如果不传则点行无效果(保留兼容性)。 */
  onSelect?: (row: ReadTransaction) => void
  /** dialogOnly:不渲染交易列表/分页,只挂编辑 Dialog + 内嵌 picker。
   *  让全局 edit 容器(GlobalEditTxDialog)能复用 panel 内置的所有字段渲染 +
   *  picker 联动逻辑,不必从头实现。 */
  dialogOnlyMode?: boolean
  /** 批量选择模式 —— 透传到 TransactionList。 */
  selectionMode?: boolean
  selectedIds?: Set<string>
  onToggleSelect?: (row: ReadTransaction, event: React.MouseEvent) => void
  /** §7 共享账本:开启后 tx 列表行末显示"谁记的"chip。 */
  showCreator?: boolean
  /** §7 共享账本:当前 caller user_id,自己创建+编辑的 tx 不显示 chip。 */
  currentUserId?: string | null
  /** 备注显示方式,透传到 TransactionList。默认 'category'。 */
  noteDisplayMode?: 'category' | 'note'
}

type AttachmentCarouselCellProps = {
  attachments: AttachmentRef[]
  onPreviewAttachment: (
    refs: AttachmentRef[],
    startIndex: number
  ) => Promise<void>
  resolveAttachmentPreviewUrl: (ref: AttachmentRef) => Promise<string | null>
  partialLabel: string
  metadataOnlyLabel: string
  notPreviewableLabel: string
  prevLabel: string
  nextLabel: string
}

function AttachmentCarouselCell({
  attachments,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  partialLabel,
  metadataOnlyLabel,
  notPreviewableLabel,
  prevLabel,
  nextLabel
}: AttachmentCarouselCellProps) {
  const [index, setIndex] = useState(0)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const readyAttachments = attachments.filter(
    (attachment) => typeof attachment.cloudFileId === 'string' && attachment.cloudFileId.trim().length > 0
  )
  const current = readyAttachments[index]

  useEffect(() => {
    if (readyAttachments.length === 0) {
      setIndex(0)
      return
    }
    if (index >= readyAttachments.length) {
      setIndex(0)
    }
  }, [index, readyAttachments.length])

  useEffect(() => {
    let cancelled = false
    if (!current) {
      setPreviewUrl(null)
      setLoading(false)
      return () => {
        cancelled = true
      }
    }
    setLoading(true)
    void resolveAttachmentPreviewUrl(current)
      .then((url) => {
        if (cancelled) return
        setPreviewUrl(url)
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [current, resolveAttachmentPreviewUrl])

  if (attachments.length === 0) return <>-</>

  if (readyAttachments.length === 0) {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="secondary">{attachments.length}</Badge>
        <span className="text-xs text-muted-foreground">{metadataOnlyLabel}</span>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="relative h-24 w-40 overflow-hidden rounded-md border border-border/70 bg-muted/30">
        {previewUrl ? (
          <img
            alt={current?.originalName || current?.fileName || 'attachment-preview'}
            className="h-full w-full cursor-zoom-in object-cover"
            src={previewUrl}
            onClick={() => {
              if (!current) return
              void onPreviewAttachment(readyAttachments, index)
            }}
          />
        ) : (
          <div className="flex h-full items-center justify-center px-2 text-center text-[11px] text-muted-foreground">
            {loading ? '...' : notPreviewableLabel}
          </div>
        )}
        {readyAttachments.length > 1 ? (
          <>
            <Button
              aria-label={prevLabel}
              className="absolute left-1 top-1/2 h-6 w-6 -translate-y-1/2 bg-background/90 p-0"
              size="icon"
              type="button"
              variant="outline"
              onClick={() =>
                setIndex((prev) => (prev - 1 + readyAttachments.length) % readyAttachments.length)
              }
            >
              ‹
            </Button>
            <Button
              aria-label={nextLabel}
              className="absolute right-1 top-1/2 h-6 w-6 -translate-y-1/2 bg-background/90 p-0"
              size="icon"
              type="button"
              variant="outline"
              onClick={() => setIndex((prev) => (prev + 1) % readyAttachments.length)}
            >
              ›
            </Button>
          </>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <Badge variant="default">{attachments.length}</Badge>
        {readyAttachments.length > 0 ? (
          <span className="text-xs text-muted-foreground">
            {Math.min(index + 1, readyAttachments.length)}/{readyAttachments.length}
          </span>
        ) : null}
        {readyAttachments.length < attachments.length ? (
          <span className="text-xs text-muted-foreground">{partialLabel}</span>
        ) : null}
      </div>
    </div>
  )
}

export function TransactionsPanel({
  form,
  baseCurrency = 'CNY',
  currencyRates,
  rows,
  total,
  page,
  pageSize,
  accounts,
  categories,
  tags,
  debts = [],
  canCreateDebt = false,
  rewardRules = [],
  onCreateCategory,
  onCreateTag,
  ledgerOptions,
  writeLedgerId,
  onWriteLedgerIdChange,
  onPageChange,
  onPageSizeChange,
  canWrite,
  dictionariesLoading = false,
  showCreatorColumn = false,
  showLedgerColumn = false,
  onFormChange,
  dialogOpen,
  onDialogOpenChange,
  onSave,
  onReset,
  onReload,
  onPreviewAttachment,
  resolveAttachmentPreviewUrl,
  iconPreviewUrlByFileId,
  onEdit,
  onDelete,
  onSelect,
  dialogOnlyMode,
  selectionMode = false,
  selectedIds,
  onToggleSelect,
  showCreator = false,
  currentUserId,
  noteDisplayMode = 'category'
}: TransactionsPanelProps) {
  const t = useT()
  const open = dialogOpen
  const setOpen = onDialogOpenChange
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [tagPickerOpen, setTagPickerOpen] = useState(false)
  // 拆帳(§2.4):null = 分类 picker 打开时选的是主分类字段;数字 = 选的是
  // splits[index] 那一行,同一个 CategoryPickerDialog 实例按这个分流 onSelect。
  const [splitPickerIndex, setSplitPickerIndex] = useState<number | null>(null)
  // 表單內直接新增分類(需求 #10,Phase 11):建立成功後直接寫回 form,不必
  // 等下一輪 dictionaries 刷新——分流邏輯跟上面既有的 onSelect(splitPickerIndex
  // 非 null 时写 splits[index],否则写主 category_name)完全一致。
  const handleCreateCategory = onCreateCategory
    ? async (name: string) => {
        const created = await onCreateCategory(name, form.tx_type === 'income' ? 'income' : 'expense')
        if (!created) return
        if (splitPickerIndex !== null) {
          const next = form.splits.slice()
          const idx = splitPickerIndex
          if (next[idx]) {
            next[idx] = { ...next[idx], category_id: created.id, category_name: created.name.trim() }
            onFormChange({ ...form, splits: next })
          }
          return
        }
        onFormChange({ ...form, category_name: created.name.trim(), category_kind: form.tx_type })
      }
    : undefined
  // 表單內直接新增標籤(需求 #10,Phase 11):建立成功後直接勾選進
  // `form.tags`,避免同名重複勾选。
  const handleCreateTag = onCreateTag
    ? async (name: string) => {
        const created = await onCreateTag(name)
        if (!created) return
        const trimmed = created.name.trim()
        if (form.tags.some((tagName) => tagName.trim().toLowerCase() === trimmed.toLowerCase())) return
        onFormChange({ ...form, tags: [...form.tags, trimmed] })
      }
    : undefined
  const textActionClass =
    'text-sm text-foreground underline-offset-4 hover:text-primary hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'
  const textDangerActionClass =
    'text-sm text-destructive underline-offset-4 hover:text-destructive/90 hover:underline disabled:pointer-events-none disabled:text-muted-foreground disabled:no-underline'

  // 账户选择彈窗(需求 #9,Phase 11):候选行排除已隐藏帳戶 —— 不能再往它記
  // 新帳,唯一例外是這三個欄位裡目前正選著的那個隱藏帳戶(编辑一笔本就挂在
  // 某隐藏账户上的历史交易时钉住显示,带「已隐藏」灰标让用户可原样保存)。
  // account_group(純管理容器)本身不可被選中,但仍保留在候選清單裡 ——
  // AccountPickerDialog 需要它來渲染子帳戶的巢狀父列,選中判斷交給該元件
  // 內部處理(點擊 account_group 列不觸發 onSelect)。
  const pickerAccountRows = useMemo(() => {
    const pinned = new Set(
      [form.account_name, form.from_account_name, form.to_account_name]
        .map((name) => name.trim().toLowerCase())
        .filter((name) => name.length > 0)
    )
    return accounts.filter((row) => !row.hidden || pinned.has(row.name.trim().toLowerCase()))
  }, [accounts, form.account_name, form.from_account_name, form.to_account_name])
  const [accountPickerOpen, setAccountPickerOpen] = useState(false)
  const [fromAccountPickerOpen, setFromAccountPickerOpen] = useState(false)
  const [toAccountPickerOpen, setToAccountPickerOpen] = useState(false)
  const categoryOptions = categories
    .filter((row) => row.kind === form.tx_type)
    .map((row) => row.name.trim())
    .filter((name) => name.length > 0)
    .filter((name, index, self) => self.indexOf(name) === index)
    .sort((a, b) => a.localeCompare(b))
  // tag 选择改用 TagPickerDialog,组件自己做 dedup + 搜索 + chip 渲染,这里
  // 不再需要 tagOptions 派生(只保留 tagColorByName 给 trigger 按钮的小 chip
  // 渲染上色用)。
  // 按 name 反查 tag 颜色，tx 列表行里给每个标签 badge 上色。大小写不敏感。
  const tagColorByName = new Map<string, string>()
  for (const row of tags) {
    const key = (row.name || '').trim().toLowerCase()
    if (!key) continue
    if (row.color && !tagColorByName.has(key)) tagColorByName.set(key, row.color)
  }

  // 当前选中的分类 row(按 name + kind 反查),给 CategoryPicker 高亮 + 触发
  // 按钮显示图标。和 TransactionList 行内渲染保持同源。
  const selectedCategoryRow = useMemo<WorkspaceCategory | null>(() => {
    const name = (form.category_name || '').trim().toLowerCase()
    if (!name) return null
    return (
      (categories as WorkspaceCategory[]).find(
        (row) =>
          row.kind === form.tx_type &&
          (row.name || '').trim().toLowerCase() === name,
      ) ?? null
    )
  }, [categories, form.category_name, form.tx_type])

  const isTransfer = form.tx_type === 'transfer'
  // 非转账允许不选账户（与 mobile 保持一致，tx.accountId 本来就是 nullable）；
  // 转账必须两端都选（否则无法表达方向）。
  const canSubmit = Boolean(writeLedgerId.trim()) && (isTransfer
    ? Boolean(form.from_account_name.trim()) && Boolean(form.to_account_name.trim())
    : true)
  const selectedTags = form.tags
  const categoryValue = form.category_name.trim()
  // 拆帳(§2.4):已填金额的行加总,给"已分配 X / 总额 Y"提示用;跟
  // forms.ts::validateTxSplits 的加总逻辑口径一致(未选分类的占位行不计入)。
  const splitAssignedTotal = form.splits
    .filter((row) => row.category_id.trim().length > 0)
    .reduce((acc, row) => acc + (Number(row.amount) || 0), 0)

  const applyTxType = (nextType: TxForm['tx_type']) => {
    if (nextType === 'transfer') {
      // 转账两个标记都隐藏 → 清掉,避免残留脏值。
      // currency 一并清空:转账不支持跨币种且币种控件隐藏,不清会把转出/
      // 转入账户下拉锁死在之前手选的外币过滤里(审查发现)。
      onFormChange({
        ...form,
        tx_type: nextType,
        account_name: '',
        currency: '',
        category_name: '',
        category_kind: 'transfer',
        exclude_from_stats: false,
        exclude_from_budget: false,
        // 分期只对 expense 有意义,切走时关掉(Phase 1.5 §2.12.1)。
        installment_enabled: false,
        // 拆帳(§2.4)只对 expense/income 有意义(跟 category 同语意),
        // 切到 transfer 时关掉 + 清空明细。
        split_enabled: false,
        splits: []
      })
      return
    }
    const keepCategory = form.category_kind === nextType ? form.category_name : ''
    // 分类分 expense/income 两棵树,切类型时旧的 splits 分类都不再有效,
    // 跟单一 category 字段(keepCategory)同样清掉重选。
    const keepSplits = form.category_kind === nextType ? form.splits : []
    onFormChange({
      ...form,
      tx_type: nextType,
      category_kind: nextType,
      category_name: keepCategory,
      splits: keepSplits,
      from_account_name: '',
      to_account_name: '',
      // 不计入预算仅 expense 显示;切到 income 时清掉
      exclude_from_budget: nextType === 'expense' ? form.exclude_from_budget : false,
      installment_enabled: nextType === 'expense' ? form.installment_enabled : false
    })
  }

  const colCount = 8 + (showCreatorColumn ? 1 : 0) + (showLedgerColumn ? 1 : 0)

  return (
    <>
      {/* dialogOnlyMode: 全局编辑容器复用本 panel 的 Dialog + picker 联动,
          不渲染列表 / 分页。 */}
      {dialogOnlyMode ? null : (
        <div className="rounded-xl border border-border/50 bg-card">
          <TransactionList
            items={rows}
            tags={tags}
            categories={categories}
            iconPreviewUrlByFileId={iconPreviewUrlByFileId}
            variant="default"
            showCreator={showCreator}
            currentUserId={currentUserId}
            noteDisplayMode={noteDisplayMode}
            canManage={canWrite}
            onEdit={(row) => {
              onEdit(row)
              setOpen(true)
            }}
            onDelete={onDelete}
            onSelect={onSelect}
            onPreviewAttachment={onPreviewAttachment}
            resolveAttachmentPreviewUrl={resolveAttachmentPreviewUrl}
            selectionMode={selectionMode}
            selectedIds={selectedIds}
            onToggleSelect={onToggleSelect}
          />
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border/60 px-6 py-4">
            <DialogTitle>{form.editingId ? t('transactions.button.update') : t('transactions.button.create')}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
              <Label>{t('shell.ledger')}</Label>
              <Select value={writeLedgerId || undefined} onValueChange={onWriteLedgerIdChange} disabled={Boolean(form.editingId)}>
                <SelectTrigger>
                  <SelectValue placeholder={t('shell.ledger')} />
                </SelectTrigger>
                <SelectContent>
                  {ledgerOptions.map((ledger) => (
                    <SelectItem key={ledger.ledger_id} value={ledger.ledger_id}>
                      {ledger.ledger_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.type')}</Label>
              <Select value={form.tx_type} onValueChange={(value) => applyTxType(value as TxForm['tx_type'])}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="expense">{t('enum.txType.expense')}</SelectItem>
                  <SelectItem value="income">{t('enum.txType.income')}</SelectItem>
                  <SelectItem value="transfer">{t('enum.txType.transfer')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.amount')}</Label>
              <Input
                placeholder={t('transactions.placeholder.amount')}
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
              {/* v30 多币种:币种另起一行,全宽显示币种全名+国旗(挨金额太窄会截断);
                  选非本位币 → 账户下拉按币种过滤 + 已选账户清空(币种优先联动,
                  transfer 不支持)。 */}
              {form.tx_type !== 'transfer' ? (
                <CurrencySelectorTrigger
                  value={form.currency || baseCurrency}
                  onChange={(code) =>
                    onFormChange({
                      ...form,
                      currency:
                        code.toUpperCase() === baseCurrency.toUpperCase()
                          ? ''
                          : code,
                      account_name: ''
                    })
                  }
                  ratesToBase={currencyRates}
                  rateBase={baseCurrency}
                />
              ) : null}
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.time')}</Label>
              <Input
                type="datetime-local"
                step={60}
                value={isoToDatetimeLocal(form.happened_at)}
                onChange={(e) =>
                  onFormChange({
                    ...form,
                    happened_at: datetimeLocalToIso(e.target.value, form.happened_at)
                  })
                }
              />
            </div>
            <div className={form.split_enabled && !isTransfer ? 'space-y-1 md:col-span-2' : 'space-y-1'}>
              <div className="flex items-center justify-between">
                <Label>{t('transactions.table.category')}</Label>
                {/* 拆帳(§2.4):只对 expense/income 有意义,跟 transfer 互斥。
                    编辑既有交易时也能开关(对齐 Moze 编辑既有交易能改分类的语意),
                    不像 recurring/installment 只在新建时显示。 */}
                {!isTransfer ? (
                  <button
                    type="button"
                    className={textActionClass}
                    onClick={() => {
                      if (form.split_enabled) {
                        onFormChange({ ...form, split_enabled: false, splits: [] })
                      } else {
                        onFormChange({
                          ...form,
                          split_enabled: true,
                          category_name: '',
                          splits:
                            form.splits.length >= 2
                              ? form.splits
                              : [txSplitItemDefaults(), txSplitItemDefaults()]
                        })
                      }
                    }}
                  >
                    {form.split_enabled
                      ? t('transactions.split.disable')
                      : t('transactions.split.enable')}
                  </button>
                ) : null}
              </div>
              {isTransfer ? (
                <Input disabled value={t('common.none')} />
              ) : form.split_enabled ? (
                <div className="space-y-2 rounded-lg border border-border/60 bg-muted/10 p-3">
                  {form.splits.map((row, idx) => (
                    <div key={idx} className="flex items-center gap-2">
                      <button
                        type="button"
                        disabled={dictionariesLoading}
                        onClick={() => {
                          setSplitPickerIndex(idx)
                          setCategoryPickerOpen(true)
                        }}
                        className="flex h-9 flex-1 items-center gap-2 rounded-md border border-input bg-muted px-3 py-1.5 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span
                          className={`flex-1 truncate ${
                            row.category_name ? '' : 'text-muted-foreground'
                          }`}
                        >
                          {row.category_name || t('transactions.placeholder.categoryName')}
                        </span>
                      </button>
                      <Input
                        className="h-9 w-28"
                        placeholder={t('transactions.table.amount')}
                        value={row.amount}
                        onChange={(e) => {
                          const next = form.splits.slice()
                          next[idx] = { ...next[idx], amount: e.target.value }
                          onFormChange({ ...form, splits: next })
                        }}
                      />
                      <button
                        type="button"
                        aria-label={t('transactions.split.removeRow') as string}
                        onClick={() =>
                          onFormChange({
                            ...form,
                            splits: form.splits.filter((_, i) => i !== idx)
                          })
                        }
                        className="shrink-0 px-1 text-sm text-muted-foreground hover:text-destructive"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className={textActionClass}
                    onClick={() =>
                      onFormChange({ ...form, splits: [...form.splits, txSplitItemDefaults()] })
                    }
                  >
                    + {t('transactions.split.addRow')}
                  </button>
                  <p
                    className={`text-xs ${
                      Math.abs(splitAssignedTotal - (Number(form.amount) || 0)) > 0.01
                        ? 'text-destructive'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {t('transactions.split.sumHint', {
                      sum: splitAssignedTotal,
                      total: Number(form.amount) || 0
                    })}
                  </p>
                </div>
              ) : (
                // 跟同行的 SelectTrigger 视觉对齐:h-10 + bg-muted + border-input,
                // 图标用 h-6 w-6 圆形塞得进 40px 高度,不撑大行高。
                <button
                  type="button"
                  disabled={dictionariesLoading}
                  onClick={() => {
                    setSplitPickerIndex(null)
                    setCategoryPickerOpen(true)
                  }}
                  className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {selectedCategoryRow ? (
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/15">
                      <CategoryIcon
                        icon={selectedCategoryRow.icon}
                        iconType={selectedCategoryRow.icon_type}
                        iconCloudFileId={selectedCategoryRow.icon_cloud_file_id}
                        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                        size={16}
                        className="text-primary"
                      />
                    </span>
                  ) : null}
                  <span
                    className={`flex-1 truncate ${
                      categoryValue ? '' : 'text-muted-foreground'
                    }`}
                  >
                    {categoryValue || t('transactions.placeholder.categoryName')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              )}
            </div>

            {isTransfer ? (
              <>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.fromAccountName')}</Label>
                  <button
                    type="button"
                    disabled={dictionariesLoading}
                    onClick={() => setFromAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className={`flex-1 truncate ${form.from_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.from_account_name || t('transactions.placeholder.fromAccountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
                <div className="space-y-1">
                  <Label>{t('transactions.placeholder.toAccountName')}</Label>
                  <button
                    type="button"
                    disabled={dictionariesLoading}
                    onClick={() => setToAccountPickerOpen(true)}
                    className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className={`flex-1 truncate ${form.to_account_name ? '' : 'text-muted-foreground'}`}>
                      {form.to_account_name || t('transactions.placeholder.toAccountName')}
                    </span>
                    <span className="text-xs text-muted-foreground opacity-60">▾</span>
                  </button>
                </div>
              </>
            ) : (
              <div className="space-y-1">
                <Label>{t('transactions.table.account')}</Label>
                <button
                  type="button"
                  disabled={dictionariesLoading}
                  onClick={() => setAccountPickerOpen(true)}
                  className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className={`flex-1 truncate ${form.account_name ? '' : 'text-muted-foreground'}`}>
                    {form.account_name || t('transactions.placeholder.accountName')}
                  </span>
                  <span className="text-xs text-muted-foreground opacity-60">▾</span>
                </button>
              </div>
            )}

            {/* 商店 + 備註(需求 #11,Phase 11):商店選填,純展示用途;備註從
                原本緊鄰「實際入帳日」的位置往上移到帳戶/分類附近,兩者相鄰
                放置,對齊使用者反饋的欄位順序需求。 */}
            <div className="space-y-1">
              <Label>{t('transactions.field.merchant')}</Label>
              <Input
                placeholder={t('transactions.placeholder.merchant')}
                value={form.merchant}
                onChange={(e) => onFormChange({ ...form, merchant: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                placeholder={t('transactions.placeholder.note')}
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            {/* 借還款追蹤(§2.5 體驗補強):expense/income 都能掛欠款(跟退款
                同样排除 transfer),不強制連動 tx_type,使用者自己判斷要記
                支出還收入。只列未結案的欠款(closed/settled 已经没有继续
                记还款的意义)。 */}
            {!isTransfer ? (
              <div className="space-y-1">
                <Label>{t('transactions.field.debt')}</Label>
                <Select
                  value={form.debt_id ? form.debt_id : '__none__'}
                  disabled={dictionariesLoading}
                  onValueChange={(value) =>
                    onFormChange({ ...form, debt_id: value === '__none__' ? '' : value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('transactions.placeholder.debt')} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__none__">
                      <span className="text-muted-foreground">
                        {t('transactions.placeholder.debt')}
                      </span>
                    </SelectItem>
                    {canCreateDebt ? (
                      <SelectItem value="__new__">
                        {t('transactions.placeholder.newDebt')}
                      </SelectItem>
                    ) : null}
                    {debts
                      .filter((d) => d.status !== 'closed' && d.status !== 'settled')
                      .map((d) => (
                        <SelectItem key={d.id} value={d.id}>
                          {d.counterparty_name}
                          <span className="ml-1 text-xs text-muted-foreground">
                            {t(`debts.direction.${d.direction}`)}
                          </span>
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                {/* 「建立新欠款」(體驗補強第二輪):这笔交易本身是欠款起点,
                    不是还款 —— 只补对方名字/到期日,direction 由 tx_type
                    自动推算并展示提示,不让用户重复选一遍(收入=借入=我欠
                    对方,支出=借出=对方欠我)。 */}
                {form.debt_id === '__new__' ? (
                  <div className="mt-2 space-y-2 rounded-md border border-dashed border-input p-2">
                    <p className="text-xs text-muted-foreground">
                      {t(
                        form.tx_type === 'income'
                          ? 'transactions.hint.newDebtDirection.payable'
                          : 'transactions.hint.newDebtDirection.receivable'
                      )}
                    </p>
                    <div className="space-y-1">
                      <Label>{t('transactions.field.newDebtCounterparty')}</Label>
                      <Input
                        value={form.new_debt_counterparty_name}
                        placeholder={t('transactions.placeholder.newDebtCounterparty')}
                        onChange={(e) =>
                          onFormChange({ ...form, new_debt_counterparty_name: e.target.value })
                        }
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('transactions.field.newDebtDueAt')}</Label>
                      <Input
                        type="date"
                        value={form.new_debt_due_at}
                        onChange={(e) =>
                          onFormChange({ ...form, new_debt_due_at: e.target.value })
                        }
                      />
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者記交易時手動
                勾選這筆消費要走哪幾條回饋規則(可複選)——系統不再依 category/
                金額自動判斷,只在 expense + 選中帳戶是信用卡時顯示。
                §2.9.5.4 補強:只顯示規則生效中的選項。
                2026-08-07 使用者反饋:原本拿「現在的時間(Date.now())」跟
                starts_at/ends_at 比,而不是拿「這筆交易自己的時間」比——結果
                補記一筆已過期活動期間的舊交易時,清單只看記錄當下規則還在
                不在,過期規則會一路留在清單裡讓使用者誤勾,越積越多。改成
                拿 `form.happened_at`(這筆交易的時間,使用者改時間欄位時
                即時重新過濾)去跟規則的生效窗比對,不在窗內就不出現在候選
                清單——但如果這條規則已經被這筆交易勾選過,即使已經不在窗內
                也要保留顯示,讓使用者可以取消勾選(跟已停用規則若曾被勾選
                仍要顯示同一個既有慣例)。 */}
            {form.tx_type === 'expense' &&
            accounts.find(
              (a) => a.name.trim().toLowerCase() === form.account_name.trim().toLowerCase()
            )?.account_type === 'credit_card' &&
            rewardRules.length > 0 ? (
              <div className="space-y-1">
                <Label>{t('transactions.field.rewardRules')}</Label>
                <div className="flex flex-wrap gap-1.5">
                  {rewardRules
                    .filter((rule) => {
                      if (form.reward_rule_ids.includes(rule.id)) return true
                      if (!rule.enabled) return false
                      const txTime = forceUtcTimestamp(form.happened_at)
                      if (rule.starts_at && forceUtcTimestamp(rule.starts_at) > txTime) return false
                      if (rule.ends_at && forceUtcTimestamp(rule.ends_at) < txTime) return false
                      return true
                    })
                    .map((rule) => {
                    const checked = form.reward_rule_ids.includes(rule.id)
                    return (
                      <button
                        key={rule.id}
                        type="button"
                        onClick={() =>
                          onFormChange({
                            ...form,
                            reward_rule_ids: checked
                              ? form.reward_rule_ids.filter((id) => id !== rule.id)
                              : [...form.reward_rule_ids, rule.id]
                          })
                        }
                        className={`rounded-full border px-2.5 py-1 text-xs ${
                          checked
                            ? 'border-primary bg-primary/15 text-primary'
                            : 'border-input text-muted-foreground hover:bg-accent/40'
                        }`}
                      >
                        {rule.label}
                        {!rule.enabled ? (
                          <span className="ml-1 text-[10px] text-muted-foreground">
                            {t('cardRewards.disabled')}
                          </span>
                        ) : null}
                      </button>
                    )
                  })}
                </div>
              </div>
            ) : null}

            <div className="space-y-1">
              <Label>{t('tags.title')}</Label>
              {/* tag 多选改用 TagPickerDialog —— mobile 风格的 chip 选择,带搜索
                  + 颜色块,比 DropdownMenu 直观。trigger 按钮里把已选标签缩略
                  显示成彩色 chip,空时占位文案。视觉上跟同行的 SelectTrigger 同高。 */}
              <button
                type="button"
                disabled={dictionariesLoading}
                onClick={() => setTagPickerOpen(true)}
                className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="flex flex-1 items-center gap-1 overflow-hidden">
                  {selectedTags.length === 0 ? (
                    <span className="text-muted-foreground">
                      {t('common.none')}
                    </span>
                  ) : (
                    <span className="flex flex-wrap items-center gap-1 overflow-hidden">
                      {selectedTags.slice(0, 3).map((name) => {
                        const color = tagColorByName.get(name.toLowerCase()) || '#94a3b8'
                        const fg = tagTextColorOn(color)
                        return (
                          <span
                            key={name}
                            className="inline-flex h-5 max-w-[120px] items-center rounded-full px-1.5 text-[11px] leading-none"
                            style={{ background: color, color: fg }}
                            title={name}
                          >
                            <span className="truncate">{name}</span>
                          </span>
                        )
                      })}
                      {selectedTags.length > 3 ? (
                        <span className="text-[11px] text-muted-foreground">
                          +{selectedTags.length - 3}
                        </span>
                      ) : null}
                    </span>
                  )}
                </span>
                <span className="text-xs text-muted-foreground opacity-60">▾</span>
              </button>
            </div>
            {/* 退款(§2.6 / §2.12.3):發起入口已搬到交易明細頁的「退款」按鈕
                (TransactionDetailDialog),這裡不再提供手動挑選退款對象的
                下拉——編輯既有退款交易時 `form.refund_of_id` 原樣保留,只是
                沒有 UI 能再手動改它,對齊 Moze「退款關聯只能從發起流程建立」
                的設計。 */}

            {/* 延後入帳(§2.10 Phase 5):進階/選填欄位,不常用 —— 只在消費日
                跟實際入帳日不同時填(比如信用卡商戶延遲請款),用於對帳 /
                信用卡帳單週期歸屬。刻意放在筆記欄位旁,不做成顯眼的必填
                欄位。 */}
            <div className="space-y-1">
              <Label>{t('tx.form.deferredPostingAt.label')}</Label>
              <Input
                type="date"
                value={form.deferred_posting_at}
                onChange={(e) => onFormChange({ ...form, deferred_posting_at: e.target.value })}
              />
              <div className="text-[11px] text-muted-foreground">
                {t('tx.form.deferredPostingAt.hint')}
              </div>
            </div>
            {/* Phase 1.5(§2.12.1/§2.12.2):建交易当下顺便设成週期性收支/
                分期付款起点。只在新建(非编辑)时显示,两者互斥(挂在同一笔
                amount/happened_at 上,语意冲突);分期只对 expense 有意义。 */}
            {!form.editingId ? (
              <div className="space-y-1 md:col-span-2">
                <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                  <p className="text-sm font-medium">{t('transactions.toggle.recurring')}</p>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={form.recurring_enabled}
                    aria-label={t('transactions.toggle.recurring') as string}
                    onClick={() =>
                      onFormChange({
                        ...form,
                        recurring_enabled: !form.recurring_enabled,
                        installment_enabled: false
                      })
                    }
                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                      form.recurring_enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                        form.recurring_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                      }`}
                    />
                  </button>
                </div>
                {form.recurring_enabled ? (
                  <div className="grid gap-3 rounded-lg border border-border/60 bg-muted/10 p-3 md:grid-cols-2">
                    <div className="space-y-1">
                      <Label>{t('recurringRules.field.frequency')}</Label>
                      <Select
                        value={form.recurring_frequency}
                        onValueChange={(value) =>
                          onFormChange({ ...form, recurring_frequency: value as TxForm['recurring_frequency'] })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="daily">{t('recurringRules.frequency.daily')}</SelectItem>
                          <SelectItem value="weekly">{t('recurringRules.frequency.weekly')}</SelectItem>
                          <SelectItem value="monthly">{t('recurringRules.frequency.monthly')}</SelectItem>
                          <SelectItem value="yearly">{t('recurringRules.frequency.yearly')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>{t('recurringRules.field.interval')}</Label>
                      <Input
                        type="number"
                        min={1}
                        max={365}
                        value={form.recurring_interval}
                        onChange={(e) => onFormChange({ ...form, recurring_interval: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('recurringRules.field.endAt')}</Label>
                      <Input
                        type="datetime-local"
                        value={form.recurring_end_at}
                        onChange={(e) => onFormChange({ ...form, recurring_end_at: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('recurringRules.field.advancedMode')}</Label>
                      <Select
                        value={form.recurring_advanced_mode}
                        onValueChange={(value) =>
                          onFormChange({ ...form, recurring_advanced_mode: value as TxForm['recurring_advanced_mode'] })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="none">{t('recurringRules.advancedMode.none')}</SelectItem>
                          <SelectItem value="weekly_days">{t('recurringRules.advancedMode.weeklyDays')}</SelectItem>
                          <SelectItem value="monthly_day">{t('recurringRules.advancedMode.monthlyDay')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    {form.recurring_advanced_mode === 'weekly_days' ? (
                      <div className="space-y-1 md:col-span-2">
                        <Label>{t('recurringRules.field.weeklyDays')}</Label>
                        <div className="flex flex-wrap gap-1.5">
                          {(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const).map((key, index) => {
                            const selected = form.recurring_weekly_days.includes(index)
                            return (
                              <button
                                key={key}
                                type="button"
                                onClick={() =>
                                  onFormChange({
                                    ...form,
                                    recurring_weekly_days: selected
                                      ? form.recurring_weekly_days.filter((d) => d !== index)
                                      : [...form.recurring_weekly_days, index].sort()
                                  })
                                }
                                className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm transition-colors ${
                                  selected
                                    ? 'border-primary bg-primary text-primary-foreground'
                                    : 'border-border/60 bg-background text-muted-foreground hover:bg-accent/40'
                                }`}
                              >
                                {t(`recurringRules.weekday.${key}`)}
                              </button>
                            )
                          })}
                        </div>
                      </div>
                    ) : null}
                    {form.recurring_advanced_mode === 'monthly_day' ? (
                      <div className="space-y-1">
                        <Label>{t('recurringRules.field.monthlyDay')}</Label>
                        <Input
                          type="number"
                          min={1}
                          max={31}
                          value={form.recurring_monthly_day}
                          onChange={(e) => onFormChange({ ...form, recurring_monthly_day: e.target.value })}
                        />
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}

            {!form.editingId && form.tx_type === 'expense' ? (
              <div className="space-y-1 md:col-span-2">
                <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2">
                  <p className="text-sm font-medium">{t('transactions.toggle.installment')}</p>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={form.installment_enabled}
                    aria-label={t('transactions.toggle.installment') as string}
                    onClick={() =>
                      onFormChange({
                        ...form,
                        installment_enabled: !form.installment_enabled,
                        recurring_enabled: false
                      })
                    }
                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                      form.installment_enabled ? 'bg-primary' : 'bg-muted-foreground/30'
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                        form.installment_enabled ? 'translate-x-[18px]' : 'translate-x-0.5'
                      }`}
                    />
                  </button>
                </div>
                {form.installment_enabled ? (
                  <div className="grid gap-3 rounded-lg border border-border/60 bg-muted/10 p-3 md:grid-cols-2">
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.periods')}</Label>
                      <Input
                        type="number"
                        min={1}
                        max={600}
                        value={form.installment_periods}
                        onChange={(e) => onFormChange({ ...form, installment_periods: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.repaymentMethod')}</Label>
                      <Select
                        value={form.installment_repayment_method}
                        onValueChange={(value) =>
                          onFormChange({
                            ...form,
                            installment_repayment_method: value as TxForm['installment_repayment_method']
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="equal_principal">
                            {t('installmentPlans.repaymentMethod.equalPrincipal')}
                          </SelectItem>
                          <SelectItem value="equal_installment">
                            {t('installmentPlans.repaymentMethod.equalInstallment')}
                          </SelectItem>
                          <SelectItem value="fixed_interest">
                            {t('installmentPlans.repaymentMethod.fixedInterest')}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.interestRate')}</Label>
                      <Input
                        type="number"
                        min={0}
                        step="0.001"
                        value={form.installment_interest_rate}
                        onChange={(e) => onFormChange({ ...form, installment_interest_rate: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.interestPeriod')}</Label>
                      <Select
                        value={form.installment_interest_period}
                        onValueChange={(value) =>
                          onFormChange({
                            ...form,
                            installment_interest_period: value as TxForm['installment_interest_period']
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="monthly">{t('installmentPlans.interestPeriod.monthly')}</SelectItem>
                          <SelectItem value="daily">{t('installmentPlans.interestPeriod.daily')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.gracePeriodMonths')}</Label>
                      <Input
                        type="number"
                        min={0}
                        value={form.installment_grace_period_months}
                        onChange={(e) =>
                          onFormChange({ ...form, installment_grace_period_months: e.target.value })
                        }
                      />
                    </div>
                    <div className="space-y-1">
                      <Label>{t('installmentPlans.field.remainderPosition')}</Label>
                      <Select
                        value={form.installment_remainder_position}
                        onValueChange={(value) =>
                          onFormChange({
                            ...form,
                            installment_remainder_position: value as TxForm['installment_remainder_position']
                          })
                        }
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="first">{t('installmentPlans.remainderPosition.first')}</SelectItem>
                          <SelectItem value="last">{t('installmentPlans.remainderPosition.last')}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                      <p className="text-sm font-medium">{t('installmentPlans.field.roundAmounts')}</p>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={form.installment_round_amounts}
                        aria-label={t('installmentPlans.field.roundAmounts') as string}
                        onClick={() =>
                          onFormChange({ ...form, installment_round_amounts: !form.installment_round_amounts })
                        }
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                          form.installment_round_amounts ? 'bg-primary' : 'bg-muted-foreground/30'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                            form.installment_round_amounts ? 'translate-x-[18px]' : 'translate-x-0.5'
                          }`}
                        />
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {/* §三 标记开关 — 按当前 type 条件显示:
                  不计入收支:income / expense(转账本就不进收支,隐藏)
                  不计入预算:仅 expense(预算只统计支出) */}
            {form.tx_type !== 'transfer' ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <p className="text-sm font-medium">{t('txFlagExcludeFromStats')}</p>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.exclude_from_stats}
                  aria-label={t('txFlagExcludeFromStats') as string}
                  onClick={() =>
                    onFormChange({ ...form, exclude_from_stats: !form.exclude_from_stats })
                  }
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.exclude_from_stats ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.exclude_from_stats ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}
            {form.tx_type === 'expense' ? (
              <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
                <p className="text-sm font-medium">{t('txFlagExcludeFromBudget')}</p>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.exclude_from_budget}
                  aria-label={t('txFlagExcludeFromBudget') as string}
                  onClick={() =>
                    onFormChange({ ...form, exclude_from_budget: !form.exclude_from_budget })
                  }
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
                    form.exclude_from_budget ? 'bg-primary' : 'bg-muted-foreground/30'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                      form.exclude_from_budget ? 'translate-x-[18px]' : 'translate-x-0.5'
                    }`}
                  />
                </button>
              </div>
            ) : null}
          </div>
          </div>
          <DialogFooter className="shrink-0 border-t border-border/60 bg-card px-6 py-4">
            <Button
              variant="outline"
              onClick={() => {
                onReset()
                setOpen(false)
              }}
            >
              {t('dialog.cancel')}
            </Button>
            <Button
              disabled={!canWrite || !canSubmit}
              onClick={async () => {
                const success = await onSave()
                if (success) {
                  setOpen(false)
                }
              }}
            >
              {form.editingId ? t('transactions.button.update') : t('transactions.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 帳戶選擇彈窗(需求 #9,Phase 11):重用 Phase 10 帳戶列表頁的緊湊列樣式。
          非轉帳單一帳戶欄位允許「不選帳戶」;轉帳轉出/轉入各自一個彈窗實例,
          都不帶 allowNone(轉帳兩端必選,見下方 canSubmit 校驗)。 */}
      <AccountPickerDialog
        open={accountPickerOpen}
        onClose={() => setAccountPickerOpen(false)}
        accounts={pickerAccountRows}
        value={form.account_name}
        allowNone
        onSelect={(row) => onFormChange({ ...form, account_name: row.id ? row.name.trim() : '' })}
      />
      <AccountPickerDialog
        open={fromAccountPickerOpen}
        onClose={() => setFromAccountPickerOpen(false)}
        title={t('transactions.placeholder.fromAccountName')}
        accounts={pickerAccountRows}
        value={form.from_account_name}
        onSelect={(row) => onFormChange({ ...form, from_account_name: row.name.trim() })}
      />
      <AccountPickerDialog
        open={toAccountPickerOpen}
        onClose={() => setToAccountPickerOpen(false)}
        title={t('transactions.placeholder.toAccountName')}
        accounts={pickerAccountRows}
        value={form.to_account_name}
        onSelect={(row) => onFormChange({ ...form, to_account_name: row.name.trim() })}
      />

      {/* 标签 picker —— chip 多选,跟 TagsPanel 卡片视觉一致。 */}
      <TagPickerDialog
        open={tagPickerOpen}
        onClose={() => setTagPickerOpen(false)}
        tags={tags}
        selectedNames={selectedTags}
        onChange={(names) => onFormChange({ ...form, tags: names })}
        onClearAll={() => onFormChange({ ...form, tags: [] })}
        onCreateNew={handleCreateTag}
      />

      {/* 分类 picker —— 跟 mobile category_selector_dialog 同样的网格 + 子级
          展开交互。expense / income 切换跟随 form.tx_type;转账类型不开 picker。
          移除"未分类"footer —— 非转账交易必选分类(对齐 mobile transaction_editor_page,
          page 层 onSaveTransaction 也会再 guard 一次)。 */}
      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => {
          setCategoryPickerOpen(false)
          setSplitPickerIndex(null)
        }}
        kind={form.tx_type === 'income' ? 'income' : 'expense'}
        rows={categories as WorkspaceCategory[]}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={
          splitPickerIndex === null
            ? selectedCategoryRow?.id
            : form.splits[splitPickerIndex]?.category_id || undefined
        }
        title={t('transactions.placeholder.categoryName')}
        onCreateNew={handleCreateCategory}
        onSelect={(cat) => {
          // 拆帳(§2.4):splitPickerIndex 非 null = 这次选的是某个 split 行的
          // 分类,写回 form.splits[index] 而不是主 category 字段。
          if (splitPickerIndex !== null) {
            const next = form.splits.slice()
            const idx = splitPickerIndex
            if (next[idx]) {
              next[idx] = { ...next[idx], category_id: cat.id, category_name: cat.name.trim() }
              onFormChange({ ...form, splits: next })
            }
            return
          }
          onFormChange({
            ...form,
            category_name: cat.name.trim(),
            category_kind: form.tx_type,
          })
        }}
      />
    </>
  )
}

/**
 * 把后端 ISO 时间（可能带 Z / 毫秒 / 时区 offset）转成 `<input type="datetime-local">`
 * 期望的 `YYYY-MM-DDTHH:mm` 字符串。用本地时区展示，避免用户看到的时间跟记录
 * 时间错位一个时区。
 */
function isoToDatetimeLocal(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * datetime-local 返回的本地时间字符串反序列化成后端想要的 ISO。保留原 value
 * 的秒与时区（避免用户只改了分钟却把秒抹 0 + 跨时区）。
 */
function datetimeLocalToIso(local: string, fallback: string): string {
  if (!local) return fallback
  // `new Date('2026-04-17T23:32')` 会按本地时区解析；toISOString() 再转 UTC。
  const d = new Date(local)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toISOString()
}

/** 後端部分欄位(card_reward_rule.starts_at/ends_at)回傳的可能是不帶時區
 *  位移的 naive datetime 字串,缺位移標記時強制當 UTC 解析,不然 UTC+8
 *  使用者這裡會把生效窗判斷提早/延後 8 小時(跟 apps/web 那邊
 *  `CardRewardRulesSection.tsx` 的 `forceUtcTimestamp` 同一個理由)。 */
function forceUtcTimestamp(iso: string): number {
  if (!iso) return NaN
  const hasTimezone = /[Zz]$|[+-]\d{2}:\d{2}$/.test(iso)
  return new Date(iso.includes('T') && !hasTimezone ? `${iso}Z` : iso).getTime()
}
