import type { AppSection, NavItem } from '@beecount/web-features'
import { GlassSegmented, useT } from '@beecount/ui'

export function GlassNavigation({ items, activeSection, onNavigate, className }: {
  items: NavItem[]
  activeSection: AppSection
  onNavigate: (section: AppSection) => void
  className?: string
}) {
  const t = useT()
  return <GlassSegmented semantics="navigation" label={t('nav.group.bookkeeping')}
    value={activeSection} onValueChange={onNavigate}
    className={`bc-glass-nested min-w-0 ${className || ''}`}
    options={items.map((item) => ({ value: item.key, label: t(item.labelKey) }))}
  />
}
