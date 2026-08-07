import { useMemo, useState } from 'react'

import { Input, useT } from '@beecount/ui'
import type { ReadTag, WorkspaceTag } from '@beecount/api-client'

import { tagTextColorOn } from '../lib/tagColorPalette'

/** 同时兼容 ReadTag(单 ledger 范围)和 WorkspaceTag(workspace 跨账本聚合)。 */
type TagLike = (ReadTag | WorkspaceTag) & { name: string; color?: string | null }

type TagSelectorProps = {
  /** 全量标签列表(workspace 维度通常已经按 user_id 去重过)。 */
  tags: readonly TagLike[]
  /** 当前选中的标签名(跟 mobile 一致用 name 数组维护,跨设备稳定靠
   *  syncId,但 transaction tags 字段历史是 csv 字符串,name 跟 syncId
   *  并存。这里用 name 简化外层调用方处理)。 */
  selectedNames: readonly string[]
  /** 选择变化时回调,新选中的 name 数组(已去重)。 */
  onChange: (names: string[]) => void
  /** 是否显示搜索框,标签很多时打开。默认开。 */
  showSearch?: boolean
  /** 没有标签时的占位文案。 */
  emptyText?: string
  className?: string
  /** 表單內直接新增(需求 #10,Phase 11,2026-08 使用者明確要求):搜尋關鍵字
   *  在現有標籤裡找不到完全同名(大小寫不敏感)的項目時,顯示「新增 "xxx"」
   *  內嵌入口,點擊呼叫這個 callback。舊版設計是刻意不做內嵌新增、逼用戶去
   *  標籤管理頁(見下方歷史記錄),這次使用者反饋要求改成表單內直接建立,
   *  不再要求跳頁。 */
  onCreateNew?: (name: string) => void | Promise<void>
}

/**
 * 通用标签多选器 —— chip 风格,跟 mobile tag_picker_sheet 行为对齐:
 * - 全量平铺(没有分组,标签本身就是扁平结构)
 * - 每个 chip 用 tag.color 当背景,选中态边框 ring 主题色 + 勾标
 * - 点击 toggle 选中/反选
 * - 支持搜索(showSearch=true 时顶上加搜索框,大小写不敏感子串匹配)
 * - 支持表單內直接新增(`onCreateNew`,Phase 11 新增,見上方 prop 註解)
 */
export function TagSelector({
  tags,
  selectedNames,
  onChange,
  showSearch = true,
  emptyText,
  className,
  onCreateNew,
}: TagSelectorProps) {
  const t = useT()
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)

  const selectedSet = useMemo(() => {
    const set = new Set<string>()
    for (const name of selectedNames) {
      const trimmed = (name || '').trim().toLowerCase()
      if (trimmed) set.add(trimmed)
    }
    return set
  }, [selectedNames])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const dedup = new Map<string, TagLike>() // 同名(忽略大小写)只留一个,WorkspaceTag dedup 后理论已唯一,这里再保险
    for (const tag of tags) {
      const name = (tag.name || '').trim()
      if (!name) continue
      if (q && !name.toLowerCase().includes(q)) continue
      const key = name.toLowerCase()
      if (!dedup.has(key)) dedup.set(key, tag)
    }
    return Array.from(dedup.values()).sort((a, b) =>
      (a.name || '').localeCompare(b.name || ''),
    )
  }, [tags, query])

  const toggle = (name: string) => {
    const trimmed = name.trim()
    if (!trimmed) return
    const lower = trimmed.toLowerCase()
    const next = new Set(
      selectedNames.map((n) => (n || '').trim()).filter((n) => n.length > 0),
    )
    // Set 自己用大小写敏感存储,删除时也按大小写敏感,这里需要手动跨大小写比对
    let removed = false
    for (const existing of [...next]) {
      if (existing.toLowerCase() === lower) {
        next.delete(existing)
        removed = true
      }
    }
    if (!removed) next.add(trimmed)
    onChange(Array.from(next))
  }

  const normalizedQuery = query.trim().toLowerCase()
  const exactMatchExists = tags.some(
    (tag) => (tag.name || '').trim().toLowerCase() === normalizedQuery,
  )
  const showCreateButton = Boolean(onCreateNew) && normalizedQuery.length > 0 && !exactMatchExists

  const handleCreateNew = async () => {
    if (!onCreateNew || creating) return
    const name = query.trim()
    if (!name) return
    setCreating(true)
    try {
      await onCreateNew(name)
      setQuery('')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className={`space-y-3 ${className || ''}`.trim()}>
      {showSearch ? (
        <Input
          placeholder={t('tags.placeholder.search')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      ) : null}

      {showCreateButton ? (
        <button
          type="button"
          disabled={creating}
          onClick={() => void handleCreateNew()}
          className="flex w-full items-center gap-2 rounded-lg border border-dashed border-primary/50 px-3 py-2 text-sm text-primary transition-colors hover:bg-primary/5 disabled:pointer-events-none disabled:opacity-50"
        >
          <span aria-hidden>+</span>
          <span className="truncate">{t('categories.picker.createNew', { name: query.trim() })}</span>
        </button>
      ) : null}

      {filtered.length === 0 ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          {emptyText ?? t('tags.empty.title')}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {filtered.map((tag) => {
            const name = (tag.name || '').trim()
            const isSelected = selectedSet.has(name.toLowerCase())
            const color = (tag.color || '').trim() || '#94a3b8'
            const textColor = tagTextColorOn(color)
            return (
              <button
                key={tag.id || name}
                type="button"
                aria-pressed={isSelected}
                onClick={() => toggle(name)}
                title={name}
                className={`group relative flex items-center gap-1.5 rounded-full px-3 py-1 text-sm transition-all ${
                  isSelected
                    ? 'shadow-sm ring-2 ring-offset-2 ring-foreground ring-offset-background'
                    : 'opacity-80 hover:opacity-100'
                }`}
                style={{ background: color, color: textColor }}
              >
                <span className="leading-none">{name}</span>
                {isSelected ? (
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke={textColor}
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden
                    className="ml-0.5"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : null}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
