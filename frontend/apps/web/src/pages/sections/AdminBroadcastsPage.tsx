import { useCallback, useEffect, useState } from 'react'
import { Megaphone, RefreshCcw, Send, Undo2 } from 'lucide-react'

import {
  createAdminBroadcast,
  fetchAdminBroadcastRecipientCount,
  fetchAdminBroadcasts,
  retractAdminBroadcast,
  type AdminBroadcast,
} from '@beecount/api-client'
import {
  Button,
  Card,
  CardContent,
  Input,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Textarea,
  useT,
  useToast,
} from '@beecount/ui'
import { ConfirmDialog } from '@beecount/web-features'

import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'
import { formatLicenseDateTime } from '../../lib/license'

const TITLE_MAX = 255
const BODY_MAX = 2000

/**
 * 管理員 · 系統公告。跟 `AdminLicensesPage` 同款 useAuth() admin 判斷 +
 * 自持 list state 樣板。
 *
 * 發送 = server 對所有啟用中的使用者各寫一筆 system 通知,web 鈴鐺與 App
 * 通知中心都拿得到;App 輪詢到新未讀會跳本機系統通知(前景最多延遲 60 秒,
 * 背景/被殺掉要等下次打開 App 才補跳,沒有接 FCM/APNs)。
 *
 * 送出前先抓一次收件人數放進確認框,避免手滑直接發給所有人。撤回只刪
 * 通知中心裡那一筆,已經跳出去的系統通知收不回來。
 */
