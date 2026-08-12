import { useMemo, useState } from 'react'
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  cn,
  useLocale,
  useT
} from '@beecount/ui'

export type DatePickerProps = {
  /** `YYYY-MM-DD`(跟原生 `<input type="date">` 同格式,純日曆日期,不含時間/
   *  時區)。空字串代表「未設定」。 */
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  /** 空值時觸發按鈕顯示的文字,預設用 `common.unset`。 */
  placeholder?: string
  /** 是否顯示「清除」按鈕把值清空(給選填欄位用,行為對齊原生 input 本來就能清空的能力)。 */
  clearable?: boolean
}

function parseDateValue(value: string): Date | null {
  if (!value) return null
  const [y, m, d] = value.split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

function formatDateValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
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

/**
 * 純日期版的 {@link DateTimePicker}(不含時分),取代原生 `<input type="date">`。
 * 值一律是純曆法日期字串(不經過 `Date` 建構子解析字串,避免時區位移造成
 * 少一天/多一天的 off-by-one),跟原生 `<input type="date">` 語意完全對齊。
 */
export function DatePicker({ value, onChange, disabled, placeholder, clearable }: DatePickerProps) {
  const t = useT()
  const { locale } = useLocale()
  const [open, setOpen] = useState(false)
  const committed = useMemo(() => parseDateValue(value), [value])
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
            weekday: 'short'
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
            <DialogTitle>{t('common.selectDate')}</DialogTitle>
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
                    onClick={() => setDraft(d)}
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
                onChange(formatDateValue(draft))
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
