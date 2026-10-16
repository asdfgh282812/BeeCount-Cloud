import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchProfileMe, fetchWorkspaceAccounts, patchProfileMe, type WorkspaceAccount } from '@beecount/api-client'
import { Button, Card, CardContent, CardHeader, CardTitle, Input, useT } from '@beecount/ui'
import { useAuth } from '../../context/AuthContext'
import { effectiveReminders, isRecord, reminderOverrides, updateReminders, type ReminderSettings } from '../../lib/cardReminderSettings'

export function SettingsRemindersPage() {
  const t = useT()
  const { token, profileMe, refreshProfile } = useAuth()
  const [params] = useSearchParams()
  const [accounts, setAccounts] = useState<WorkspaceAccount[] | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setAccounts(null)
    setError(false)
    void fetchWorkspaceAccounts(token).then(
      (a) => { if (active) setAccounts(a) },
      () => { if (active) setError(true) },
    )
    return () => { active = false }
  }, [token, revision])
  const requested = params.get('account')
  const selected = accounts?.find((a) => a.id === requested)
  const account = selected?.parent_account_id ? accounts?.find((a) => a.id === selected.parent_account_id) : selected
  const accountId = account?.id
  const data = profileMe?.notification_settings ?? {}
  const overridden = !!accountId && isRecord(reminderOverrides(data)[accountId])
  const value = effectiveReminders(data, accountId)
  const editable = !requested || overridden
  const save = async (patch: Partial<ReminderSettings> = {}, reset = false) => {
    if (saving || (requested && !accountId)) return
    setSaving(true)
    setError(false)
    try {
      // Read before replacing the JSON bundle to preserve unrelated phone preferences.
      const latest = (await fetchProfileMe(token)).notification_settings ?? {}
      await patchProfileMe(token, { notification_settings: updateReminders(latest,
        { ...effectiveReminders(latest, accountId), ...patch }, accountId, reset) })
      await refreshProfile()
    } catch { setError(true) }
    finally { setSaving(false) }
  }
  const toggle = (key: 'cc_reminder_enabled' | 'cc_billing_reminder_enabled' | 'cc_due_reminder_enabled', label: string) =>
    <label className="flex min-h-12 items-center justify-between gap-4 py-2">
      <span>{t(label)}</span><input type="checkbox" className="h-5 w-5 shrink-0 accent-primary"
        checked={value[key]} onChange={(e) => void save({ [key]: e.target.checked })} />
    </label>
  const days = (key: 'cc_reminder_days' | 'cc_due_reminder_maxdays', options: number[], label: string) =>
    <label className="flex flex-wrap items-center justify-between gap-3 py-2">
      <span>{t(label)}</span><select className="rounded-lg border border-border bg-background p-2" value={value[key]}
        onChange={(e) => void save({ [key]: Number(e.target.value) })}>
        {[...new Set([...options, value[key]])].sort((a, b) => a - b).map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  return <div className="mx-auto max-w-3xl space-y-4" data-testid="card-reminders">
    {requested && <Link className="text-sm text-primary underline" to="/app/settings/reminders">{t('reminders.back')}</Link>}
    <Card className="bc-panel"><CardHeader><CardTitle>{account?.name ?? t('reminders.title')}</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">{t(requested ? 'reminders.cardHint' : 'reminders.defaultsHint')}</p>
        {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-destructive">{t('reminders.error')}
          <Button variant="outline" onClick={() => { setRevision((r) => r + 1); void refreshProfile() }}>{t('reminders.retry')}</Button></div>}
        {!accounts || !profileMe ? <p role="status">{t('reminders.loading')}</p> : requested && !account ? <p>{t('reminders.missing')}</p> : <>
          {requested && <label className="flex min-h-12 items-center justify-between gap-4 border-b pb-4">
            <span>{t('reminders.inherit')}</span><input type="checkbox" className="h-5 w-5 shrink-0 accent-primary"
              checked={!overridden} disabled={saving} onChange={(e) => void save({}, e.target.checked)} />
          </label>}
          {account && (!account.billing_day || !account.payment_due_day) && <p className="text-sm text-muted-foreground">{t('reminders.needsDates')}</p>}
          {value.cc_settings_version === 0 && <p className="text-sm text-muted-foreground">{t('reminders.legacy')}</p>}
          <fieldset disabled={!editable || saving} className="space-y-2 disabled:opacity-50">
            <label className="flex flex-wrap items-center justify-between gap-3 py-2"><span>{t('reminders.time')}</span>
              <Input className="w-36" type="time" value={`${String(value.cc_reminder_hour).padStart(2, '0')}:${String(value.cc_reminder_minute).padStart(2, '0')}`}
                onChange={(e) => { if (!/^\d{2}:\d{2}$/.test(e.target.value)) return; const [hour, minute] = e.target.value.split(':').map(Number); void save({ cc_reminder_hour: hour, cc_reminder_minute: minute }) }} />
            </label>
            {toggle('cc_reminder_enabled', 'reminders.advance')}
            {value.cc_reminder_enabled && days('cc_reminder_days', [1, 3, 5, 7], 'reminders.advanceDays')}
            {toggle('cc_billing_reminder_enabled', 'reminders.closing')}
            {toggle('cc_due_reminder_enabled', 'reminders.due')}
            {value.cc_due_reminder_enabled && days('cc_due_reminder_maxdays', [1, 2, 3, 5, 7, 10], 'reminders.maxDays')}
          </fieldset>
          {!requested && <div className="space-y-2 border-t pt-4"><h2 className="font-medium">{t('reminders.cards')}</h2>
            {accounts.filter((a) => !a.parent_account_id && (a.account_type === 'credit_card' ||
              (a.account_type === 'account_group' && (a.billing_day || accounts.some((c) => c.parent_account_id === a.id && c.account_type === 'credit_card')))))
              .map((a) => <Link key={a.id} to={`/app/settings/reminders?account=${encodeURIComponent(a.id)}`}
                className="flex flex-wrap justify-between gap-2 rounded-xl border border-border p-4 hover:bg-muted/30">
                <span className="break-all">{a.name}</span><span className="text-sm text-muted-foreground">{t(isRecord(reminderOverrides(data)[a.id]) ? 'reminders.custom' : 'reminders.inherited')}</span>
              </Link>)}
            {!accounts.some((a) => a.account_type === 'credit_card' || (a.account_type === 'account_group' && a.billing_day)) && <p>{t('reminders.empty')}</p>}
          </div>}
          <p className="text-sm text-muted-foreground">{t('reminders.delivery')}</p>
        </>}
      </CardContent></Card>
  </div>
}
