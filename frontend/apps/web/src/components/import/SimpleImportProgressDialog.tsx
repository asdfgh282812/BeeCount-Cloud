import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react'

import { Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, useT } from '@beecount/ui'
import {
  type SimpleImportEntityType,
  type SimpleImportSseEvent,
  cancelSimpleImport,
  streamExecuteAccountsImport,
  streamExecuteCategoriesImport,
} from '@beecount/api-client'

import { useAuth } from '../../context/AuthContext'

interface Props {
  open: boolean
  entityType: SimpleImportEntityType
  importToken: string | null
  onClose: () => void
  onSuccess?: (data: { created_count: number }) => void
}

/**
 * 分類 / 帳戶匯入的執行進度 dialog(2026-08 新增)——比交易匯入的
 * `ImportProgressDialog` 單純:只有一個 stage,complete 事件也只有
 * `created_count`(沒有 dedup skip 概念)。獨立元件而不是把
 * `ImportProgressDialog` 改成通用版,是因為兩者的 SSE 事件形狀跟階段模型
 * 差異夠大,硬共用反而兩邊都難讀。
 */
export function SimpleImportProgressDialog({ open, entityType, importToken, onClose, onSuccess }: Props) {
  const t = useT()
  const { token } = useAuth()
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const [phase, setPhase] = useState<'running' | 'complete' | 'error'>('running')
  const [completeData, setCompleteData] = useState<{ created_count: number } | null>(null)
  const [errorData, setErrorData] = useState<{ code: string; row_number: number; message: string } | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!open || !importToken) return
    cancelledRef.current = false
    setProgress(null)
    setPhase('running')
    setCompleteData(null)
    setErrorData(null)

    let aborted = false
    const streamFn = entityType === 'categories' ? streamExecuteCategoriesImport : streamExecuteAccountsImport

    void (async () => {
      try {
        for await (const ev of streamFn(token, importToken)) {
          if (aborted) break
          handleEvent(ev)
        }
      } catch (err) {
        if (!aborted) {
          setPhase('error')
          setErrorData({
            code: 'IMPORT_NETWORK',
            row_number: 0,
            message: err instanceof Error ? err.message : String(err),
          })
        }
      }
    })()

    return () => {
      aborted = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, importToken, entityType])

  const handleEvent = (ev: SimpleImportSseEvent) => {
    if (ev.event === 'stage') {
      setProgress({ done: ev.data.done, total: ev.data.total })
    } else if (ev.event === 'complete') {
      setPhase('complete')
      setCompleteData({ created_count: ev.data.created_count })
      onSuccess?.({ created_count: ev.data.created_count })
    } else if (ev.event === 'error') {
      setPhase('error')
      setErrorData(ev.data)
    }
  }

  const onCancel = async () => {
    if (!importToken) return
    cancelledRef.current = true
    try {
      await cancelSimpleImport(token, importToken)
    } catch {
      // 静默 — 用户已经按了取消
    }
    onClose()
  }

  const percent = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0

  return (
    <Dialog open={open} onOpenChange={(v) => !v && phase !== 'running' && onClose()}>
      <DialogContent className="max-w-md gap-0 p-0">
        <DialogHeader className="border-b border-border/60 px-6 py-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            {phase === 'running' ? (
              <Loader2 className="h-4 w-4 animate-spin text-primary" />
            ) : phase === 'complete' ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-destructive" />
            )}
            {phase === 'running'
              ? t('import.progress.running')
              : phase === 'complete'
                ? t('import.progress.complete')
                : t('import.progress.failed')}
          </DialogTitle>
        </DialogHeader>

        <div className="px-6 py-5 text-sm">
          {phase === 'running' ? (
            <div className="mb-1 h-2 w-full overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-all" style={{ width: `${percent}%` }} />
            </div>
          ) : phase === 'complete' && completeData ? (
            <p>{t('import.simple.progress.completeBody', { count: completeData.created_count })}</p>
          ) : phase === 'error' && errorData ? (
            <div className="space-y-2">
              <p className="text-foreground">{t('import.progress.failedBody')}</p>
              <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-[11px]">
                <p className="font-medium text-destructive">
                  {errorData.code}
                  {errorData.row_number > 0 ? ` · L${errorData.row_number}` : ''}
                </p>
                <p className="mt-1 text-muted-foreground">{errorData.message}</p>
              </div>
            </div>
          ) : null}
        </div>

        <DialogFooter className="border-t border-border/60 bg-muted/20 px-6 py-3">
          {phase === 'running' ? (
            <Button variant="outline" size="sm" onClick={onCancel}>
              <X className="mr-1 h-3 w-3" />
              {t('import.progress.cancel')}
            </Button>
          ) : (
            <Button size="sm" onClick={onClose}>
              {t('common.close')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
