import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'

import type { AnnualReportData } from './data'
import { yearTheme } from './data/yearTheme'
import { ProgressDots } from './widgets/ProgressDots'
import { StoryBackground, StoryProvider, paletteVars, themedPalette, type PaletteKey, type StoryState } from './widgets/storyKit'
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
import { PageFunFacts } from './pages/PageFunFacts'
import { PageAchievements } from './pages/PageAchievements'
import { PageAccounts } from './pages/PageAccounts'
import { PageStockOverview } from './pages/PageStockOverview'
import { PageStockHighlights } from './pages/PageStockHighlights'
import { PagePersona } from './pages/PagePersona'
import { PosterDialog } from './widgets/PosterDialog'

/**
 * 年度回顧主容器 —— 全螢幕限動式(像 IG 限動 / Spotify Wrapped),跟 App 的
 * `annual_report_page.dart` 同一套互動與視覺:
 *
 * - 每一章一組設計好的深色漸層(`widgets/storyKit.tsx`),整份報告的色相依
 *   年度生肖旋轉(`data/yearTheme.ts`),每年都不一樣;
 * - **點畫面右側 70% 下一章、左側 30% 上一章**;觸控左右滑、鍵盤 ← → / 空白鍵、
 *   滾輪(節流)、頂部分段進度條點擊直跳也都可以;
 * - 「錢去哪了」「每月起伏」兩章先猜再揭曉,答案記在這裡,翻回去不會再問;
 * - 年度稱號是最後的壓軸(分享 / 再看一次 / 關閉都在那一章)。
 *
 * 退出:頂部 X / ESC / 壓軸的關閉按鈕 → onClose。
 */
export type AnnualReportPageProps = {
  data: AnnualReportData
  onClose: () => void
  /** 壓軸「分享」按鈕回調,可選;沒給就開內建的海報彈窗 */
  onShare?: () => void
}

/** 點擊落在這些元素上時交給元素自己處理,不翻頁。 */
const INTERACTIVE = 'button, a, input, select, textarea, label, [data-story-interactive]'

