import { useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useT,
} from '@beecount/ui'

import type {
  ReadAccount,
  ReadCardRewardRule,
  ReadProject,
  ReadTag,
  ReadTxTemplate,
  TxTemplateApplyPayload,
  WorkspaceCategory,
  WorkspaceTag,
} from '@beecount/api-client'

import { Amount } from '../components/Amount'
import { AccountPickerDialog } from '../components/AccountPickerDialog'
import { CategoryIcon } from '../components/CategoryIcon'
import { CategoryPickerDialog } from '../components/CategoryPickerDialog'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { CurrencySelectorTrigger } from '../components/CurrencySelector'
import { DateTimePicker } from '../components/DateTimePicker'
import { ProjectPickerDialog } from '../components/ProjectPickerDialog'
import { TagPickerDialog } from '../components/TagPickerDialog'
import type { TxTemplateForm } from '../forms'
import { txTemplateDefaults } from '../forms'

type TxTemplatesPanelProps = {
  templates: readonly ReadTxTemplate[]
  categories: readonly WorkspaceCategory[]
  accounts: readonly ReadAccount[]
  /** 標籤(user-global);範本存 syncId,挑選器用名稱,面板內部做對照。 */
  tags: readonly (ReadTag | WorkspaceTag)[]
  /** 該帳本的專案;已停用的專案由 ProjectPickerDialog 自己過濾。 */
  projects: readonly ReadProject[]
  /** 目前表單所選帳戶(信用卡)名下的回饋規則,由頁面依 `form.account_id` 抓。 */
  rewardRules: readonly ReadCardRewardRule[]
  iconPreviewUrlByFileId?: Record<string, string>
  /** 帳本本位幣。 */
  currency: string
  form: TxTemplateForm
  onFormChange: (next: TxTemplateForm) => void
  onSubmit: () => Promise<boolean> | boolean
  onDelete: (template: ReadTxTemplate) => Promise<void> | void
  onApply: (template: ReadTxTemplate, payload: TxTemplateApplyPayload) => Promise<void> | void
  /** 账本 owner 才能新建/编辑/删除範本(server `_OWNER_ONLY_ROLES`)。套用範本
   *  走一般交易写权限,不受这个开关限制。 */
  canManage: boolean
}

type SortMode = 'frequent' | 'manual'

const SORT_STORAGE_KEY = 'bc.txTemplates.sort'

function loadSortMode(): SortMode {
  try {
    return window.localStorage.getItem(SORT_STORAGE_KEY) === 'manual' ? 'manual' : 'frequent'
  } catch {
    return 'frequent'
  }
}

/**
 * 「記憶」排序:常用 = 使用次數多的在前,同次數看最近套用時間,再退回手動排序;
 * 手動 = 照 sort_order(同值按名稱)。範本內容本身不變,只動順序。
 */
export function sortTemplates(rows: readonly ReadTxTemplate[], mode: SortMode): ReadTxTemplate[] {
  const byManual = (a: ReadTxTemplate, b: ReadTxTemplate) =>
    (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)
  const out = [...rows]
  if (mode === 'manual') return out.sort(byManual)
  return out.sort((a, b) => {
    const diff = (b.usage_count ?? 0) - (a.usage_count ?? 0)
    if (diff !== 0) return diff
    const ta = a.last_used_at ? Date.parse(a.last_used_at) : 0
    const tb = b.last_used_at ? Date.parse(b.last_used_at) : 0
    if (tb !== ta) return tb - ta
    return byManual(a, b)
  })
}

function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (next: boolean) => void }) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 md:col-span-2">
      <p className="text-sm font-medium">{label}</p>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors ${
          checked ? 'bg-primary' : 'bg-muted-foreground/30'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  )
}

