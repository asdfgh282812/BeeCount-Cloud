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
  /** 操作执行中:disable 两个选项按钮,防止双击 / 重复触发。 */
  loading?: boolean
  onCancel: () => void
  onChooseSingle: () => void
  onChooseWhole: () => void
}

/**
 * 分期交易的退款发起点(§2.6):对分期计划生成的某一期做退款前,先问使用者
 * 要「只退這一期」(保留其它期数,该期标记 refunded)还是「整筆退款」(直接
 * 删除整个分期计划与所有期数交易)。两者互斥,由 GlobalEntityDialogs 根据
 * 选择分别调 refundInstallmentPeriod / deleteInstallmentPlan。
 *
 * 「整筆」选项本身不在这里二次确认 —— 那是高破坏性操作(删整个计划,不可
 * 复原),交给调用方在选完之后再接一个 destructive ConfirmDialog,跟本应用
 * 其它"提前结清/终止未来分期"等破坏性操作的既有模式一致。
 */
export function InstallmentRefundChoiceDialog({
  open,
  loading = false,
  onCancel,
  onChooseSingle,
  onChooseWhole,
}: Props) {
  const t = useT()
  return (
    <Dialog open={open} onOpenChange={(next) => !next && !loading && onCancel()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('installmentPlans.refundChoice.title')}</DialogTitle>
          <DialogDescription>{t('installmentPlans.refundChoice.description')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2 py-2">
          <button
            type="button"
            disabled={loading}
            onClick={onChooseSingle}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="text-sm font-medium">{t('installmentPlans.refundChoice.single')}</span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.refundChoice.singleHint')}
            </span>
          </button>
          <button
            type="button"
            disabled={loading}
            onClick={onChooseWhole}
            className="flex flex-col items-start gap-0.5 rounded-lg border border-border/60 px-4 py-3 text-left transition hover:border-destructive hover:bg-destructive/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="text-sm font-medium text-destructive">
              {t('installmentPlans.refundChoice.whole')}
            </span>
            <span className="text-xs text-muted-foreground">
              {t('installmentPlans.refundChoice.wholeHint')}
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
