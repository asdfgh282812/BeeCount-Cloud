import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

import type { AnnualReportData } from './data'
import { ProgressDots } from './widgets/ProgressDots'
import { PageWelcome } from './pages/PageWelcome'
import { PageOverview } from './pages/PageOverview'
import { PageYoY } from './pages/PageYoY'
import { PageMonthlyTrend } from './pages/PageMonthlyTrend'
import { PageCategories } from './pages/PageCategories'
import { PageHours } from './pages/PageHours'
import { PageWeekday } from './pages/PageWeekday'
import { PageExtremes } from './pages/PageExtremes'
import { PageHabits } from './pages/PageHabits'
import { PageTags } from './pages/PageTags'
import { PageAchievements } from './pages/PageAchievements'
import { PageOutro } from './pages/PageOutro'
import { PageAccounts } from './pages/PageAccounts'
import { PageStockOverview } from './pages/PageStockOverview'
import { PageStockHighlights } from './pages/PageStockHighlights'
import { PagePersona } from './pages/PagePersona'
import { PosterDialog } from './widgets/PosterDialog'

/**
 * 年度报告主容器 — 全屏沉浸式 carousel,故事性回顾(基礎 9 屏 + 依資料出現的
 * 帳戶 / 標籤 / 股票 / 年度稱號,沒有對應資料的頁就不出現)。
 *
 * 翻页方式:
 * - 鼠标点击右下「下一页」/ 左下「上一页」浮动按钮
 * - 键盘 ← → 方向键
 * - 滚轮上下(节流)
 * - 触屏滑动手势(后期)
 * - 顶部进度条点击直跳
 *
 * 退出:
 * - 顶部 X 按钮 / ESC 键 / 末页关闭按钮 → onClose
 *
 * 不会阻塞主任务:动画用 GPU(transform + opacity),不触发 layout。
 */
export type AnnualReportPageProps = {
  data: AnnualReportData
  onClose: () => void
  /** 末页「分享」按钮回调,可选 */
  onShare?: () => void
}