export function AdminBroadcastsPage() {
  const t = useT()
  const toast = useToast()
  const { token, isAdmin, isAdminResolved } = useAuth()

  const [rows, setRows] = useState<AdminBroadcast[]>([])
  const [loading, setLoading] = useState(false)

  const [titleDraft, setTitleDraft] = useState('')
  const [bodyDraft, setBodyDraft] = useState('')
  const [preparing, setPreparing] = useState(false)
  const [recipientCount, setRecipientCount] = useState<number | null>(null)
  const [sending, setSending] = useState(false)

  const [retractTarget, setRetractTarget] = useState<AdminBroadcast | null>(null)
  const [retracting, setRetracting] = useState(false)

  const notifyError = useCallback(
    (err: unknown) => toast.error(localizeError(err, t), t('notice.error')),
    [toast, t],
  )

  const refresh = useCallback(async () => {
    if (!isAdmin) return
    setLoading(true)
    try {
      const list = await fetchAdminBroadcasts(token)
      setRows(list.items)
    } catch (err) {
      notifyError(err)
    } finally {
      setLoading(false)
    }
  }, [token, isAdmin, notifyError])

  useEffect(() => {
    if (!isAdminResolved || !isAdmin) return
    void refresh()
  }, [isAdminResolved, isAdmin, refresh])

  const handlePrepareSend = useCallback(async () => {
    if (!titleDraft.trim()) {
      toast.error(t('admin.broadcasts.compose.titleRequired'), t('notice.error'))
      return
    }
    setPreparing(true)
    try {
      const { count } = await fetchAdminBroadcastRecipientCount(token)
      setRecipientCount(count)
    } catch (err) {
      notifyError(err)
    } finally {
      setPreparing(false)
    }
  }, [token, titleDraft, toast, t, notifyError])

  const handleConfirmSend = useCallback(async () => {
    setSending(true)
    try {
      const body = bodyDraft.trim()
      const result = await createAdminBroadcast(token, {
        title: titleDraft.trim(),
        ...(body ? { body } : {}),
      })
      setRecipientCount(null)
      setTitleDraft('')
      setBodyDraft('')
      toast.success(
        t('admin.broadcasts.notice.sent', { count: result.recipient_count }),
        t('notice.success'),
      )
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setSending(false)
    }
  }, [token, titleDraft, bodyDraft, toast, t, notifyError, refresh])

  const handleConfirmRetract = useCallback(async () => {
    if (!retractTarget) return
    setRetracting(true)
    try {
      await retractAdminBroadcast(token, retractTarget.id)
      setRetractTarget(null)
      toast.success(t('admin.broadcasts.notice.retracted'), t('notice.success'))
      void refresh()
    } catch (err) {
      notifyError(err)
    } finally {
      setRetracting(false)
    }
  }, [retractTarget, token, toast, t, notifyError, refresh])

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
            <Megaphone className="h-5 w-5 text-primary" />
            <div>
              <h3 className="text-sm font-medium">{t('admin.broadcasts.title')}</h3>
              <p className="text-xs text-muted-foreground">{t('admin.broadcasts.subtitle')}</p>
            </div>
          </div>
          <Button size="sm" variant="outline" onClick={() => void refresh()} disabled={loading}>
            <RefreshCcw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('admin.broadcasts.refresh')}
          </Button>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-3 py-4">
          <h4 className="text-sm font-medium">{t('admin.broadcasts.compose.title')}</h4>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="broadcast-title">
              {t('admin.broadcasts.compose.titleLabel')}
            </label>
            <Input
              id="broadcast-title"
              className="h-9"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              placeholder={t('admin.broadcasts.compose.titlePlaceholder')}
              maxLength={TITLE_MAX}
              disabled={sending}
            />
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <label className="text-xs text-muted-foreground" htmlFor="broadcast-body">
                {t('admin.broadcasts.compose.bodyLabel')}
              </label>
              <span className="text-[11px] text-muted-foreground">
                {bodyDraft.length}/{BODY_MAX}
              </span>
            </div>
            <Textarea
              id="broadcast-body"
              rows={4}
              value={bodyDraft}
              onChange={(e) => setBodyDraft(e.target.value)}
              placeholder={t('admin.broadcasts.compose.bodyPlaceholder')}
              maxLength={BODY_MAX}
              disabled={sending}
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">{t('admin.broadcasts.compose.hint')}</p>
            <Button
              size="sm"
              className="h-9"
              onClick={() => void handlePrepareSend()}
              disabled={preparing || sending || !titleDraft.trim()}
            >
              <Send className="mr-1.5 h-4 w-4" />
              {preparing ? t('common.loading') : t('admin.broadcasts.compose.submit')}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="bc-panel">
        <CardContent className="space-y-3 py-4">
          <h4 className="text-sm font-medium">{t('admin.broadcasts.history.title')}</h4>
          {rows.length === 0 ? (
            <div className="rounded-md border border-dashed py-10 text-center text-sm text-muted-foreground">
              {loading ? t('common.loading') : t('admin.broadcasts.history.empty')}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('admin.broadcasts.col.content')}</TableHead>
                    <TableHead>{t('admin.broadcasts.col.sentAt')}</TableHead>
                    <TableHead>{t('admin.broadcasts.col.sender')}</TableHead>
                    <TableHead>{t('admin.broadcasts.col.read')}</TableHead>
                    <TableHead>{t('admin.broadcasts.col.status')}</TableHead>
                    <TableHead className="text-right">{t('admin.broadcasts.col.actions')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.id} className={row.retracted_at ? 'opacity-60' : undefined}>
                      <TableCell className="min-w-[220px] max-w-[420px]">
                        <div className="font-medium">{row.title}</div>
                        {row.body ? (
                          <div className="mt-0.5 line-clamp-2 whitespace-pre-line text-xs text-muted-foreground">
                            {row.body}
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                        {formatLicenseDateTime(row.created_at) || '-'}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{row.created_by_email || '-'}</TableCell>
                      <TableCell className="whitespace-nowrap">
                        {row.retracted_at ? '-' : `${row.read_count} / ${row.recipient_count}`}
                      </TableCell>
                      <TableCell>
                        {row.retracted_at ? (
                          <span
                            className="inline-flex items-center whitespace-nowrap rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
                            title={formatLicenseDateTime(row.retracted_at)}
                          >
                            {t('admin.broadcasts.status.retracted')}
                          </span>
                        ) : (
                          <span className="inline-flex items-center whitespace-nowrap rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
                            {t('admin.broadcasts.status.sent')}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-right">
                        {!row.retracted_at ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-destructive hover:text-destructive"
                            title={t('admin.broadcasts.action.retract')}
                            aria-label={t('admin.broadcasts.action.retract')}
                            onClick={() => setRetractTarget(row)}
                          >
                            <Undo2 className="h-4 w-4" />
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

      <ConfirmDialog
        open={recipientCount !== null}
        title={t('admin.broadcasts.send.title')}
        description={t('admin.broadcasts.send.confirm', {
          count: recipientCount ?? 0,
          title: titleDraft.trim(),
        })}
        confirmText={t('admin.broadcasts.compose.submit')}
        cancelText={t('common.cancel')}
        loading={sending}
        onCancel={() => setRecipientCount(null)}
        onConfirm={() => void handleConfirmSend()}
      />

      <ConfirmDialog
        open={!!retractTarget}
        title={t('admin.broadcasts.retract.title')}
        description={
          retractTarget ? t('admin.broadcasts.retract.confirm', { title: retractTarget.title }) : ''
        }
        confirmText={t('admin.broadcasts.action.retract')}
        cancelText={t('common.cancel')}
        loading={retracting}
        onCancel={() => setRetractTarget(null)}
        onConfirm={() => void handleConfirmRetract()}
      />
    </div>
  )
}
