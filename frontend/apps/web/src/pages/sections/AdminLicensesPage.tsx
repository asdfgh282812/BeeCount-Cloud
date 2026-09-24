import { useCallback, useEffect, useState } from 'react'
import { Ban, Copy, KeyRound, Plus, RefreshCcw, Trash2 } from 'lucide-react'

import {
  createAdminLicenseKeys,
  deleteAdminLicenseKey,
  fetchAdminLicenseKeys,
  revokeAdminLicenseKey,
  type AdminLicenseKey,
  type AdminLicenseKeyStatus,
  type AdminLicenseKeyStatusFilter,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useT,
  useToast,
} from '@beecount/ui'
import { ConfirmDialog } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'
import { formatLicenseDate, formatLicenseDateTime } from '../../lib/license'

const STATUS_FILTERS: AdminLicenseKeyStatusFilter[] = ['all', 'unused', 'active', 'expired', 'revoked']

const STATUS_BADGE_CLASS: Record<AdminLicenseKeyStatus, string> = {
  unused: 'border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  active: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  expired: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  revoked: 'border-border bg-muted text-muted-foreground',
}

// 搜尋框打字時不要每個字元都打一次 API。
const SEARCH_DEBOUNCE_MS = 300

type PendingAction = { kind: 'revoke' | 'delete'; row: AdminLicenseKey }

/**
 * 自部署常見用區網 http:// 直連,非安全來源下 `navigator.clipboard` 是
 * undefined(或權限被拒),退回舊的 textarea + execCommand('copy')。
 */
async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 落到下面的 fallback
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.setAttribute('readonly', '')
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(el)
    return ok
  } catch {
    return false
  }
}

/**
 * 管理員 · 授權金鑰(docs/LICENSE_KEYS.md)。跟 `AdminAppVersionPage` 同款
 * useAuth() admin 判斷 + 自持 list state 樣板。
 *
 * - 產生:一次最多 100 把,有效天數從「使用者啟用」那一刻起算(不是產生當下),
 *   產生完彈窗列出這批新金鑰 + 全部複製,方便直接貼給使用者。
 * - 撤銷:任何非撤銷狀態都可以撤銷(含已啟用的 → 使用者立即失去這把的授權)。
 * - 刪除:只有「從未被啟用」的金鑰才給刪除按鈕;已啟用的 server 會回 409,
 *   要保留紀錄就只能撤銷。
 */
