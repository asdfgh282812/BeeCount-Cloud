import { motion } from 'framer-motion'

/**
 * 頂部分段進度條(限動式):看過的滿格、目前這章從 0 填滿、還沒看的空著。
 * 點任一段直跳到那一章。不自動翻頁——財務數字需要時間看,填滿只是節奏感。
 */
export type ProgressDotsProps = {
  total: number
  current: number
  onJump: (index: number) => void
  className?: string
}

export function ProgressDots({ total, current, onJump, className = '' }: ProgressDotsProps) {
  return (
    <div className={`flex items-center gap-1 ${className}`}>
      {Array.from({ length: total }).map((_, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onJump(i)}
          className="flex h-4 flex-1 cursor-pointer items-center"
          aria-label={`Page ${i + 1}`}
          aria-current={i === current ? 'step' : undefined}
        >
          <span className="block h-1 w-full overflow-hidden rounded-full bg-white/25">
            {i < current ? (
              <span className="block h-full w-full rounded-full bg-white" />
            ) : i === current ? (
              <motion.span
                key={`fill-${current}`}
                className="block h-full rounded-full bg-white"
                initial={{ width: '0%' }}
                animate={{ width: '100%' }}
                transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
              />
            ) : null}
          </span>
        </button>
      ))}
    </div>
  )
}