function PickerButton({
  label,
  value,
  placeholder,
  onClick,
  className = '',
}: {
  label: string
  value: string
  placeholder: string
  onClick: () => void
  className?: string
}) {
  return (
    <div className={`space-y-1 ${className}`}>
      <Label>{label}</Label>
      <button
        type="button"
        onClick={onClick}
        className="flex h-10 w-full items-center justify-between gap-2 rounded-md border border-input bg-muted px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40"
      >
        <span className={`flex-1 truncate ${value ? '' : 'text-muted-foreground'}`}>{value || placeholder}</span>
        <span className="text-xs text-muted-foreground opacity-60">▾</span>
      </button>
    </div>
  )
}

/**
 * 交易範本面板(MOZE_FEATURE_GAP_SD.md §2.7;v2 對齊 App 記帳表單全部欄位)。
 * 存一組固定的交易行為(類型/金額/分類/帳戶/商家/標籤/專案/幣別/手續費折扣/
 * 不計入統計與預算/信用卡回饋規則),「套用」直接把內容套成一筆新交易,金額/備註
 * 可在套用時臨時覆蓋;金額留空(0)的範本套用時必須填金額。清單依使用次數排
 * 「常用」,也可切回手動排序。
 */
export function TxTemplatesPanel({
  templates,
  categories,
  accounts,
  tags,
  projects,
  rewardRules,
  iconPreviewUrlByFileId,
  currency,
  form,
  onFormChange,
  onSubmit,
  onDelete,
  onApply,
  canManage,
}: TxTemplatesPanelProps) {
  const t = useT()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [categoryPickerOpen, setCategoryPickerOpen] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<ReadTxTemplate | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [applyTarget, setApplyTarget] = useState<ReadTxTemplate | null>(null)
  const [accountPickerOpen, setAccountPickerOpen] = useState(false)
  const [fromAccountPickerOpen, setFromAccountPickerOpen] = useState(false)
  const [toAccountPickerOpen, setToAccountPickerOpen] = useState(false)
  const [tagPickerOpen, setTagPickerOpen] = useState(false)
  const [projectPickerOpen, setProjectPickerOpen] = useState(false)
  const [applyAt, setApplyAt] = useState('')
  const [applyAmount, setApplyAmount] = useState('')
  const [applyNote, setApplyNote] = useState('')
  const [applyNative, setApplyNative] = useState('')
  const [applying, setApplying] = useState(false)
  const [query, setQuery] = useState('')
  const [sortMode, setSortModeState] = useState<SortMode>(loadSortMode)

  const setSortMode = (next: SortMode) => {
    setSortModeState(next)
    try {
      window.localStorage.setItem(SORT_STORAGE_KEY, next)
    } catch {
      // 隱私模式 / 禁用儲存:只是記不住偏好,不影響功能。
    }
  }

  const visibleTemplates = useMemo(() => {
    const q = query.trim().toLowerCase()
    const rows = q
      ? templates.filter((row) =>
          [row.name, row.merchant, row.note, row.category_name, row.account_name]
            .filter(Boolean)
            .some((v) => String(v).toLowerCase().includes(q)),
        )
      : templates
    return sortTemplates(rows, sortMode)
  }, [templates, query, sortMode])

  const tagNameById = useMemo(() => new Map(tags.map((tag) => [tag.id, tag.name])), [tags])
  const tagIdByName = useMemo(() => new Map(tags.map((tag) => [tag.name, tag.id])), [tags])
  const selectedTagNames = form.tag_ids
    .map((id) => tagNameById.get(id))
    .filter((name): name is string => Boolean(name))

  const isTransfer = form.tx_type === 'transfer'
  const categoryKind = form.tx_type === 'income' ? 'income' : 'expense'
  const baseCurrency = (currency || '').toUpperCase()

  const handleOpenCreate = () => {
    onFormChange(txTemplateDefaults())
    setDialogOpen(true)
  }

  const handleOpenEdit = (template: ReadTxTemplate) => {
    onFormChange({
      editingId: template.id,
      name: template.name,
      tx_type: (template.tx_type as TxTemplateForm['tx_type']) || 'expense',
      amount: template.amount > 0 ? String(template.amount) : '',
      note: template.note || '',
      merchant: template.merchant || '',
      category_id: template.category_id || '',
      category_name: template.category_name || '',
      account_id: template.account_id || '',
      account_name: template.account_name || '',
      from_account_id: template.from_account_id || '',
      from_account_name: template.from_account_name || '',
      to_account_id: template.to_account_id || '',
      to_account_name: template.to_account_name || '',
      tag_ids: template.tag_ids ?? [],
      project_id: template.project_id || '',
      project_name: template.project_name || '',
      currency_code: template.currency_code || '',
      to_amount: template.to_amount != null ? String(template.to_amount) : '',
      fee_amount: template.fee_amount != null ? String(template.fee_amount) : '',
      fee_label: template.fee_label || '',
      discount_amount: template.discount_amount != null ? String(template.discount_amount) : '',
      discount_label: template.discount_label || '',
      exclude_from_stats: Boolean(template.exclude_from_stats),
      exclude_from_budget: Boolean(template.exclude_from_budget),
      reward_rule_ids: template.reward_rule_ids ?? [],
      sort_order: String(template.sort_order ?? 0),
    })
    setDialogOpen(true)
  }

  const handleSubmit = async () => {
    setSubmitting(true)
    try {
      const ok = await onSubmit()
      if (ok) setDialogOpen(false)
    } finally {
      setSubmitting(false)
    }
  }

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await onDelete(pendingDelete)
      setPendingDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  const openApplyDialog = (template: ReadTxTemplate) => {
    setApplyTarget(template)
    setApplyAt(nowLocalInput())
    setApplyAmount(template.amount > 0 ? String(template.amount) : '')
    setApplyNote(template.note || '')
    setApplyNative('')
  }

  const applyNeedsNative =
    applyTarget !== null &&
    Boolean(applyTarget.currency_code) &&
    applyTarget.currency_code!.toUpperCase() !== baseCurrency
  const applyAmountNum = Number(applyAmount)
  const canApply =
    applyTarget !== null &&
    Number.isFinite(applyAmountNum) &&
    applyAmountNum > 0 &&
    (!applyNeedsNative || Number(applyNative) > 0)

  const handleConfirmApply = async () => {
    if (!applyTarget || !canApply) return
    setApplying(true)
    try {
      await onApply(applyTarget, {
        happened_at: new Date(applyAt).toISOString(),
        amount: applyAmountNum,
        note: applyNote || null,
        native_amount: applyNeedsNative ? Number(applyNative) : null,
      })
      setApplyTarget(null)
    } finally {
      setApplying(false)
    }
  }

  const categoryPickerRows = useMemo(
    () => categories.filter((c) => c.kind === categoryKind),
    [categories, categoryKind],
  )

  const selectedAccount = accounts.find((a) => a.id === form.account_id)
  const showRewardRules =
    form.tx_type === 'expense' &&
    selectedAccount?.account_type === 'credit_card' &&
    rewardRules.length > 0

  // MOZE:範本名稱必須獨一無二(套用時靠名稱挑)。
  const nameTaken = templates.some(
    (row) =>
      row.id !== form.editingId &&
      row.name.trim().toLowerCase() === form.name.trim().toLowerCase(),
  )
  const amountNum = form.amount.trim() === '' ? 0 : Number(form.amount)
  const canSubmit =
    Boolean(form.name.trim()) &&
    !nameTaken &&
    Number.isFinite(amountNum) &&
    amountNum >= 0 &&
    (isTransfer ? Boolean(form.from_account_id) && Boolean(form.to_account_id) : true)

  const activeProjects = projects.filter((p) => p.enabled !== false)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t('txTemplates.desc')}</p>
        <Button size="sm" disabled={!canManage} onClick={handleOpenCreate}>
          {t('txTemplates.button.create')}
        </Button>
      </div>

      {templates.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <Input
            className="h-9 max-w-xs"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('txTemplates.search.placeholder')}
          />
          <div className="inline-flex overflow-hidden rounded-md border border-input text-xs" role="group">
            {(['frequent', 'manual'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={sortMode === mode}
                onClick={() => setSortMode(mode)}
                className={`px-3 py-1.5 transition-colors ${
                  sortMode === mode ? 'bg-primary/15 text-primary' : 'text-muted-foreground hover:bg-accent/40'
                }`}
              >
                {t(mode === 'frequent' ? 'txTemplates.sort.frequent' : 'txTemplates.sort.manual')}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      {templates.length === 0 ? (
        <EmptyState
          icon={
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="4" y="3" width="16" height="18" rx="2" />
              <path d="M8 8h8M8 12h8M8 16h5" />
            </svg>
          }
          title={t('txTemplates.empty')}
          description={t('txTemplates.emptyDesc')}
        />
      ) : visibleTemplates.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">{t('txTemplates.search.empty')}</p>
      ) : (
        <div className="space-y-3">
          {visibleTemplates.map((template) => {
            const cat = template.category_id
              ? categories.find((c) => c.id === template.category_id)
              : null
            const usage = template.usage_count ?? 0
            const chips = [
              template.merchant,
              template.project_name,
              template.currency_code && template.currency_code.toUpperCase() !== baseCurrency
                ? template.currency_code.toUpperCase()
                : null,
              ...(template.tag_ids ?? []).map((id) => tagNameById.get(id)).filter(Boolean),
            ].filter((v): v is string => Boolean(v))
            return (
              <div
                key={template.id}
                className="rounded-xl border border-border/60 bg-card p-4 transition hover:border-primary/40 hover:shadow-sm"
              >
                <div className="flex items-start gap-3">
                  <span
                    aria-hidden
                    className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                  >
                    {template.tx_type === 'transfer' ? (
                      <span className="material-symbols-outlined text-2xl">swap_horiz</span>
                    ) : (
                      <CategoryIcon
                        icon={cat?.icon}
                        iconType={cat?.icon_type || 'material'}
                        iconCloudFileId={cat?.icon_cloud_file_id || null}
                        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
                        size={24}
                      />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold">{template.name}</span>
                      {usage > 0 ? (
                        <span className="shrink-0 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                          {t('txTemplates.usage.count').replace('{count}', String(usage))}
                        </span>
                      ) : null}
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      {template.tx_type === 'transfer'
                        ? `${template.from_account_name || ''} → ${template.to_account_name || ''}`
                        : [template.category_name, template.account_name].filter(Boolean).join(' · ')}
                    </div>
                    {chips.length > 0 ? (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {chips.map((chip, idx) => (
                          <span
                            key={`${chip}-${idx}`}
                            className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground"
                          >
                            {chip}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {template.note ? (
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {template.note}
                      </div>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-right">
                    {template.amount > 0 ? (
                      <Amount value={template.amount} currency={currency} size="md" bold tone="default" />
                    ) : (
                      <span className="text-xs text-muted-foreground">{t('txTemplates.amount.askOnApply')}</span>
                    )}
                  </div>
                </div>

                <div className="mt-3 flex items-center justify-end gap-2">
                  <Button size="sm" onClick={() => openApplyDialog(template)}>
                    {t('txTemplates.button.apply')}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={!canManage} onClick={() => handleOpenEdit(template)}>
                    {t('common.edit')}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={!canManage}
                    onClick={() => setPendingDelete(template)}
                  >
                    {t('common.delete')}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 创建/编辑 dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {form.editingId ? t('txTemplates.button.update') : t('txTemplates.button.create')}
            </DialogTitle>
          </DialogHeader>
          <div className="grid max-h-[70vh] gap-3 overflow-y-auto pr-1 md:grid-cols-2">
            <div className="space-y-1 md:col-span-2">
              <Label>{t('txTemplates.field.name')}</Label>
              <Input
                value={form.name}
                onChange={(e) => onFormChange({ ...form, name: e.target.value })}
                placeholder={t('txTemplates.placeholder.name')}
              />
              {nameTaken ? (
                <p className="text-xs text-destructive">{t('txTemplates.error.nameTaken')}</p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label>{t('transactions.table.type')}</Label>
              <Select
                value={form.tx_type}
                onValueChange={(value) => {
                  const next = value as TxTemplateForm['tx_type']
                  onFormChange({
                    ...form,
                    tx_type: next,
                    category_id: '',
                    category_name: '',
                    // 專案只支援收支、回饋規則只支援支出、不計預算只支援支出。
                    project_id: next === 'transfer' ? '' : form.project_id,
                    project_name: next === 'transfer' ? '' : form.project_name,
                    reward_rule_ids: next === 'expense' ? form.reward_rule_ids : [],
                    exclude_from_stats: next === 'transfer' ? false : form.exclude_from_stats,
                    exclude_from_budget: next === 'expense' ? form.exclude_from_budget : false,
                  })
                }}
              >
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
              <Label>{t('budgets.field.amount')}</Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                placeholder={t('txTemplates.amount.placeholder')}
                value={form.amount}
                onChange={(e) => onFormChange({ ...form, amount: e.target.value })}
              />
              <p className="text-[11px] text-muted-foreground">{t('txTemplates.amount.hint')}</p>
            </div>

            {!isTransfer ? (
              <div className="space-y-1 md:col-span-2">
                <Label>{t('txTemplates.field.currency')}</Label>
                <CurrencySelectorTrigger
                  value={form.currency_code || baseCurrency}
                  onChange={(code) =>
                    onFormChange({
                      ...form,
                      currency_code: code.toUpperCase() === baseCurrency ? '' : code.toUpperCase(),
                    })
                  }
                />
              </div>
            ) : null}

            {isTransfer ? (
              <>
                <PickerButton
                  label={t('transactions.placeholder.fromAccountName')}
                  value={form.from_account_name}
                  placeholder={t('transactions.placeholder.accountName')}
                  onClick={() => setFromAccountPickerOpen(true)}
                />
                <PickerButton
                  label={t('transactions.placeholder.toAccountName')}
                  value={form.to_account_name}
                  placeholder={t('transactions.placeholder.accountName')}
                  onClick={() => setToAccountPickerOpen(true)}
                />
                <div className="space-y-1 md:col-span-2">
                  <Label>{t('txTemplates.field.toAmount')}</Label>
                  <Input
                    type="number"
                    inputMode="decimal"
                    step="0.01"
                    min="0"
                    value={form.to_amount}
                    onChange={(e) => onFormChange({ ...form, to_amount: e.target.value })}
                  />
                  <p className="text-[11px] text-muted-foreground">{t('txTemplates.field.toAmountHint')}</p>
                </div>
              </>
            ) : (
              <>
                <PickerButton
                  label={t('transactions.table.category')}
                  value={form.category_name}
                  placeholder={t('transactions.placeholder.categoryName')}
                  onClick={() => setCategoryPickerOpen(true)}
                />
                <PickerButton
                  label={t('installmentPlans.field.account')}
                  value={form.account_name}
                  placeholder={t('transactions.placeholder.accountName')}
                  onClick={() => setAccountPickerOpen(true)}
                />
                <PickerButton
                  label={t('transactions.field.project')}
                  value={form.project_name}
                  placeholder={t('transactions.placeholder.project')}
                  onClick={() => setProjectPickerOpen(true)}
                />
              </>
            )}

            <div className="space-y-1">
              <Label>{t('transactions.field.merchant')}</Label>
              <Input
                value={form.merchant}
                onChange={(e) => onFormChange({ ...form, merchant: e.target.value })}
                placeholder={t('transactions.placeholder.merchant')}
              />
            </div>

            <PickerButton
              label={t('tags.title')}
              value={selectedTagNames.join('、')}
              placeholder={t('transactions.placeholder.tags')}
              onClick={() => setTagPickerOpen(true)}
            />

            <div className="space-y-1 md:col-span-2">
              <Label>{t('transactions.table.note')}</Label>
              <Input
                value={form.note}
                onChange={(e) => onFormChange({ ...form, note: e.target.value })}
              />
            </div>

            {/* 手續費 / 折扣(expense/income:實際金額 = 金額 ± 手續費 ∓ 折扣,由 server 重算;
                transfer:疊加在餘額上的獨立 delta)。 */}
            <div className="space-y-1">
              <Label>{t('transactions.field.fee')}</Label>
              <div className="grid grid-cols-2 gap-2">
                <Input
                  placeholder={t('transactions.field.fee')}
                  value={form.fee_label}
                  onChange={(e) => onFormChange({ ...form, fee_label: e.target.value })}
                />
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0"
                  value={form.fee_amount}
                  onChange={(e) => onFormChange({ ...form, fee_amount: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label>{t('transactions.field.discount')}</Label>
              <div className="grid grid-cols-2 gap-2">
                <Input
                  placeholder={t('transactions.field.discount')}
                  value={form.discount_label}
                  onChange={(e) => onFormChange({ ...form, discount_label: e.target.value })}
                />
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  placeholder="0"
                  value={form.discount_amount}
                  onChange={(e) => onFormChange({ ...form, discount_amount: e.target.value })}
                />
              </div>
            </div>

            {showRewardRules ? (
              <div className="space-y-1 md:col-span-2">
                <Label>{t('transactions.field.rewardRules')}</Label>
                <div className="flex flex-wrap gap-1.5">
                  {rewardRules
                    .filter((rule) => rule.enabled || form.reward_rule_ids.includes(rule.id))
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
                                : [...form.reward_rule_ids, rule.id],
                            })
                          }
                          className={`rounded-full border px-2.5 py-1 text-xs ${
                            checked
                              ? 'border-primary bg-primary/15 text-primary'
                              : 'border-input text-muted-foreground hover:bg-accent/40'
                          }`}
                        >
                          {rule.label}
                        </button>
                      )
                    })}
                </div>
              </div>
            ) : null}

            {!isTransfer ? (
              <SwitchRow
                label={t('txFlagExcludeFromStats') as string}
                checked={form.exclude_from_stats}
                onChange={(next) => onFormChange({ ...form, exclude_from_stats: next })}
              />
            ) : null}
            {form.tx_type === 'expense' ? (
              <SwitchRow
                label={t('txFlagExcludeFromBudget') as string}
                checked={form.exclude_from_budget}
                onChange={(next) => onFormChange({ ...form, exclude_from_budget: next })}
              />
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={submitting} onClick={() => setDialogOpen(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={submitting || !canManage || !canSubmit} onClick={() => void handleSubmit()}>
              {form.editingId ? t('txTemplates.button.update') : t('txTemplates.button.create')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 套用範本 dialog */}
      <Dialog open={applyTarget !== null} onOpenChange={(next) => !next && setApplyTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t('txTemplates.apply.title')}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label>{t('recurringRules.field.nextRunAt')}</Label>
              <DateTimePicker value={applyAt} onChange={(next) => setApplyAt(next)} />
            </div>
            <div className="space-y-1">
              <Label>
                {t('budgets.field.amount')}
                {applyTarget && applyTarget.amount <= 0 ? (
                  <span className="ml-1 text-xs text-destructive">{t('txTemplates.apply.amountRequired')}</span>
                ) : null}
              </Label>
              <Input
                type="number"
                inputMode="decimal"
                step="0.01"
                min="0"
                value={applyAmount}
                onChange={(e) => setApplyAmount(e.target.value)}
              />
            </div>
            {applyNeedsNative ? (
              <div className="space-y-1">
                <Label>
                  {t('txTemplates.apply.nativeAmount').replace('{currency}', baseCurrency)}
                </Label>
                <Input
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0"
                  value={applyNative}
                  onChange={(e) => setApplyNative(e.target.value)}
                />
              </div>
            ) : null}
            <div className="space-y-1">
              <Label>{t('transactions.table.note')}</Label>
              <Input value={applyNote} onChange={(e) => setApplyNote(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={applying} onClick={() => setApplyTarget(null)}>
              {t('dialog.cancel')}
            </Button>
            <Button disabled={applying || !canApply} onClick={() => void handleConfirmApply()}>
              {t('txTemplates.button.apply')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryPickerDialog
        open={categoryPickerOpen}
        onClose={() => setCategoryPickerOpen(false)}
        kind={categoryKind}
        rows={categoryPickerRows}
        iconPreviewUrlByFileId={iconPreviewUrlByFileId}
        selectedId={form.category_id || undefined}
        title={t('transactions.placeholder.categoryName')}
        onSelect={(cat) =>
          onFormChange({
            ...form,
            category_id: cat.id,
            category_name: cat.name,
          })
        }
      />

      <AccountPickerDialog
        open={accountPickerOpen}
        onClose={() => setAccountPickerOpen(false)}
        accounts={accounts}
        value={form.account_name}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount')}
        title={t('installmentPlans.field.account')}
        onSelect={(row) =>
          onFormChange({
            ...form,
            account_id: row.id,
            account_name: row.id ? row.name.trim() : '',
            // 換帳戶:原本勾的回饋規則屬於舊那張卡,一併清掉。
            reward_rule_ids: row.id === form.account_id ? form.reward_rule_ids : [],
          })
        }
      />
      <AccountPickerDialog
        open={fromAccountPickerOpen}
        onClose={() => setFromAccountPickerOpen(false)}
        accounts={accounts}
        value={form.from_account_name}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount')}
        title={t('transactions.placeholder.fromAccountName')}
        onSelect={(row) =>
          onFormChange({ ...form, from_account_id: row.id, from_account_name: row.id ? row.name.trim() : '' })
        }
      />
      <AccountPickerDialog
        open={toAccountPickerOpen}
        onClose={() => setToAccountPickerOpen(false)}
        accounts={accounts}
        value={form.to_account_name}
        allowNone
        noneLabel={t('transactions.placeholder.noAccount')}
        title={t('transactions.placeholder.toAccountName')}
        onSelect={(row) =>
          onFormChange({ ...form, to_account_id: row.id, to_account_name: row.id ? row.name.trim() : '' })
        }
      />

      <TagPickerDialog
        open={tagPickerOpen}
        onClose={() => setTagPickerOpen(false)}
        tags={tags}
        selectedNames={selectedTagNames}
        onChange={(names) =>
          onFormChange({
            ...form,
            tag_ids: names.map((name) => tagIdByName.get(name)).filter((id): id is string => Boolean(id)),
          })
        }
        onClearAll={() => onFormChange({ ...form, tag_ids: [] })}
      />

      <ProjectPickerDialog
        open={projectPickerOpen}
        onClose={() => setProjectPickerOpen(false)}
        projects={activeProjects}
        selectedId={form.project_id || null}
        title={t('transactions.field.project')}
        onSelect={(project) => {
          onFormChange({ ...form, project_id: project.id, project_name: project.name.trim() })
          setProjectPickerOpen(false)
        }}
        onClear={() => {
          onFormChange({ ...form, project_id: '', project_name: '' })
          setProjectPickerOpen(false)
        }}
        clearLabel={t('transactions.placeholder.project')}
      />

      <ConfirmDialog
        open={pendingDelete !== null}
        onCancel={() => {
          if (!deleting) setPendingDelete(null)
        }}
        onConfirm={() => void handleConfirmDelete()}
        loading={deleting}
        title={t('txTemplates.delete.title')}
        description={t('txTemplates.delete.confirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
      />
    </div>
  )
}

function nowLocalInput(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