export function AnnualReportPage({ data, onClose, onShare }: AnnualReportPageProps) {
  const [page, setPage] = useState(0)
  const [direction, setDirection] = useState<1 | -1>(1)
  const [posterOpen, setPosterOpen] = useState(false)

  const handleShare = useCallback(() => {
    if (onShare) onShare()
    else setPosterOpen(true)
  }, [onShare])

  // 股票頁的幣別切換(總覽 / 亮點共用同一個選擇)
  const [stockCurrencyIndex, setStockCurrencyIndex] = useState(0)

  // 頁面清單依實際資料動態組成:沒有帳戶 / 標籤 / 股票活動就不出現對應頁。
  const pages = useMemo(() => {
    const list: { key: string; node: React.ReactNode }[] = [
      { key: 'welcome', node: <PageWelcome data={data} /> },
      { key: 'overview', node: <PageOverview data={data} /> },
      { key: 'yoy', node: <PageYoY data={data} /> },
      { key: 'monthly', node: <PageMonthlyTrend data={data} /> },
      { key: 'categories', node: <PageCategories data={data} /> },
    ]
    if (data.topAccounts.length > 0) list.push({ key: 'accounts', node: <PageAccounts data={data} /> })
    list.push(
      { key: 'hours', node: <PageHours data={data} /> },
      { key: 'weekday', node: <PageWeekday data={data} /> },
      { key: 'extremes', node: <PageExtremes data={data} /> },
      { key: 'habits', node: <PageHabits data={data} /> },
    )
    if (data.topTags.length > 0) list.push({ key: 'tags', node: <PageTags data={data} /> })
    if (data.stock) {
      list.push(
        {
          key: 'stock-overview',
          node: (
            <PageStockOverview
              data={data}
              currencyIndex={stockCurrencyIndex}
              onCurrencyChange={setStockCurrencyIndex}
            />
          ),
        },
        {
          key: 'stock-highlights',
          node: (
            <PageStockHighlights
              data={data}
              currencyIndex={stockCurrencyIndex}
              onCurrencyChange={setStockCurrencyIndex}
            />
          ),
        },
      )
    }
    list.push(
      { key: 'persona', node: <PagePersona data={data} /> },
      { key: 'achievements', node: <PageAchievements data={data} /> },
      {
        key: 'outro',
        node: (
          <PageOutro data={data} onShare={handleShare} onRestart={() => setPage(0)} onClose={onClose} />
        ),
      },
    )
    return list
  }, [data, handleShare, onClose, stockCurrencyIndex])

  const total = pages.length
  const goNext = useCallback(() => {
    setDirection(1)
    setPage((p) => Math.min(p + 1, total - 1))
  }, [total])
  const goPrev = useCallback(() => {
    setDirection(-1)
    setPage((p) => Math.max(p - 1, 0))
  }, [])
  const jumpTo = useCallback(
    (idx: number) => {
      setDirection(idx >= page ? 1 : -1)
      setPage(idx)
    },
    [page],
  )

  // 键盘翻页 — 海报弹窗打开时禁用(避免滑掉底层 page)
  useEffect(() => {
    if (posterOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') goNext()
      else if (e.key === 'ArrowLeft') goPrev()
      else if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [goNext, goPrev, onClose, posterOpen])

  // 滚轮翻页(节流,380ms 一次)— 海报弹窗打开时禁用
  useEffect(() => {
    if (posterOpen) return
    let lock = false
    const handler = (e: WheelEvent) => {
      if (lock) return
      if (Math.abs(e.deltaY) < 30) return
      lock = true
      if (e.deltaY > 0) goNext()
      else goPrev()
      setTimeout(() => {
        lock = false
      }, 380)
    }
    window.addEventListener('wheel', handler, { passive: true })
    return () => window.removeEventListener('wheel', handler)
  }, [goNext, goPrev, posterOpen])

  return (
    <div className="fixed inset-0 z-[100] overflow-hidden bg-[#0F0C09] text-white">
      {/* 顶部进度条 + 关闭 */}
      <div className="absolute left-0 right-0 top-0 z-50 flex items-center gap-3 px-6 py-4 sm:px-8">
        <ProgressDots total={total} current={page} onJump={jumpTo} className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="rounded-full bg-white/10 p-2 text-white transition hover:bg-white/20"
          aria-label="Close"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      {/* 翻页内容 */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={pages[page].key}
          custom={direction}
          variants={pageVariants}
          initial="enter"
          animate="center"
          exit="exit"
          transition={{ duration: 0.45, ease: [0.4, 0, 0.2, 1] }}
          className="absolute inset-0 flex items-center justify-center"
        >
          {pages[page].node}
        </motion.div>
      </AnimatePresence>

      {/* 浮动翻页按钮(末页隐藏 next) */}
      {page > 0 && (
        <NavButton position="left" onClick={goPrev} />
      )}
      {page < total - 1 && (
        <NavButton position="right" onClick={goNext} />
      )}

      {/* 海报弹窗 */}
      <PosterDialog data={data} open={posterOpen} onClose={() => setPosterOpen(false)} />
    </div>
  )
}

const pageVariants = {
  enter: (dir: number) => ({ opacity: 0, x: dir * 40 }),
  center: { opacity: 1, x: 0 },
  exit: (dir: number) => ({ opacity: 0, x: -dir * 40 }),
}

function NavButton({ position, onClick }: { position: 'left' | 'right'; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`absolute top-1/2 z-50 -translate-y-1/2 rounded-full bg-white/10 p-3 backdrop-blur-sm transition hover:bg-white/20 hover:scale-110 ${
        position === 'left' ? 'left-4 sm:left-8' : 'right-4 sm:right-8'
      }`}
      aria-label={position === 'left' ? 'Previous' : 'Next'}
    >
      <motion.svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-white"
        animate={position === 'right' ? { x: [0, 4, 0] } : { x: [0, -4, 0] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
      >
        {position === 'right' ? (
          <polyline points="9 18 15 12 9 6" />
        ) : (
          <polyline points="15 18 9 12 15 6" />
        )}
      </motion.svg>
    </button>
  )
}
