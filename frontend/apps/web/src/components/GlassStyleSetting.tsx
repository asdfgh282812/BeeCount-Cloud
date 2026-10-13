import { Layers } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, useT } from '@beecount/ui'

import { resolveGlassStyle, type GlassStyle } from '../lib/glassStyle'

type Props = {
  value: GlassStyle
  disabled?: boolean
  onChange: (value: GlassStyle) => void
}

export function GlassStyleSetting({ value, disabled, onChange }: Props) {
  const t = useT()
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <div className="rounded-xl border border-border/60 bg-gradient-to-br from-primary/35 to-harvest/35 p-1" aria-hidden>
          <div className="bc-glass flex h-9 w-9 items-center justify-center rounded-lg">
            <Layers className="h-5 w-5" />
          </div>
        </div>
        <div>
          <p className="text-sm font-medium">{t('profile.glass.title')}</p>
          <p className="text-xs text-muted-foreground">{t('profile.glass.desc')}</p>
        </div>
      </div>
      <Select value={value} disabled={disabled} onValueChange={(next) => onChange(resolveGlassStyle(next))}>
        <SelectTrigger className="w-36" aria-label={t('profile.glass.title')}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="clear">{t('profile.glass.clear')}</SelectItem>
          <SelectItem value="balanced">{t('profile.glass.balanced')}</SelectItem>
          <SelectItem value="tinted">{t('profile.glass.tinted')}</SelectItem>
        </SelectContent>
      </Select>
    </div>
  )
}
