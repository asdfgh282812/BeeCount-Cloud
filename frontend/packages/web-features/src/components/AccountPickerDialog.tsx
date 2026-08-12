import { useMemo, useState } from 'react'

import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  useT
} from '@beecount/ui'

import type { ReadAccount } from '@beecount/api-client'

import { AccountListRow, buildAccountChildrenMap, TYPE_COLORS, TypeIcon, accountTypeLabel } from './AccountListRow'
import { computeTypeGroups } from '../features/AccountsPanel'

type AccountPickerDialogProps = {
  open: boolean
  onClose: () => void
  title?: string
  /** 候選帳戶(通常已排除已隱藏帳戶,但可能保留使用者目前選中的那一個隱藏
   *  帳戶讓他能原样保存 —— 呼叫方比照既有 accountOptionsWithPinned 邏輯決定)。
   *  `account_group`(純管理容器)本身不可被選中,但仍需要出現在候選裡才能
   *  當子帳戶的巢狀父列渲染。 */
  accounts: readonly ReadAccount[]
  /** 目前選中的帳戶名(比對 name,大小寫不敏感),用於高亮/搜尋清空後定位。 */
  value?: string
  onSelect: (row: ReadAccount) => void
  /** 顯示「不選帳戶」選項(非轉帳單一帳戶欄位用;轉帳的轉出/轉入不傳)。 */
  allowNone?: boolean
  noneLabel?: string
  emptyText?: string
}

/**
 * 帳戶選擇彈窗(需求 #9,Phase 11):取代原生 `<Select>` 下拉,重用 Phase 10
 * 帳戶列表頁做出來的緊湊列樣式(`AccountListRow`)—— 分組 + 圖示 + 名稱 +
 * 子帳戶縮排,交易表單的帳戶(單一/轉帳轉出/轉入)、Phase 8 回饋規則帳戶
 * 選擇都應該改用同一個元件,避免各自維護一份 UI。
 */
export function AccountPickerDialog({
  open,
  onClose,
  title,
  accounts,
  value,
  onSelect,
  allowNone = false,
  noneLabel,
  emptyText
}: AccountPickerDialogProps) {
  const t = useT()
  const [query, setQuery] = useState('')

  const groups = useMemo(() => computeTypeGroups(accounts, t), [accounts, t])
  const { childrenByParent, childIds } = useMemo(() => buildAccountChildrenMap(groups), [groups])

  const normalizedQuery = query.trim().toLowerCase()
  // 搜尋時放棄分組巢狀,直接攤平成單一清單(含子帳戶),按名稱排序 —— 找特定
  // 一個帳戶時,分組/縮排反而增加视觉扫描成本。
  const searchResults = useMemo(() => {
    if (!normalizedQuery) return null
    return accounts
      .filter((row) => row.account_type !== 'account_group')
      .filter((row) => row.name.toLowerCase().includes(normalizedQuery))
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [accounts, normalizedQuery])

  const handleSelect = (row: ReadAccount) => {
    if (row.account_type === 'account_group') return
    onSelect(row)
    setQuery('')
    onClose()
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          setQuery('')
          onClose()
        }
      }}
    >
      <DialogContent className="flex max-h-[80vh] max-w-lg flex-col gap-3 overflow-hidden">
        <DialogHeader>
          <DialogTitle>{title ?? t('transactions.table.account')}</DialogTitle>
        </DialogHeader>
        <Input
          placeholder={t('accounts.picker.searchPlaceholder')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {allowNone && !normalizedQuery ? (
            <button
              type="button"
              onClick={() => {
                onSelect({ id: '', name: '' } as ReadAccount)
                setQuery('')
                onClose()
              }}
              className="flex w-full items-center px-3.5 py-2.5 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/20"
            >
              {noneLabel ?? t('transactions.placeholder.noAccount')}
            </button>
          ) : null}

          {searchResults ? (
            searchResults.length === 0 ? (
              <div className="py-10 text-center text-sm text-muted-foreground">
                {emptyText ?? t('common.noResults')}
              </div>
            ) : (
              <div className="divide-y divide-border/30">
                {searchResults.map((row) => (
                  <div
                    key={row.id}
                    className={isSelected(row, value) ? 'bg-primary/5' : undefined}
                  >
                    <AccountListRow
                      row={row}
                      color={TYPE_COLORS[row.account_type || 'other'] || '#94a3b8'}
                      isLiability={row.account_type === 'credit_card' || row.account_type === 'loan'}
                      canManage={false}
                      onClick={handleSelect}
                      selectMode
                      hiddenBadge
                    />
                  </div>
                ))}
              </div>
            )
          ) : groups.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              {emptyText ?? t('common.noResults')}
            </div>
          ) : (
            <div className="space-y-3">
              {groups.map((group) => {
                const topLevelRows = group.rows.filter((row) => !childIds.has(row.id))
                if (topLevelRows.length === 0) return null
                return (
                  <div key={group.type}>
                    <div className="flex items-center gap-2 px-1 py-1.5">
                      <div
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full"
                        style={{ background: `${group.color}18`, border: `1px solid ${group.color}40` }}
                      >
                        <TypeIcon type={group.type} size={14} />
                      </div>
                      <span className="text-xs font-medium text-muted-foreground">
                        {accountTypeLabel(t, group.type)}
                      </span>
                    </div>
                    <div className="divide-y divide-border/30 rounded-lg border border-border/40">
                      {topLevelRows.map((row) => (
                        <div key={row.id} className={isSelected(row, value) ? 'bg-primary/5' : undefined}>
                          <AccountListRow
                            row={row}
                            color={group.color}
                            isLiability={group.isLiability}
                            canManage={false}
                            onClick={handleSelect}
                            childRows={childrenByParent.get(row.id)}
                            selectMode
                            hiddenBadge
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
        <Button variant="ghost" onClick={onClose}>
          {t('dialog.cancel')}
        </Button>
      </DialogContent>
    </Dialog>
  )
}

function isSelected(row: ReadAccount, value?: string): boolean {
  const trimmed = (value || '').trim().toLowerCase()
  if (!trimmed) return false
  return row.name.trim().toLowerCase() === trimmed
}

export type { AccountPickerDialogProps }
