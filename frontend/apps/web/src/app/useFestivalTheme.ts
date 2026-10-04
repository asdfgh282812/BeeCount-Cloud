import { useEffect, useMemo, useState } from 'react'

import { fetchHolidays, type ProfileMe } from '@beecount/api-client'
import { usePrimaryColor } from '@beecount/ui'
import {
  festivalThemeFor,
  holidaySettingsFromAppearance,
  localDayKey,
  msUntilNextLocalMidnight,
  toHolidayLite,
  type HolidayLite,
} from '@beecount/web-features'

/** dev 專用:localStorage 有這個 key(`YYYY-MM-DD`)時把「今天」當成那一天,驗證節日主題用。 */
const DEBUG_TODAY_KEY = 'beecount.debug-holiday-today'

function todayKey(): string {
  if (import.meta.env.DEV) {
    try {
      const override = window.localStorage.getItem(DEBUG_TODAY_KEY)
      if (override && /^\d{4}-\d{2}-\d{2}$/.test(override)) return override
    } catch {
      // localStorage 不可用就用真的今天
    }
  }
  return localDayKey(new Date().toISOString())
}

/**
 * 節日主題(節日 P3,docs/HOLIDAYS_SD.md「節日主題」)。
 *
 * profile 的 appearance 有 `holiday_theme_enabled`(App 推上來,預設開)且今天是
 * 主要國家的節日時,把 Web 主題色暫時換成節日色(`setFestivalColor`,不寫
 * localStorage),回傳節日給頁首顯示徽章。跨午夜、分頁切回前景時重新判斷。
 * 抓節日資料失敗就當沒有節日,不影響其他畫面。
 */
export function useFestivalTheme(
  token: string,
  profileMe: ProfileMe | null,
  locale: string,
): HolidayLite | null {
  const { setFestivalColor } = usePrimaryColor()
  const [dayKey, setDayKey] = useState(todayKey)
  const [loaded, setLoaded] = useState<{ key: string; entries: HolidayLite[] } | null>(null)

  const appearance = profileMe?.appearance
  const settings = profileMe ? holidaySettingsFromAppearance(appearance, locale) : null
  const themeOn = !!settings && settings.enabled && (appearance?.holiday_theme_enabled ?? true)
  const primary = settings?.primary ?? null
  const year = Number(dayKey.slice(0, 4))
  const fetchKey = themeOn && primary ? `${primary}|${year}` : null

  useEffect(() => {
    if (!token || !fetchKey || !primary) return
    let cancelled = false
    fetchHolidays(token, { countries: [primary], years: [year] })
      .then((res) => {
        if (!cancelled) setLoaded({ key: fetchKey, entries: res.entries.map(toHolidayLite) })
      })
      .catch(() => {
        if (!cancelled) setLoaded({ key: fetchKey, entries: [] })
      })
    return () => {
      cancelled = true
    }
  }, [token, fetchKey, primary, year])

  useEffect(() => {
    const refresh = () => setDayKey(todayKey())
    let timer = 0
    const schedule = () => {
      timer = window.setTimeout(() => {
        refresh()
        schedule()
      }, msUntilNextLocalMidnight() + 1000)
    }
    schedule()
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  const festival = useMemo(() => {
    if (!fetchKey || !primary || loaded?.key !== fetchKey) return null
    return festivalThemeFor(loaded.entries, primary, dayKey)
  }, [fetchKey, primary, loaded, dayKey])

  const festivalColor = festival?.color ?? null
  useEffect(() => {
    setFestivalColor(festivalColor)
  }, [festivalColor, setFestivalColor])

  // 登出(AppShell 卸載)時把顏色還給使用者
  useEffect(() => () => setFestivalColor(null), [setFestivalColor])

  return festival
}
