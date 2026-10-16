import { useCallback, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Button, Card, CardContent, CardHeader, CardTitle,
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue, useT, useToast,
} from '@beecount/ui'
import { type AIConfig, patchProfileMe } from '@beecount/api-client'

import { CapabilityBindingCard } from '../../components/ai-config/CapabilityBindingCard'
import { ProvidersCard } from '../../components/ai-config/ProvidersCard'
import { useAuth } from '../../context/AuthContext'
import { localizeError } from '../../i18n/errors'
import { mergeAiConfig, normalizeAiConfig } from '../../lib/aiConfigMerge'

/** 同一正式入口的主頁與管理子頁，沿用整包合併儲存及 profile_change 同步。 */
export function SettingsAiPage({ management = false }: { management?: boolean }) {
  const t = useT()
  const toast = useToast()
  const { token, profileMe, refreshProfile } = useAuth()
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const [consentOpen, setConsentOpen] = useState(false)
  const config = profileMe?.ai_config ? normalizeAiConfig(profileMe.ai_config) : null

  const saveAiConfig = useCallback(async (next: AIConfig) => {
    if (savingRef.current || !profileMe) return false
    savingRef.current = true
    setSaving(true)
    try {
      await patchProfileMe(token, { ai_config: next })
      await refreshProfile()
      toast.success(t('ai.editor.saved'))
      return true
    } catch (err) {
      toast.error(localizeError(err, t))
      return false
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }, [token, profileMe, refreshProfile, toast, t])
  const patch = (next: Partial<AIConfig>) => saveAiConfig(mergeAiConfig(config, next))

  const preference = (label: string, value: string, options: string[], onChange: (v: string) => void) => (
    <div className="flex flex-wrap items-center justify-between gap-3 py-3">
      <span className="text-sm font-medium">{t(`ai.preferences.${label}`)}</span>
      <Select value={value} disabled={saving} onValueChange={onChange}>
        <SelectTrigger className="w-full sm:w-64" aria-label={t(`ai.preferences.${label}`) as string}><SelectValue placeholder={t('ai.preferences.strategy.unsupported') as string} /></SelectTrigger>
        <SelectContent>{options.map((v) => <SelectItem key={v} value={v}>{label === 'silence' ? `${Number(v) / 1000}s` : t(`ai.preferences.${label}.${v}`)}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  )

  if (!profileMe) return <Card className="bc-panel"><CardContent className="space-y-3 p-5">
    <p role="status">{t('ai.preferences.loading')}</p>
    <Button variant="outline" onClick={() => void refreshProfile().catch((err) => toast.error(localizeError(err, t)))}>{t('ai.preferences.retry')}</Button>
  </CardContent></Card>

  return <div className="mx-auto max-w-3xl space-y-4" data-testid={management ? 'ai-management' : 'smart-billing'}>
    {management ? <>
      <Link className="inline-block text-sm text-primary underline" to="/app/settings/ai">{t('ai.preferences.back')}</Link>
      <h2 className="text-lg font-semibold">{t('ai.management.title')}</h2>
      <ProvidersCard config={config} saving={saving} onSave={saveAiConfig} />
      <CapabilityBindingCard config={config} saving={saving} onSave={saveAiConfig} />
      <Card className="bc-panel"><CardHeader><CardTitle>{t('ai.preferences.strategy')}</CardTitle></CardHeader>
        <CardContent>{preference('strategy', config?.strategy || 'cloud_first', ['cloud_first', 'cloud_only'],
          (strategy) => void patch({ strategy }))}</CardContent></Card>
      <PromptEditor key={config?.custom_prompt ?? ''} value={config?.custom_prompt ?? ''} saving={saving}
        onSave={(custom_prompt) => patch({ custom_prompt })} />
    </> : <>
      <Card className="bc-panel"><CardHeader><CardTitle>{t('ai.preferences.title')}</CardTitle></CardHeader>
        <CardContent className="divide-y divide-border/60">
          <label className="flex cursor-pointer items-center justify-between gap-4 py-3">
            <span><span className="block text-sm font-medium">{t('ai.preferences.enabled')}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{t('ai.preferences.enabledHint')}</span></span>
            <input className="h-5 w-5 accent-primary" type="checkbox" role="switch" checked={!!config?.bill_extraction_enabled}
              disabled={saving} onChange={(e) => e.target.checked ? setConsentOpen(true) : void patch({ bill_extraction_enabled: false })} />
          </label>
          {preference('project', config?.project_assign_mode ?? 'none', ['none', 'ask', 'aiDecide'],
            (v) => void patch({ project_assign_mode: v as AIConfig['project_assign_mode'] }))}
          {preference('tags', config?.tag_assign_mode ?? 'aiDecideOrNew', ['none', 'ask', 'aiDecideExisting', 'aiDecideOrNew'],
            (v) => void patch({ tag_assign_mode: v as AIConfig['tag_assign_mode'] }))}
          <p className="py-3 text-xs text-muted-foreground">{t('ai.preferences.assignmentHint')}</p>
        </CardContent></Card>
      <Link className="bc-panel block rounded-2xl border border-border p-5 transition-colors hover:bg-muted/30" to="/app/settings/ai/manage">
        <span className="block font-semibold">{t('ai.management.title')} →</span>
        <span className="mt-1 block text-sm text-muted-foreground">{t('ai.management.desc')}</span>
      </Link>
      <Card className="bc-panel"><CardHeader><CardTitle>{t('ai.preferences.voice')}</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {preference('trigger', config?.voice_trigger_mode ?? 'auto', ['auto', 'hold_to_talk'],
            (v) => void patch({ voice_trigger_mode: v as AIConfig['voice_trigger_mode'] }))}
          {(config?.voice_trigger_mode ?? 'auto') === 'auto' && preference('silence', String(config?.voice_silence_timeout_ms ?? 1500),
            Array.from({ length: 36 }, (_, i) => String(500 + i * 100)),
            (v) => void patch({ voice_silence_timeout_ms: Number(v) }))}
          <p className="text-xs text-muted-foreground">{t('ai.preferences.nativeHint')}</p>
        </CardContent></Card>
    </>}
    {!config && <p className="text-sm text-muted-foreground">{t('ai.empty')}</p>}
    <Dialog open={consentOpen} onOpenChange={(open) => !saving && setConsentOpen(open)}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto">
        <DialogHeader><DialogTitle>{t('ai.preferences.consentTitle')}</DialogTitle></DialogHeader>
        <p className="whitespace-pre-line text-sm leading-relaxed">{t('ai.preferences.consentBody')}</p>
        <DialogFooter><Button variant="outline" disabled={saving} onClick={() => setConsentOpen(false)}>{t('common.cancel')}</Button>
          <Button disabled={saving} onClick={async () => {
            if (await patch({ bill_extraction_enabled: true })) setConsentOpen(false)
          }}>{t('ai.preferences.consentAgree')}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
}

function PromptEditor({ value, saving, onSave }: { value: string; saving: boolean; onSave: (value: string) => Promise<boolean> }) {
  const t = useT()
  const [draft, setDraft] = useState(value)
  return <Card className="bc-panel"><CardHeader><CardTitle>{t('ai.preferences.prompt')}</CardTitle></CardHeader>
    <CardContent><form className="space-y-3" onSubmit={(e) => { e.preventDefault(); void onSave(draft) }}>
      <textarea className="min-h-48 w-full rounded-lg border border-border bg-background p-3 text-sm"
        aria-label={t('ai.preferences.prompt') as string} value={draft} disabled={saving} onChange={(e) => setDraft(e.target.value)} />
      <p className="text-xs text-muted-foreground">{t('ai.preferences.promptHint')}</p>
      <Button type="submit" disabled={saving || draft === value}>{t('common.save')}</Button>
    </form></CardContent></Card>
}
