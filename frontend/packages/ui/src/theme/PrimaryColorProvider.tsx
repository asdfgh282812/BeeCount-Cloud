import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react'

import {
  applyFestivalOverride,
  applyPrimaryColor,
  DEFAULT_PRIMARY_COLOR,
  initialPrimaryColor,
  persistPrimaryColor
} from './primary-color-script'

type PrimaryColorContextValue = {
  color: string
  setColor: (hex: string) => void
  reset: () => void
  /** Server 下发的主题色（mobile 推上来）。无条件覆盖本地色；web 用户点
   *  picker 只是临时切换，下一次 server 推 / loadProfile 会再覆盖。
   *  单向：mobile → server → web，反向不同步。 */
  applyServerColor: (hex: string | null | undefined) => void
  /** 節日主題色(節日 P3):非 null 時畫面暫時用它,`color` 仍是使用者的顏色。 */
  festivalColor: string | null
  /** 設定 / 取消節日主題色。只改 CSS 變數,不寫 localStorage、不影響 `color`。 */
  setFestivalColor: (hex: string | null) => void
}

const PrimaryColorContext = createContext<PrimaryColorContextValue | null>(null)

export function PrimaryColorProvider({ children }: PropsWithChildren) {
  const [color, setColorState] = useState<string>(() => initialPrimaryColor())
  const [festivalColor, setFestivalColorState] = useState<string | null>(null)
  // setColor / applyServerColor 是穩定的 callback,用 ref 讀目前的節日色:
  // 節日當天使用者改色或 server 推色,只更新 `color`,畫面仍維持節日色。
  const festivalRef = useRef<string | null>(null)

  // 组件挂载时先把 localStorage 里的色应用一次，防止首帧用默认色闪烁。
  useEffect(() => {
    applyPrimaryColor(color)
    // 只跑一次，之后走 setColor 显式触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const setColor = useCallback((hex: string) => {
    const cleaned = hex.trim()
    if (!/^#[0-9a-fA-F]{6}$/.test(cleaned)) return
    setColorState(cleaned)
    if (!festivalRef.current) applyPrimaryColor(cleaned)
    // 本地持久化仅为了下次刷新前不闪回；下一次 server 推 / loadProfile
    // 会覆盖它。web 不会回推 mobile。
    persistPrimaryColor(cleaned)
  }, [])

  const reset = useCallback(() => {
    setColor(DEFAULT_PRIMARY_COLOR)
  }, [setColor])

  const applyServerColor = useCallback((hex: string | null | undefined) => {
    if (!hex) return
    const cleaned = hex.trim()
    if (!/^#[0-9a-fA-F]{6}$/.test(cleaned)) {
      console.warn('[theme] applyServerColor invalid hex', cleaned)
      return
    }
    // 每次 server 推下来都应用：mobile 改色 → web 无条件跟上。
    // Web 本地 setColor 是"临时切换"，下一次 mobile 推送依然覆盖。
    console.info('[theme] applyServerColor apply', cleaned)
    setColorState(cleaned)
    if (!festivalRef.current) applyPrimaryColor(cleaned)
    // 同步写 localStorage：保证页面下次加载前（loadProfile 还没返回）
    // 也是 server 值，避免短暂闪回旧色。
    persistPrimaryColor(cleaned)
  }, [])

  const setFestivalColor = useCallback((hex: string | null) => {
    const cleaned = hex?.trim() ?? null
    const next = cleaned && /^#[0-9a-fA-F]{6}$/.test(cleaned) ? cleaned : null
    festivalRef.current = next
    setFestivalColorState(next)
  }, [])

  // 節日色出現 / 消失時重新套用:有節日色用節日色,取消後換回使用者的顏色。
  // 首次掛載(festivalColor = null)由上面那個 effect 處理,這裡跳過避免重複。
  const festivalMounted = useRef(false)
  useEffect(() => {
    if (!festivalMounted.current) {
      festivalMounted.current = true
      return
    }
    applyFestivalOverride(festivalColor, color)
    // 只在節日色變化時觸發;color 變化由 setColor / applyServerColor 自己處理
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [festivalColor])

  const value = useMemo(
    () => ({ color, setColor, reset, applyServerColor, festivalColor, setFestivalColor }),
    [color, setColor, reset, applyServerColor, festivalColor, setFestivalColor]
  )

  return (
    <PrimaryColorContext.Provider value={value}>{children}</PrimaryColorContext.Provider>
  )
}

export function usePrimaryColor(): PrimaryColorContextValue {
  const ctx = useContext(PrimaryColorContext)
  if (!ctx) {
    throw new Error('usePrimaryColor must be used within PrimaryColorProvider')
  }
  return ctx
}
