import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Download, Pencil } from 'lucide-react'

import {
  Button,
  Card,
  CardContent,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useLocale,
  useT,
  useToast,
} from '@beecount/ui'
import {
  type ImportFieldMapping,
  type ImportSummary,
  type SimpleImportEntityType,
  type SimpleImportSummary,
  downloadImportTemplate,
  previewImport,
  uploadAccountsImport,
  uploadCategoriesImport,
  uploadImport,
} from '@beecount/api-client'

import { FieldMappingDialog } from '../../components/import/FieldMappingDialog'
import { FileDropZone } from '../../components/import/FileDropZone'
import { ImportProgressDialog } from '../../components/import/ImportProgressDialog'
import { ImportStatsCard } from '../../components/import/ImportStatsCard'
import { SimpleImportPreviewCard } from '../../components/import/SimpleImportPreviewCard'
import { SimpleImportProgressDialog } from '../../components/import/SimpleImportProgressDialog'
import { TransactionsPreviewCard } from '../../components/import/TransactionsPreviewCard'
import { useAuth } from '../../context/AuthContext'
import { useLedgers } from '../../context/LedgersContext'
import { localizeError } from '../../i18n/errors'
import { consumePendingImportFile } from '../../lib/pwa-intake'

type Phase = 'idle' | 'uploading' | 'preview' | 'executing'
type EntityType = 'transactions' | SimpleImportEntityType

/**
 * 账本导入页 —— 设计 .docs/web-ledger-import.md
 *
 * 上传后默认显示**预览**:统计数字 + 实际交易(前 10 笔)。**映射默认折叠
 * 成顶部一个小标签**(自动识别 ✓ + 编辑按钮),不满意再点编辑出 dialog 改。
 * 「导入目标 / 冲突策略」做成紧凑一行,不抢主视野。
 *
 * 2026-08 新增:頂部 entity type 切換(交易/分類/帳戶)——分類/帳戶範本
 * 格式固定,不需要欄位對應,upload 直接回傳可執行的 preview,少一步。三種
 * entity type 各自獨立(切換會重置當前流程的暫存狀態,不互相影響)。
 */
