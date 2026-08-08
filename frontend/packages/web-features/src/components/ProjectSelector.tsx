import { useMemo, useState } from 'react'

import { Input, useT } from '@beecount/ui'
import type { ReadProject } from '@beecount/api-client'

type ProjectSelectorProps = {
  /** 全量專案列表(該帳本維度,通常從 fetchReadProjects 拿)。已停用
   *  (`enabled=false`,§4.2 軟刪除)的專案不會出現在挑選器裡。 */
  projects: readonly ReadProject[]
  /** 當前選中的專案 syncId(單選,對齊 debt_id 語意)。 */
  selectedId?: string | null
  onSelect: (project: ReadProject) => void
  showSearch?: boolean
  emptyText?: string
  className?: string
  /** 表單內直接新增(Phase 13,比照 CategorySelector/TagSelector 同款模式):
   *  搜尋關鍵字在現有專案裡找不到完全同名(大小寫不敏感)的項目時,顯示
   *  「新增「xxx」」內嵌入口。實際呼叫 createProject 的邏輯由呼叫方實作。 */
  onCreateNew?: (name: string) => void | Promise<void>
}

/**
 * 專案單選器(Phase 13,docs/PH13_PROJECT_SD.md)—— 扁平 chip 清單,結構
 * 比照 `TagSelector`,差異是單選(不是多選)且不帶顏色,只顯示 icon + name。
 */
export function ProjectSelector({
  projects,
  selectedId,
  onSelect,
  showSearch = true,
  emptyText,
  className,
  onCreateNew,
}: ProjectSelectorProps) {
  const t = useT()
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)

  const enabledProjects = useMemo(() => projects.filter((p) => p.enabled), [projects])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const sorted = [...enabledProjects].sort(
      (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name),
    )
    if (!q) return sorted
    return sorted.filter((p) => p.name.toLowerCase().includes(q))
  }, [enabledProjects, query])

  const normalizedQuery = query.trim().toLowerCase()
  const exactMatchExists = enabledProjects.some(
    (p) => p.name.trim().toLowerCase() === normalizedQuery,
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
          placeholder={t('projects.picker.searchPlaceholder')}
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
          {emptyText ?? t('projects.empty')}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {filtered.map((project) => {
            const isSelected = selectedId === project.id
            return (
              <button
                key={project.id}
                type="button"
                aria-pressed={isSelected}
                onClick={() => onSelect(project)}
                title={project.name}
                className={[
                  'flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-all',
                  isSelected
                    ? 'border-primary bg-primary/10 text-primary ring-1 ring-primary/40'
                    : 'border-border/60 text-foreground hover:bg-accent/40',
                ].join(' ')}
              >
                {project.icon ? <span aria-hidden>{project.icon}</span> : null}
                <span className="max-w-[10rem] truncate">{project.name}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
