import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  useT,
} from '@beecount/ui'

type Props = {
  open: boolean
  onCancel: () => void
  onChooseEditThis: () => void
  onChooseEditFuture: () => void
}

/**
 * 週期性收支產生的交易的編輯發起點(docs/PH17_USER_FEEDBACK_2026-08_SD.md
 * Phase 24,需求 #5,對齊 Moze 參考截圖「修改此記錄 / 修改連同未來週期」
 * 兩個選項)。跟 InstallmentEditChoiceDialog 同一套樣式,但選完之後**不**
 * 導頁——沿用同一個編輯表單 UI,只是存檔時分別呼叫
 * `PATCH .../occurrences/{tx_id}`(此記錄,標記 overridden)或
 * `POST .../update-from/{tx_id}`(連同未來)。
 */
export function RecurringEditChoiceDialog({
  open,
  onCancel,
  onChooseEditThis,
  onChooseEditFuture,
}: Props) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('recurringRules.editChoice.title')}</DialogTitle>
          <DialogDescription>{t('recurringRules.editChoice.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 py-2">
          <button
            type="button"
            onClick={onChooseEditThis}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('recurringRules.editChoice.editThis')}</span>
            <span className="text-xs text-muted-foreground">
              {t('recurringRules.editChoice.editThisHint')}
            </span>
          </button>
          <button
            type="button"
            onClick={onChooseEditFuture}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('recurringRules.editChoice.editFuture')}</span>
            <span className="text-xs text-muted-foreground">
              {t('recurringRules.editChoice.editFutureHint')}
            </span>
          </button>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            {t('dialog.cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
