import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'
import type { ReadProject } from '@beecount/api-client'

import { ProjectSelector } from './ProjectSelector'

type ProjectPickerDialogProps = {
  open: boolean
  onClose: () => void
  projects: readonly ReadProject[]
  selectedId?: string | null
  title: string
  onSelect: (project: ReadProject) => void
  /** 傳入時 footer 顯示「不掛專案」按鈕,點擊清空選擇並關閉。 */
  onClear?: () => void
  clearLabel?: string
  emptyText?: string
  onCreateNew?: (name: string) => void | Promise<void>
}

/**
 * 選擇專案 dialog(Phase 13,docs/PH13_PROJECT_SD.md)—— 把 `ProjectSelector`
 * 包裝成彈窗,結構比照 `CategoryPickerDialog`/`TagPickerDialog`。
 */
export function ProjectPickerDialog({
  open,
  onClose,
  projects,
  selectedId,
  title,
  onSelect,
  onClear,
  clearLabel,
  emptyText,
  onCreateNew,
}: ProjectPickerDialogProps) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto py-2">
          <ProjectSelector
            projects={projects}
            selectedId={selectedId}
            emptyText={emptyText}
            onCreateNew={onCreateNew}
            onSelect={(project) => {
              onSelect(project)
              onClose()
            }}
          />
        </div>
        <DialogFooter>
          {onClear ? (
            <Button
              variant="outline"
              onClick={() => {
                onClear()
                onClose()
              }}
            >
              {clearLabel ?? t('common.none')}
            </Button>
          ) : null}
          <Button variant="ghost" onClick={onClose}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
