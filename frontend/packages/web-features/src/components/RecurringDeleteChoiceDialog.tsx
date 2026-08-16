import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT
} from '@beecount/ui'

type Props = {
  open: boolean
  loading?: boolean
  onCancel: () => void
  onChooseKeepFuture: () => void
  onChooseDeleteFuture: () => void
}

/**
 * 刪除週期性收支規則(2026-08-16 補):同 RecurringEditChoiceDialog 的兩選項
 * 卡片樣式,但問的是「要不要連同刪除未來已產生的收支記錄」——舊行為(單純
 * `ConfirmDialog`)只會刪規則本身,已產生的交易(含未來的)一律孤兒保留,
 * 沒有選項可以一次連同清掉未來那些。
 */
export function RecurringDeleteChoiceDialog({
  open,
  loading = false,
  onCancel,
  onChooseKeepFuture,
  onChooseDeleteFuture
}: Props) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !loading && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('recurringRules.deleteChoice.title')}</DialogTitle>
          <DialogDescription>{t('recurringRules.deleteChoice.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 py-2">
          <button
            type="button"
            disabled={loading}
            onClick={onChooseKeepFuture}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5 disabled:opacity-50"
          >
            <span className="text-sm font-medium">{t('recurringRules.deleteChoice.keepFuture')}</span>
            <span className="text-xs text-muted-foreground">
              {t('recurringRules.deleteChoice.keepFutureHint')}
            </span>
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={onChooseDeleteFuture}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-destructive/40 px-4 py-3 text-left transition hover:border-destructive hover:bg-destructive/5 disabled:opacity-50"
          >
            <span className="text-sm font-medium text-destructive">
              {t('recurringRules.deleteChoice.deleteFuture')}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('recurringRules.deleteChoice.deleteFutureHint')}
            </span>
          </button>
        </div>
        <DialogFooter>
          <Button variant="outline" disabled={loading} onClick={onCancel}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
