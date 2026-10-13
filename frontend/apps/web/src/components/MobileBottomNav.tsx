import { Home, LayoutGrid, Receipt, Tag, Wallet } from 'lucide-react'

import type { AppSection } from '@beecount/web-features'
import { useT } from '@beecount/ui'

interface Props {
  activeSection: AppSection
  onNavigate: (section: AppSection) => void
}

/**
 * 移动端固定底部 tab bar,5 个主视图:
 *   首页 / 交易 / 资产 / 分类 / 标签
 *
 * 更多视图(预算 / 账本 / 设置 / 管理员 / 更新日志 / 退出登录)都在顶部头像
 * 下拉(AvatarDropdown)里,跟这里重复反而占位,所以不再维护"更多"tab。
 *
 * 仅在 <md 显示;桌面端 layout 自带侧栏 + 顶部 nav,不需要这个。
 */
export function MobileBottomNav({ activeSection, onNavigate }: Props) {
  const t = useT()

  const tabs: Array<{ section: AppSection; label: string; Icon: typeof Home }> = [
    { section: 'overview', label: t('nav.overview'), Icon: Home },
    { section: 'transactions', label: t('nav.transactions'), Icon: Receipt },
    { section: 'accounts', label: t('nav.accounts'), Icon: Wallet },
    { section: 'categories', label: t('nav.categories'), Icon: LayoutGrid },
    { section: 'tags', label: t('nav.tags'), Icon: Tag }
  ]

  return (
    <nav
      className="bc-glass fixed inset-x-4 bottom-3 z-40 rounded-full md:hidden"
      style={{ bottom: 'calc(env(safe-area-inset-bottom) + 12px)' }}
    >
      <div className="relative mx-auto flex max-w-3xl items-stretch p-1">
        <span
          className="bc-glass-thumb pointer-events-none absolute bottom-1 top-1 rounded-full"
          style={{
            width: 'calc((100% - 8px) / 5)',
            left: '4px',
            transform: `translateX(${Math.max(0, tabs.findIndex((tab) => tab.section === activeSection)) * 100}%)`,
            opacity: tabs.some((tab) => tab.section === activeSection) ? 1 : 0,
          }}
          aria-hidden
        />
        {tabs.map(({ section, label, Icon }) => {
          const active = activeSection === section
          return (
            <button
              key={section}
              type="button"
              aria-current={active ? 'page' : undefined}
              onClick={() => onNavigate(section)}
              className={`relative flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[11px] transition-colors ${
                active ? 'text-primary' : 'text-muted-foreground'
              }`}
            >
              <Icon className={`h-5 w-5 ${active ? 'text-primary' : ''}`} />
              <span className="font-medium">{label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