export function ImportPage() {
  const t = useT()
  const toast = useToast()
  const navigate = useNavigate()
  const { token } = useAuth()
  const { locale } = useLocale()
  const { ledgers, activeLedgerId } = useLedgers()
  const [searchParams] = useSearchParams()

  const [entityType, setEntityType] = useState<EntityType>('transactions')

  const [phase, setPhase] = useState<Phase>('idle')
  const [summary, setSummary] = useState<ImportSummary | null>(null)
  const [executeOpen, setExecuteOpen] = useState(false)
  const [mappingDialogOpen, setMappingDialogOpen] = useState(false)

  // 分類 / 帳戶匯入(2026-08 新增):獨立於上面 tx 流程的一套暫存狀態。
  const [simpleLedgerId, setSimpleLedgerId] = useState(searchParams.get('ledger') || activeLedgerId || '')
  const [simpleSummary, setSimpleSummary] = useState<SimpleImportSummary | null>(null)
  const [simpleExecuteOpen, setSimpleExecuteOpen] = useState(false)

  const initialLedger = searchParams.get('ledger') || activeLedgerId || ''

  const switchEntityType = (next: EntityType) => {
    if (next === entityType) return
    setEntityType(next)
    setPhase('idle')
    setSummary(null)
    setSimpleSummary(null)
  }

  const handleSelectFile = useCallback(
    async (file: File) => {
      if (entityType !== 'transactions') {
        if (!simpleLedgerId) {
          toast.error(t('import.target.pickLedger'))
          return
        }
        setPhase('uploading')
        try {
          const uploadFn = entityType === 'categories' ? uploadCategoriesImport : uploadAccountsImport
          const sum = await uploadFn(token, { file, targetLedgerId: simpleLedgerId })
          setSimpleSummary(sum)
          setPhase('preview')
        } catch (err) {
          setPhase('idle')
          toast.error(localizeError(err, t))
        }
        return
      }
      setPhase('uploading')
      try {
        const sum = await uploadImport(token, {
          file,
          targetLedgerId: initialLedger || null,
        })
        setSummary(sum)
        setPhase('preview')
      } catch (err) {
        setPhase('idle')
        toast.error(localizeError(err, t))
      }
    },
    [token, initialLedger, toast, t, entityType, simpleLedgerId],
  )

  // PWA Share Target / File Handler 入口:ShareIncomingPage 把 File 暂存到
  // pwa-intake 单例,这里挂载时 consume 一次,自动触发上传流程。useRef 守门
  // 避免 StrictMode 双 mount 重复消费。只对交易匯入生效(分享进来的檔案预设
  // 当交易处理,分類/帳戶匯入需要使用者主动切换 tab)。
  const pwaPickedRef = useRef(false)
  useEffect(() => {
    if (pwaPickedRef.current) return
    if (entityType !== 'transactions') return
    const file = consumePendingImportFile()
    if (!file) return
    pwaPickedRef.current = true
    void handleSelectFile(file)
  }, [handleSelectFile, entityType])

  const refreshPreview = useCallback(
    async (
      patch: {
        mapping?: ImportFieldMapping
        targetLedgerId?: string | null
        dedupStrategy?: 'skip_duplicates' | 'insert_all'
        autoTagNames?: string[]
      },
    ) => {
      if (!summary) return
      setPhase('uploading')
      try {
        const sum = await previewImport(token, summary.import_token, patch)
        setSummary(sum)
        setPhase('preview')
      } catch (err) {
        setPhase('preview')
        toast.error(localizeError(err, t))
      }
    },
    [summary, token, toast, t],
  )

  const handleExecute = () => {
    if (!summary) return
    if (!summary.target_ledger_id) {
      toast.error(t('import.exec.needLedger'))
      return
    }
    if (summary.stats.parse_errors_total > 0) {
      toast.error(t('import.exec.fixErrorsFirst'))
      return
    }
    setExecuteOpen(true)
    setPhase('executing')
  }

  const handleSimpleExecute = () => {
    if (!simpleSummary) return
    if (simpleSummary.errors.length > 0) {
      toast.error(t('import.exec.fixErrorsFirst'))
      return
    }
    setSimpleExecuteOpen(true)
    setPhase('executing')
  }

  const handleDownloadTemplate = (format: 'csv' | 'xlsx') => {
    void downloadImportTemplate(token, { entityType, format, lang: locale }).catch((err) => {
      toast.error(localizeError(err, t))
    })
  }

  const onSuccess = useCallback(
    (data: { created_tx_count: number; skipped_count: number }) => {
      toast.success(
        t('import.exec.successToast', {
          created: data.created_tx_count,
          skipped: data.skipped_count,
        }),
      )
    },
    [toast, t],
  )

  const onSimpleSuccess = useCallback(
    (data: { created_count: number }) => {
      toast.success(t('import.simple.progress.completeBody', { count: data.created_count }))
    },
    [toast, t],
  )

  const requiredComplete = !!(
    summary?.current_mapping.tx_type &&
    summary?.current_mapping.amount &&
    summary?.current_mapping.happened_at
  )

  const showSimpleFlow = entityType !== 'transactions'
  const activeSummary = showSimpleFlow ? simpleSummary : summary

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8"
          onClick={() => navigate(-1)}
          aria-label={t('common.back') as string}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-lg font-semibold">{t('import.pageTitle')}</h1>
      </div>

      {/* entity type 切換(2026-08 新增)—— 三種各自獨立,切換即重置暫存狀態。 */}
      <div className="flex gap-1 rounded-full border border-border/60 bg-muted/20 p-1 text-xs">
        {(['transactions', 'categories', 'accounts'] as EntityType[]).map((et) => (
          <button
            key={et}
            type="button"
            onClick={() => switchEntityType(et)}
            disabled={phase === 'uploading' || phase === 'executing'}
            className={`flex-1 rounded-full px-3 py-1.5 font-medium transition ${
              entityType === et
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-muted/60'
            }`}
          >
            {t(`import.entityType.${et}`)}
          </button>
        ))}
      </div>

      {phase === 'idle' || (phase === 'uploading' && !activeSummary) ? (
        <Card className="bc-panel">
          <CardContent className="space-y-3 p-6">
            {showSimpleFlow ? (
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-muted-foreground">{t('import.target.ledger')}:</span>
                <Select value={simpleLedgerId} onValueChange={setSimpleLedgerId}>
                  <SelectTrigger className="h-7 w-[200px] text-xs">
                    <SelectValue placeholder={t('import.target.pickLedger')} />
                  </SelectTrigger>
                  <SelectContent>
                    {ledgers.map((l) => (
                      <SelectItem key={l.ledger_id} value={l.ledger_id}>
                        {l.ledger_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <FileDropZone onSelect={handleSelectFile} disabled={phase === 'uploading'} />
            {phase === 'uploading' ? (
              <p className="text-center text-xs text-muted-foreground">
                {t('import.uploading')}
              </p>
            ) : null}

            {/* 範本下載(2026-08 新增) */}
            <div className="flex items-center justify-center gap-2 pt-1">
              <span className="text-[11px] text-muted-foreground">{t('import.template.label')}</span>
              <Button size="sm" variant="outline" className="h-7" onClick={() => handleDownloadTemplate('csv')}>
                <Download className="mr-1 h-3 w-3" />
                CSV
              </Button>
              <Button size="sm" variant="outline" className="h-7" onClick={() => handleDownloadTemplate('xlsx')}>
                <Download className="mr-1 h-3 w-3" />
                Excel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {showSimpleFlow && simpleSummary ? (
        <>
          <SimpleImportPreviewCard entityType={entityType as SimpleImportEntityType} summary={simpleSummary} />
          <div className="flex items-center justify-end gap-2 rounded-md border border-border/60 bg-muted/20 px-4 py-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSimpleSummary(null)
                setPhase('idle')
              }}
              disabled={phase === 'uploading' || phase === 'executing'}
            >
              {t('common.cancel')}
            </Button>
            <Button
              size="sm"
              onClick={handleSimpleExecute}
              disabled={
                phase === 'uploading' || phase === 'executing' || simpleSummary.errors.length > 0
              }
            >
              {t('import.exec.button', { count: simpleSummary.valid_rows })}
            </Button>
          </div>
        </>
      ) : null}

      {!showSimpleFlow && summary ? (
        <>
          {/* 紧凑信息栏:映射状态徽章(必填不全 → 红色) + 编辑映射按钮。
              「检测到来源」徽章去掉 — sniff 不一定对(用户清洗过的 BeeCount
              文件可能被识别成 alipay),展示反而误导。来源仅做内部分发用。 */}
          <Card className="bc-panel">
            <CardContent className="flex flex-wrap items-center gap-3 p-3 text-xs">
              {requiredComplete ? (
                <span className="rounded-full border border-emerald-500/30 bg-emerald-500/5 px-2 py-0.5 text-emerald-600 dark:text-emerald-400">
                  ✓ {t('import.mappingBadge.auto')}
                </span>
              ) : (
                <span className="rounded-full border border-destructive/40 bg-destructive/5 px-2 py-0.5 text-destructive">
                  ⚠ {t('import.mappingBadge.incomplete')}
                </span>
              )}
              <Button
                size="sm"
                variant="outline"
                className="h-7"
                onClick={() => setMappingDialogOpen(true)}
                disabled={phase === 'uploading'}
              >
                <Pencil className="mr-1 h-3 w-3" />
                {t('import.mappingBadge.edit')}
              </Button>
              <span className="ml-auto text-muted-foreground">
                {t('import.detected.expiresAt', {
                  time: new Date(summary.expires_at).toLocaleTimeString(),
                })}
              </span>
            </CardContent>
          </Card>

          {/* 统计卡 */}
          <ImportStatsCard stats={summary.stats} />

          {/* 实际交易预览(前 10 笔) */}
          <TransactionsPreviewCard
            samples={summary.sample_transactions}
            totalRows={summary.stats.total_rows}
          />

          {/* 紧凑导入目标行 + 主操作 */}
          <div className="flex flex-wrap items-center gap-3 rounded-md border border-border/60 bg-muted/20 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">
                {t('import.target.ledger')}:
              </span>
              <Select
                value={summary.target_ledger_id || ''}
                onValueChange={(v) =>
                  void refreshPreview({ targetLedgerId: v || null })
                }
                disabled={phase === 'uploading'}
              >
                <SelectTrigger className="h-7 w-[180px] text-xs">
                  <SelectValue placeholder={t('import.target.pickLedger')} />
                </SelectTrigger>
                <SelectContent>
                  {ledgers.map((l) => (
                    <SelectItem key={l.ledger_id} value={l.ledger_id}>
                      {l.ledger_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">
                {t('import.target.dedup')}:
              </span>
              <Select
                value={summary.dedup_strategy}
                onValueChange={(v) =>
                  void refreshPreview({
                    dedupStrategy: v as 'skip_duplicates' | 'insert_all',
                  })
                }
                disabled={phase === 'uploading'}
              >
                <SelectTrigger className="h-7 w-[160px] truncate text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip_duplicates">
                    {t('import.target.dedup.skip')}
                  </SelectItem>
                  <SelectItem value="insert_all">
                    {t('import.target.dedup.insertAll')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSummary(null)
                  setPhase('idle')
                }}
                disabled={phase === 'uploading' || phase === 'executing'}
              >
                {t('common.cancel')}
              </Button>
              <Button
                size="sm"
                onClick={handleExecute}
                disabled={
                  phase === 'uploading' ||
                  phase === 'executing' ||
                  !summary.target_ledger_id ||
                  summary.stats.parse_errors_total > 0
                }
              >
                {t('import.exec.button', { count: summary.stats.total_rows })}
              </Button>
            </div>
          </div>

          <FieldMappingDialog
            open={mappingDialogOpen}
            headers={summary.headers}
            suggestedMapping={summary.suggested_mapping}
            currentMapping={summary.current_mapping}
            saving={phase === 'uploading'}
            onClose={() => setMappingDialogOpen(false)}
            onApply={(mapping) => void refreshPreview({ mapping })}
          />
        </>
      ) : null}

      <ImportProgressDialog
        open={executeOpen}
        importToken={summary?.import_token || null}
        onClose={() => {
          setExecuteOpen(false)
          if (phase === 'executing') {
            navigate('/app/transactions')
          }
        }}
        onSuccess={onSuccess}
      />

      <SimpleImportProgressDialog
        open={simpleExecuteOpen}
        entityType={entityType === 'transactions' ? 'categories' : entityType}
        importToken={simpleSummary?.import_token || null}
        onClose={() => {
          setSimpleExecuteOpen(false)
          if (phase === 'executing') {
            setSimpleSummary(null)
            setPhase('idle')
            navigate(entityType === 'accounts' ? '/app/accounts' : '/app/categories')
          }
        }}
        onSuccess={onSimpleSuccess}
      />
    </div>
  )
}