export function AdminLicensesPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [rows, setRows] = useState<AdminLicenseKey[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<AdminLicenseKeyStatusFilter>('all')
  const [queryDraft, setQueryDraft] = useState('')
  const [query, setQuery] = useState('')

  const [countDraft, setCountDraft] = useState('1')
  const [durationDraft, setDurationDraft] = useState('365')
  const [noteDraft, setNoteDraft] = useState('')
  const [generating, setGenerating] = useState(false)
  const [createdKeys, setCreatedKeys] = useState<AdminLicenseKey[] | null>(null)

  const [pending, setPending] = useState<PendingAction | null>(null)
  const [acting, setActing] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  useEffect(() => {
    const id = window.setTimeout(() => setQuery(queryDraft.trim()), SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(id)
  }, [queryDraft])

  const refresh = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      const list = await fetchAdminLicenseKeys(token, {
        status: statusFilter,
        q: query || undefined,
        limit: 1000,
      })
      setRows(list.items)
      setTotal(list.total)
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, isAdmin, statusFilter, query, notifyError])

  useEffect(() => {
    if (!isAdminResolved || !isAdmin) return
    void refresh()
  }, [isAdminResolved, isAdmin, refresh])

  const copyText = useCallback(
    async (text: string) => {
      if (await writeClipboard(text)) {
        toast.success(t('admin.licenses.copied'), t('notice.success'))
      } else {
        toast.error(t('admin.licenses.copyFailed'), t('notice.error'))
      }
    },
    [toast, t],
  )

  const handleGenerate = useCallback(async () => {
    const count = Number(countDraft)
    const durationDays = Number(durationDraft)
    if (
      !Number.isInteger(count) ||
      count < 1 ||
      count > 100 ||
      !Number.isInteger(durationDays) ||
      durationDays < 1 ||
      durationDays > 3650
    ) {
      toast.error(t('admin.licenses.generate.invalid'), t('notice.error'))
      return
    }
    setGenerating(true)
    try {
      const note = noteDraft.trim()
      const result = await createAdminLicenseKeys(token, {
        count,
        duration_days: durationDays,
        ...(note ? { note } : {}),
      })
      setCreatedKeys(result.items)
      setNoteDraft('')
      toast.success(t('admin.licenses.notice.created', { count: result.items.length }), t('notice.success'))
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setGenerating(false)
    }
  }, [token, countDraft, durationDraft, noteDraft, toast, t, notifyError, refresh])

  const handleConfirmPending = useCallback(async () => {
    if (!pending) return
    setActing(true)
    try {
      if (pending.kind === 'revoke') {
        await revokeAdminLicenseKey(token, pending.row.id)
        toast.success(t('admin.licenses.notice.revoked'), t('notice.success'))
      } else {
        await deleteAdminLicenseKey(token, pending.row.id)
        toast.success(t('admin.licenses.notice.deleted'), t('notice.success'))
      }
      setPending(null)
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setActing(false)
    }
  }, [pending, token, toast, t, notifyError, refresh])

  if (!isAdminResolved) {
    return null
  }

  if (!isAdmin) {
    return (
      <Card className="bc-panel">
        <CardContent className="py-6">
          <p className="text-center text-sm text-muted-foreground">{t('admin.users.noPermission')}</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card className="bc-panel">
        <CardContent className="flex items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            <KeyRound className="h-5 w-5 text-primary" />
            <div>
              <h3 className="text-sm font-medium">{t('admin.licenses.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('admin.licenses.subtitle')}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
            <RefreshCcw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('admin.licenses.refresh')}
          </Button>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-3 py-4">
          <h4 className="text-sm font-medium">{t('admin.licenses.generate.title')}</h4>
          <div className="grid gap-3 sm:grid-cols-[120px_140px_minmax(0,1fr)_auto] sm:items-end">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="license-gen-count">
                {t('admin.licenses.generate.count')}
              </label>
              <Input
                id="license-gen-count"
                className="h-9"
                type="number"
                min={1}
                max={100}
                value={countDraft}
                onChange={(e) => setCountDraft(e.target.value)}
                disabled={generating}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="license-gen-duration">
                {t('admin.licenses.generate.duration')}
              </label>
              <Input
                id="license-gen-duration"
                className="h-9"
                type="number"
                min={1}
                max={3650}
                value={durationDraft}
                onChange={(e) => setDurationDraft(e.target.value)}
                disabled={generating}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="license-gen-note">
                {t('admin.licenses.generate.note')}
              </label>
              <Input
                id="license-gen-note"
                className="h-9"
                value={noteDraft}
                onChange={(e) => setNoteDraft(e.target.value)}
                placeholder={t('admin.licenses.generate.notePlaceholder')}
                maxLength={255}
                disabled={generating}
              />
            </div>
            <Button size="sm" className="h-9" onClick={() => void handleGenerate()} disabled={generating}>
              <Plus className="mr-1 h-4 w-4" />
              {generating ? t('common.loading') : t('admin.licenses.generate.submit')}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">{t('admin.licenses.generate.hint')}</p>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-3 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={statusFilter}
              onValueChange={(v) => setStatusFilter(v as AdminLicenseKeyStatusFilter)}
            >
              <SelectTrigger className="h-9 w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_FILTERS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {value === 'all'
                      ? t('admin.licenses.filter.all')
                      : t(`admin.licenses.status.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              className="h-9 w-[220px] bg-muted lg:w-[320px]"
              placeholder={t('admin.licenses.searchPlaceholder')}
              value={queryDraft}
              onChange={(e) => setQueryDraft(e.target.value)}
            />
            <span className="ml-auto text-xs text-muted-foreground">
              {t('admin.licenses.total', { total })}
            </span>
          </div>

          {rows.length === 0 ? (
            <div className="rounded-md border border-dashed py-10 text-center text-sm text-muted-foreground">
              {loading ? t('common.loading') : t('admin.licenses.empty')}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('admin.licenses.col.key')}</TableHead>
                    <TableHead>{t('admin.licenses.col.status')}</TableHead>
                    <TableHead>{t('admin.licenses.col.duration')}</TableHead>
                    <TableHead>{t('admin.licenses.col.note')}</TableHead>
                    <TableHead>{t('admin.licenses.col.redeemedBy')}</TableHead>
                    <TableHead>{t('admin.licenses.col.redeemedAt')}</TableHead>
                    <TableHead>{t('admin.licenses.col.expiresAt')}</TableHead>
                    <TableHead>{t('admin.licenses.col.createdAt')}</TableHead>
                    <TableHead className="text-right">{t('admin.licenses.col.actions')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell className="whitespace-nowrap">
                        <div className="flex items-center gap-1">
                          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{row.key}</code>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            title={t('admin.licenses.copy')}
                            aria-label={t('admin.licenses.copy')}
                            onClick={() => void copyText(row.key)}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                      <TableCell>
                        <span
                          className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] ${STATUS_BADGE_CLASS[row.status]}`}
                        >
                          {t(`admin.licenses.status.${row.status}`)}
                        </span>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {t('admin.licenses.durationDays', { days: row.duration_days })}
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate" title={row.note ?? ''}>
                        {row.note || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{row.redeemed_by_email || '-'}</TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatLicenseDateTime(row.redeemed_at) || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        {formatLicenseDate(row.expires_at) || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatLicenseDateTime(row.created_at) || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right">
                        {row.status !== 'revoked' ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-destructive hover:text-destructive"
                            title={t('admin.licenses.action.revoke')}
                            aria-label={t('admin.licenses.action.revoke')}
                            onClick={() => setPending({ kind: 'revoke', row })}
                          >
                            <Ban className="h-4 w-4" />
                          </Button>
                        ) : null}
                        {row.status === 'unused' ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-destructive hover:text-destructive"
                            title={t('admin.licenses.action.delete')}
                            aria-label={t('admin.licenses.action.delete')}
                            onClick={() => setPending({ kind: 'delete', row })}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!createdKeys} onOpenChange={(open) => !open && setCreatedKeys(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {t('admin.licenses.created.title', { count: createdKeys?.length ?? 0 })}
            </DialogTitle>
            <DialogDescription>{t('admin.licenses.created.desc')}</DialogDescription>
          </DialogHeader>
          {createdKeys ? (
            <div className="space-y-3">
              <div className="max-h-72 overflow-y-auto rounded-md border bg-muted/50 p-3 font-mono text-sm leading-7">
                {createdKeys.map((row) => (
                  <div key={row.id} className="break-all">
                    {row.key}
                  </div>
                ))}
              </div>
              <Button
                variant="secondary"
                className="w-full"
                onClick={() => void copyText(createdKeys.map((row) => row.key).join('\n'))}
              >
                <Copy className="mr-2 h-4 w-4" />
                {t('admin.licenses.copyAll')}
              </Button>
            </div>
          ) : null}
          <DialogFooter>
            <Button onClick={() => setCreatedKeys(null)}>{t('common.done')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!pending}
        title={
          pending?.kind === 'delete'
            ? t('admin.licenses.delete.title')
            : t('admin.licenses.revoke.title')
        }
        description={
          pending
            ? t(
                pending.kind === 'delete'
                  ? 'admin.licenses.delete.confirm'
                  : 'admin.licenses.revoke.confirm',
                { key: pending.row.key },
              )
            : ''
        }
        confirmText={
          pending?.kind === 'delete'
            ? t('admin.licenses.action.delete')
            : t('admin.licenses.action.revoke')
        }
        cancelText={t('common.cancel')}
        loading={acting}
        onCancel={() => setPending(null)}
        onConfirm={() => void handleConfirmPending()}
      />
    </div>
  )
}