export function AnnualReportPage({ data, onClose, onShare }: AnnualReportPageProps) {
  const [page, setPage] = useState(0)
  const [direction, setDirection] = useState<1 | -1>(1)
  const [posterOpen, setPosterOpen] = useState(false)
  const [quizAnswers, setQuizAnswers] = useState<Record<string, number>>({})

  const theme = useMemo(() => yearTheme(data.year), [data.year])
  const story: StoryState = useMemo(
    () => ({
      theme,
      quizAnswers,
      answerQuiz: (id, index) => setQuizAnswers((prev) => ({ ...prev, [id]: index })),
    }),
    [theme, quizAnswers],
  )

  const handleShare = useCallback(() => {
    if (onShare) onShare()
    else setPosterOpen(true)
  }, [onShare])

  // 股票頁的幣別切換(總覽 / 亮點共用同一個選擇)
  const [stockCurrencyIndex, setStockCurrencyIndex] = useState(0)

  // 章節清單依實際資料動態組成:沒有帳戶 / 標籤 / 股票 / 冷知識就不出現對應章。
  const pages = useMemo(() => {
    const list: { key: string; palette: PaletteKey; node: React.ReactNode }[] = [
      { key: 'welcome', palette: 'cover', node: <PageWelcome data={data} /> },
      { key: 'overview', palette: 'records', node: <PageOverview data={data} /> },
      { key: 'yoy', palette: 'yoy', node: <PageYoY data={data} /> },
      { key: 'categories', palette: 'categories', node: <PageCategories data={data} /> },
      { key: 'monthly', palette: 'months', node: <PageMonthlyTrend data={data} /> },
    ]
    if (data.topAccounts.length > 0) list.push({ key: 'accounts', palette: 'money', node: <PageAccounts data={data} /> })
    list.push(
      { key: 'hours', palette: 'habits', node: <PageHours data={data} /> },
      { key: 'weekday', palette: 'daily', node: <PageWeekday data={data} /> },
      { key: 'extremes', palette: 'moments', node: <PageExtremes data={data} /> },
      { key: 'habits', palette: 'firstRecord', node: <PageHabits data={data} /> },
    )
    if (data.funFacts.length > 0) list.push({ key: 'facts', palette: 'funFacts', node: <PageFunFacts data={data} /> })
    if (data.topTags.length > 0) list.push({ key: 'tags', palette: 'tags', node: <PageTags data={data} /> })
    if (data.stock) {
      list.push(
        {
          key: 'stock-overview',
          palette: 'stockOverview',
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
          palette: 'stockHighlights',
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
      { key: 'achievements', palette: 'achievements', node: <PageAchievements data={data} /> },
      {
        key: 'persona',
        palette: 'finale',
        node: <PagePersona data={data} onShare={handleShare} onRestart={() => setPage(0)} onClose={onClose} />,
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

  // 鍵盤翻頁 — 海報彈窗打開時停用(避免滑掉底層 page)
  useEffect(() => {
    if (posterOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === ' ') {
        e.preventDefault()
        goNext()
      } else if (e.key === 'ArrowLeft') goPrev()
      else if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [goNext, goPrev, onClose, posterOpen])

  // 滾輪翻頁(節流,380ms 一次)— 海報彈窗打開時停用
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

  // 點畫面翻頁:右側 70% 下一章、左側 30% 上一章。觸控左右滑也可以,
  // 滑完的那一下 click 要吃掉,不然會多翻一頁。
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const swallowClick = useRef(false)
  const onStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (swallowClick.current) {
      swallowClick.current = false
      return
    }
    if ((e.target as HTMLElement).closest(INTERACTIVE)) return
    const rect = e.currentTarget.getBoundingClientRect()
    if (e.clientX - rect.left < rect.width * 0.3) goPrev()
    else goNext()
  }
  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0]
    touchStart.current = { x: t.clientX, y: t.clientY }
  }
  const onTouchEnd = (e: React.TouchEvent) => {
    const start = touchStart.current
    touchStart.current = null
    if (!start) return
    const t = e.changedTouches[0]
    const dx = t.clientX - start.x
    const dy = t.clientY - start.y
    if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      swallowClick.current = true
      if (dx < 0) goNext()
      else goPrev()
    }
  }

  const current = pages[Math.min(page, total - 1)]
  const palette = themedPalette(current.palette, theme)
  // 背景層的疊放順序:每換一次章 +1,新的背景永遠蓋在舊的上面淡入
  const bgSeq = useRef({ key: current.key, n: 0 })
  if (bgSeq.current.key !== current.key) bgSeq.current = { key: current.key, n: bgSeq.current.n + 1 }

  return (
    <StoryProvider value={story}>
      <div className="fixed inset-0 z-[100] overflow-hidden bg-[#0F0C09] text-white">
        {/* 頂部分段進度條 + 關閉 */}
        <div className="absolute left-0 right-0 top-0 z-50 flex items-center gap-3 px-4 py-4 sm:px-8">
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

        {/* 背景另外一層交叉淡入:內容換章時(mode="wait" 先淡出再淡入)背景不會閃黑 */}
        <div className="absolute inset-0 isolate">
          <AnimatePresence initial={false}>
            <motion.div
              key={current.key}
              className="absolute inset-0"
              style={{ zIndex: bgSeq.current.n }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 1, transition: { duration: 0.7 } }}
              transition={{ duration: 0.6, ease: 'easeOut' }}
            >
              <StoryBackground palette={palette} />
            </motion.div>
          </AnimatePresence>
        </div>

        {/* 翻頁內容 */}
        <div
          className="absolute inset-0 cursor-pointer select-none"
          onClick={onStageClick}
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={current.key}
              custom={direction}
              variants={pageVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.45, ease: [0.4, 0, 0.2, 1] }}
              className="absolute inset-0 flex items-center justify-center"
              style={paletteVars(palette)}
            >
              {current.node}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* 桌機版的浮動翻頁按鈕(手機版點畫面就好) */}
        {page > 0 && <NavButton position="left" onClick={goPrev} />}
        {page < total - 1 && <NavButton position="right" onClick={goNext} />}

        {/* 海報彈窗 */}
        <PosterDialog data={data} open={posterOpen} onClose={() => setPosterOpen(false)} />
      </div>
    </StoryProvider>
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
      className={`absolute top-1/2 z-50 hidden -translate-y-1/2 rounded-full bg-white/10 p-3 backdrop-blur-sm transition hover:scale-110 hover:bg-white/20 sm:block ${
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
        {position === 'right' ? <polyline points="9 18 15 12 9 6" /> : <polyline points="15 18 9 12 15 6" />}
      </motion.svg>
    </button>
  )
}
