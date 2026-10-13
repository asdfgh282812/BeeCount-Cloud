import { GlassSegmented } from '@beecount/ui'
import { useT } from '@beecount/ui'

import type { DetailScope } from '../../lib/txDialogEvents'

interface Props {
  value: DetailScope
  onChange: (next: DetailScope) => void
  className?: string
}

/**
 * 详情弹窗顶部的"全部账本 / 当前账本"切换。复用 i18n 现有键 shell.allLedgers
 * + home.scope.current,不再引入新文案。被 AccountDetail / CategoryDetail /
 * TagDetail 三处弹窗共用,所以放在 components/dialogs/。
 */
export function DetailScopeToggle({ value, onChange, className }: Props) {
  const t = useT()
  const options: Array<{ key: DetailScope; label: string }> = [
    { key: 'all', label: t('shell.allLedgers') },
    { key: 'current', label: t('home.scope.current') },
  ]
  return (
    <GlassSegmented semantics="tabs" label={t('shell.allLedgers')} value={value}
      className={`h-8 w-auto min-w-[180px] text-xs ${className || ''}`}
      options={options.map((opt) => ({ value: opt.key, label: opt.label }))}
      onValueChange={onChange}
    />
  )
}
