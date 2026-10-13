import type { ButtonHTMLAttributes, HTMLAttributes } from 'react'

import { cn } from '../lib/cn'

export function Tabs({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('space-y-4', className)} {...props} />
}

/** 相容舊呼叫介面；目前頁面的互斥選項統一使用 GlassSegmented。 */
export function TabsList({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'bc-glass-control inline-flex h-auto w-full flex-wrap items-center gap-2 rounded-md p-1 text-muted-foreground',
        className
      )}
      {...props}
    />
  )
}

type TabsTriggerProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean
}

export function TabsTrigger({ className, active = false, ...props }: TabsTriggerProps) {
  return (
    <button
      aria-pressed={active}
      className={cn(
        'inline-flex items-center rounded-sm px-3 py-1.5 text-sm transition-colors',
        active
          ? 'bc-glass-segment text-foreground font-semibold'
          : 'font-medium text-muted-foreground hover:bg-accent/40 hover:text-foreground',
        className
      )}
      {...props}
    />
  )
}

export function TabsContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('mt-2', className)} {...props} />
}
