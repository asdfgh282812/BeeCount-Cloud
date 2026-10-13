import { forwardRef, type ButtonHTMLAttributes } from 'react'

import { cn } from '../lib/cn'

/** 日期、維度等互斥篩選。選取語意與點擊行為由呼叫端保留。 */
export const GlassChoice = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(
  function GlassChoice({ className, type = 'button', ...props }, ref) {
    return <button ref={ref} type={type} className={cn(
      'bc-glass-control bc-glass-choice rounded-full px-3 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 disabled:pointer-events-none',
      className,
    )} {...props} />
  },
)
