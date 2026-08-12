import { useMemo, useState } from 'react'
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  cn,
  useLocale,
  useT
} from '@beecount/ui'

export type DateTimePickerProps = {
  /** `YYYY-MM-DDTHH:mm`(跟原生 `<input type="datetime-local">` 同格式,本地時間,
   *  不含秒/時區),沿用呼叫方既有的 `isoToDatetimeLocal`/`datetimeLocalToIso`
   *  轉換,方便無痛替換舊的原生 input。空字串代表「未設定」。 */
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  /** 空值時觸發按鈕顯示的文字,預設用 `common.unset`。 */
  placeholder?: string
  /** 是否顯示「清除」按鈕把值清空(給選填欄位用,行為對齊原生 input 本來就能清空的能力)。 */
  clearable?: boolean
}

function parseLocalValue(value: string): Date | null {
  if (!value) return null
  const [datePart, timePart] = value.split('T')
  const [y, m, d] = (datePart || '').split('-').map(Number)
  const [hh, mm] = (timePart || '').split(':').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d, Number.isFinite(hh) ? hh : 0, Number.isFinite(mm) ? mm : 0)
}

function formatLocalValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

function buildMonthGrid(viewMonth: Date): Date[] {
  const year = viewMonth.getFullYear()
  const month = viewMonth.getMonth()
  const startOffset = new Date(year, month, 1).getDay()
  const gridStart = new Date(year, month, 1 - startOffset)
  return Array.from(
    { length: 42 },
    (_, i) => new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i)
  )
}

const HOURS = Array.from({ length: 24 }, (_, i) => i)
const MINUTES = Array.from({ length: 60 }, (_, i) => i)

/**
 * 自製日期+時間選擇器,取代原生 `<input type="datetime-local">`(2026-08 使用
 * 者回饋:原生控件桌機上要手動打字輸入年月日、行動裝置上各家瀏覽器的原生
 * 選擇器體驗又醜又不一致)。改成 Dialog 內嵌月曆網格(點選日期)+ 下拉選單
 * 選時分,跟既有的 CategoryPickerDialog/TagPickerDialog 走同一套「trigger
 * 按鈕 + Dialog 內容」慣例,行動裝置上點按操作跟桌機一致,不需要另外處理
 * RWD。
 */
export function DateTimePicker({ value, onChange, disabled, placeholder, clearable }: DateTimePickerProps) {
  const t = useT()
  const { locale } = useLocale()
  const [open, setOpen] = useState(false)
  const committed = useMemo(() => parseLocalValue(value), [value])
  const [draft, setDraft] = useState(committed ?? new Date())
  const [viewMonth, setViewMonth] = useState(() => {
    const base = committed ?? new Date()
    return new Date(base.getFullYear(), base.getMonth(), 1)
  })

  const handleOpenChange = (next: boolean) => {
    if (next) {
      const base = committed ?? new Date()
      setDraft(base)
      setViewMonth(new Date(base.getFullYear(), base.getMonth(), 1))
    }
    setOpen(next)
  }

  const grid = useMemo(() => buildMonthGrid(viewMonth), [viewMonth])

  const weekdayLabels = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(locale, { weekday: 'narrow' })
    // 2023-01-01 是週日,用這週(日~六)當基準只是為了取本地化的星期簡稱。
    return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2023, 0, 1 + i)))
  }, [locale])

  const monthLabel = useMemo(
    () => new Intl.DateTimeFormat(locale, { year: 'numeric', month: 'long' }).format(viewMonth),
    [locale, viewMonth]
  )

  const triggerLabel = useMemo(
    () =>
      committed
        ? new Intl.DateTimeFormat(locale, {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            weekday: 'short',
            hour: '2-digit',
            minute: '2-digit'
          }).format(committed)
        : placeholder || t('common.unset'),
    [locale, committed, placeholder, t]
  )

  const today = new Date()

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => handleOpenChange(true)}
        className="flex h-10 w-full items-center gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm shadow-sm transition-colors hover:bg-accent/40 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <CalendarIcon className="h-4 w-4 shrink-0 opacity-60" />
        <span className={cn('flex-1 truncate', !committed && 'text-muted-foreground')}>{triggerLabel}</span>
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('common.selectDateTime')}</DialogTitle>
          </DialogHeader>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => setViewMonth((m) => new Date(m.getFullYear(), m.getMonth() - 1, 1))}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span className="text-sm font-medium">{monthLabel}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => setViewMonth((m) => new Date(m.getFullYear(), m.getMonth() + 1, 1))}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>

            <div className="grid grid-cols-7 place-items-center gap-y-1 text-xs text-muted-foreground">
              {weekdayLabels.map((label, i) => (
                <span key={i}>{label}</span>
              ))}
            </div>
            <div className="grid grid-cols-7 place-items-center gap-y-1">
              {grid.map((d) => {
                const inMonth = d.getMonth() === viewMonth.getMonth()
                const selected = isSameDay(d, draft)
                const isToday = isSameDay(d, today)
                return (
                  <button
                    key={d.toISOString()}
                    type="button"
                    onClick={() =>
                      setDraft(
                        new Date(d.getFullYear(), d.getMonth(), d.getDate(), draft.getHours(), draft.getMinutes())
                      )
                    }
                    className={cn(
                      'flex h-9 w-9 items-center justify-center rounded-full text-sm transition-colors',
                      inMonth ? 'text-foreground' : 'text-muted-foreground/40',
                      selected
                        ? 'bg-primary font-semibold text-primary-foreground'
                        : isToday
                          ? 'ring-1 ring-inset ring-primary/60 hover:bg-accent/40'
                          : 'hover:bg-accent/40'
                    )}
                  >
                    {d.getDate()}
                  </button>
                )
              })}
            </div>

            <div className="flex items-center gap-2 border-t border-border/60 pt-3">
              <Select
                value={String(draft.getHours())}
                onValueChange={(v) => {
                  const h = Number(v)
                  setDraft((prev) => new Date(prev.getFullYear(), prev.getMonth(), prev.getDate(), h, prev.getMinutes()))
                }}
              >
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {HOURS.map((h) => (
                    <SelectItem key={h} value={String(h)}>
                      {String(h).padStart(2, '0')}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-muted-foreground">:</span>
              <Select
                value={String(draft.getMinutes())}
                onValueChange={(v) => {
                  const mnt = Number(v)
                  setDraft(
                    (prev) => new Date(prev.getFullYear(), prev.getMonth(), prev.getDate(), prev.getHours(), mnt)
                  )
                }}
              >
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {MINUTES.map((mnt) => (
                    <SelectItem key={mnt} value={String(mnt)}>
                      {String(mnt).padStart(2, '0')}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={() => setDraft(new Date())}>
                {t('common.now')}
              </Button>
            </div>
          </div>

          <DialogFooter>
            {clearable ? (
              <Button
                type="button"
                variant="ghost"
                className="mr-auto"
                onClick={() => {
                  onChange('')
                  setOpen(false)
                }}
              >
                {t('common.clear')}
              </Button>
            ) : null}
            <Button type="button" variant="ghost" onClick={() => handleOpenChange(false)}>
              {t('dialog.cancel')}
            </Button>
            <Button
              type="button"
              onClick={() => {
                onChange(formatLocalValue(draft))
                setOpen(false)
              }}
            >
              {t('dialog.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
