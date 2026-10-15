import { useEffect, useState } from 'react'
import { CircleHelp, RefreshCw, Trophy, UserRound } from 'lucide-react'
import {
  FARM_BOARDS,
  fetchFarmLeaderboard,
  resolveApiUrl,
  type FarmBoard,
  type FarmLeaderboard,
} from '@beecount/api-client'
import {
  Button, Card, CardContent, Select, SelectContent, SelectItem, SelectTrigger,
  SelectValue, GlassSegmented, Dialog, DialogContent, DialogTitle, DialogDescription,
  DialogTrigger, useLocale, useT,
} from '@beecount/ui'

import { useAuth } from '../../context/AuthContext'

/** Web deliberately exposes only public weekly standings, with no farm scene or game actions. */
export function FarmLeaderboardPage() {
  const t = useT()
  const { locale } = useLocale()
  const { token } = useAuth()
  const [board, setBoard] = useState<FarmBoard>('bookkeeping')
  const [week, setWeek] = useState<string>()
  const [data, setData] = useState<FarmLeaderboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [reload, setReload] = useState(0)
  const number = (value: number) => new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value)
  const weeks = [...new Set(data ? [data.week, ...data.availableWeeks] : [])].sort().reverse()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setFailed(false)
    void fetchFarmLeaderboard(token, board, week).then((next) => {
      if (!cancelled) setData(next)
    }).catch(() => {
      if (!cancelled) setFailed(true)
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    // Switching quickly must not let an earlier board overwrite the current one.
    return () => { cancelled = true }
  }, [token, board, week, reload])

  const ready = !loading && !failed && data !== null && data.board === board && (!week || data.week === week)
  const delta = board === 'bookkeeping' && data?.myBookkeepingScore != null
    ? data.myBookkeepingScore - data.averageScore : null

  return (
    <section className="space-y-5" aria-labelledby="farm-leaderboard-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="farm-leaderboard-title" className="flex items-center gap-2 text-xl font-semibold">
            <Trophy className="h-5 w-5 text-primary" aria-hidden="true" />
            {t('nav.farmLeaderboard')}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{t('farmBoard.subtitle')}</p>
        </div>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => setReload((n) => n + 1)}>
          <RefreshCw className="mr-2 h-4 w-4" aria-hidden="true" />{t('farmBoard.refresh')}
        </Button>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex w-full items-center gap-2 sm:w-auto">
          <GlassSegmented
            value={board}
            onValueChange={setBoard}
            options={FARM_BOARDS.map((key) => ({ value: key, label: t(`farmBoard.${key}`) }))}
            label={t('farmBoard.chooseBoard')}
            className="min-w-0 flex-1 sm:min-w-[360px]"
          />
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="outline" size="icon" className="shrink-0 rounded-full"
                aria-label={`${t(`farmBoard.${board}`)} · ${t('farmBoard.calculation')}`}>
                <CircleHelp className="h-5 w-5" aria-hidden="true" />
              </Button>
            </DialogTrigger>
            <DialogContent className="flex max-h-[85dvh] flex-col overflow-hidden">
              <DialogTitle className="shrink-0 pr-6">{t(`farmBoard.${board}`)} · {t('farmBoard.calculation')}</DialogTitle>
              <DialogDescription className="min-h-0 overflow-y-auto whitespace-pre-line pr-1 leading-relaxed">
                {t(`farmBoard.help.${board}`)}{'\n\n'}{t('farmBoard.help.common')}
              </DialogDescription>
            </DialogContent>
          </Dialog>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="farm-board-week" className="text-sm text-muted-foreground">{t('farmBoard.week')}</label>
          <Select value={week ?? '__latest__'} onValueChange={(value) => setWeek(value === '__latest__' ? undefined : value)}>
            <SelectTrigger id="farm-board-week" className="w-[190px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__latest__">{t('farmBoard.latest')}</SelectItem>
              {weeks.map((value) => <SelectItem key={value} value={value}>{t('farmBoard.weekOf', { week: value })}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      <div aria-live="polite" aria-busy={loading}>
        {loading && <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">{t('farmBoard.loading')}</CardContent></Card>}
        {failed && <Card><CardContent className="space-y-3 py-10 text-center">
          <p role="alert" className="text-sm text-muted-foreground">{t('farmBoard.error')}</p>
          <Button variant="outline" onClick={() => setReload((n) => n + 1)}>{t('farmBoard.retry')}</Button>
        </CardContent></Card>}
        {ready && <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{t('farmBoard.weekOf', { week: data.week })}</p>
          <div className={`grid gap-3 ${board === 'bookkeeping' ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
            <Summary label={t('farmBoard.players')} value={number(data.playerCount)} />
            {board === 'bookkeeping' && <Summary label={t('farmBoard.average')} value={number(data.averageScore)} />}
            <Summary label={t('farmBoard.myValue')} value={data.myValue === null ? t('farmBoard.notParticipating') : number(data.myValue)}>
              {delta !== null && <p className="mt-1 text-xs text-muted-foreground">{t('farmBoard.difference', { value: `${delta > 0 ? '+' : ''}${number(delta)}` })}</p>}
            </Summary>
          </div>
          {data.playerCount < 5 && <p className="rounded-lg border border-border bg-muted/50 px-4 py-3 text-sm text-muted-foreground">{t('farmBoard.smallGroup')}</p>}
          {data.items.length === 0 ? <Card><CardContent className="py-10 text-center">
            <p className="font-medium">{t('farmBoard.empty')}</p>
            <p className="mt-1 text-sm text-muted-foreground">{t('farmBoard.emptyHint')}</p>
          </CardContent></Card> : <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">{t(`farmBoard.${board}`)} · {data.week}</caption>
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-4 py-3">{t('farmBoard.rank')}</th>
                    <th scope="col" className="px-4 py-3">{t('farmBoard.player')}</th>
                    <th scope="col" className="px-4 py-3 text-right">{t('farmBoard.value')}</th>
                    <th scope="col" className="px-4 py-3 text-right">{t('farmBoard.reward')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.items.map((row) => (
                    <tr key={`${row.rank}-${row.userId ?? 'anonymous'}`} className={row.isMe ? 'bg-primary/5' : ''}>
                      <td className="px-4 py-3 font-medium tabular-nums">{row.rank}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          {/* An anonymous row never loads an identifying avatar, even if a server accidentally includes one. */}
                          {row.userId && row.avatarUrl ? <img src={resolveApiUrl(row.avatarUrl) ?? undefined} alt="" referrerPolicy="no-referrer" className="h-8 w-8 shrink-0 rounded-full object-cover" /> : <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted"><UserRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" /></span>}
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="max-w-[260px] break-words font-medium">{row.userId ? (row.displayName?.trim() || t('farmBoard.unnamed')) : t('farmBoard.anonymous')}</span>
                              {row.thiefUntil && <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground" title={t('farmBoard.thiefMarkBody')}>🦝 {t('farmBoard.thiefMark')}</span>}
                              {row.isMe && <span className="rounded-full border border-primary/30 bg-primary/15 px-2 py-0.5 text-xs font-bold text-primary-strong">{t('farmBoard.me')}</span>}
                            </div>
                            {row.title && <p className="mt-0.5 text-xs text-muted-foreground">{row.title}</p>}
                          </div>
                        </div>
                      </td>
                      <td className={`whitespace-nowrap px-4 py-3 text-right tabular-nums ${row.isMe ? 'font-bold text-primary-strong' : ''}`}>{number(row.value)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">{t('farmBoard.coins', { value: number(row.rewardCoins) })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>}
          <p className="text-xs text-muted-foreground">{t('farmBoard.privacy')}</p>
        </div>}
      </div>
    </section>
  )
}

function Summary({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return <Card><CardContent className="py-4">
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p>
    {children}
  </CardContent></Card>
}
