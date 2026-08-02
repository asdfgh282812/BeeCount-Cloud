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
  onChooseEarlyRepay: () => void
  onChoosePayoff: () => void
}

/**
 * 分期產生的交易的編輯發起點(2026-08-03 使用者反饋 #4,對齊 Moze 參考
 * 截圖「修改此記錄 / 修改連同未來分期 / 提前償還本金 / 提前繳清分期」四個
 * 選項)。這筆交易本身不再開一般的自由編輯表單——後端也擋了直接改
 * amount/happened_at/account_id(見 `TX_UPDATE_INSTALLMENT_LINKED`),四個
 * 選項都是導去 `/app/installment-plans` 帶 highlight + action query,由該頁
 * 的 `InstallmentPlansPanel` 自動展開對應的既有差異化編輯表單(單期編輯/
 * rebalance-from/提前還本/提前結清),不在這裡重複實作一份表單。
 */
export function InstallmentEditChoiceDialog({
  open,
  onCancel,
  onChooseEditThis,
  onChooseEditFuture,
  onChooseEarlyRepay,
  onChoosePayoff,
}: Props) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('installmentPlans.editChoice.title')}</DialogTitle>
          <DialogDescription>{t('installmentPlans.editChoice.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 py-2">
          <button
            type="button"
            onClick={onChooseEditThis}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('installmentPlans.editChoice.editThis')}</span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.editChoice.editThisHint')}
            </span>
          </button>
          <button
            type="button"
            onClick={onChooseEditFuture}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('installmentPlans.editChoice.editFuture')}</span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.editChoice.editFutureHint')}
            </span>
          </button>
          <button
            type="button"
            onClick={onChooseEarlyRepay}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('installmentPlans.button.earlyRepay')}</span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.editChoice.earlyRepayHint')}
            </span>
          </button>
          <button
            type="button"
            onClick={onChoosePayoff}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5"
          >
            <span className="text-sm font-medium">{t('installmentPlans.button.payoff')}</span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.editChoice.payoffHint')}
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
