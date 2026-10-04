/**
 * 拉取 + 聚合 — 高层入口。UI 层只用这个,内部走 api-client + aggregate。
 *
 * 一年内事务量典型 1-3K 笔,分页拉取(api-client `fetchWorkspaceTransactions`
 * 默认 limit 200),全部读完再聚合。读取失败不返 null,而是 throw,UI 层
 * try / catch 给出错提示。
 */
import {
  fetchHolidays,
  fetchProfileMe,
  fetchStockAnnual,
  fetchWorkspaceTransactions,
  type StockAnnualCurrency,
  type StockAnnualReport,
  type StockAnnualSell,
  type StockAnnualSymbol,
  type WorkspaceTransaction,
} from '@beecount/api-client'

import { aggregate } from './aggregate'
import { holidaySettingsFromAppearance, resolveHolidays, toHolidayLite } from './holidays'
import type {
  AnnualReportData,
  HolidayLite,
  StockAnnual,
  StockCurrencySummary,
  StockStyle,
  StockSellHighlight,
  StockSymbolHighlight,
  TransactionLite,
} from './types'

const PAGE_SIZE = 500

export async function fetchAnnualReportData(
  token: string,
  ledger: { id: string; name: string; currency: string },
  year: number,
  /** Web 介面語系(節日設定沒推過時用來推主要國家) */
  locale = 'zh-TW',
): Promise<AnnualReportData> {
  // 时间窗口:本年 + 去年(用于 YoY 对比),取年初到年末(独占下界)
  const thisYearFrom = `${year}-01-01T00:00:00.000Z`
  const thisYearTo = `${year + 1}-01-01T00:00:00.000Z`
  const prevYearFrom = `${year - 1}-01-01T00:00:00.000Z`
  const prevYearTo = `${year}-01-01T00:00:00.000Z`

  const [thisYear, prevYear, stock, holidays] = await Promise.all([
    fetchAllPaged(token, ledger.id, thisYearFrom, thisYearTo),
    fetchAllPaged(token, ledger.id, prevYearFrom, prevYearTo),
    // 股票是加分項:讀取失敗不能讓整份年度報告失敗,視為沒有股票頁。
    fetchStockAnnual(token, year).then(toStockAnnual).catch(() => null),
    // 節日同理:失敗(舊版 server 404 等)就當沒有節日。
    fetchReportHolidays(token, year, locale).catch(() => ({ holidays: {}, primary: null })),
  ])

  // §2.10 Phase 5:tx_type 新增 'adjustment'(餘額調整,語意化端點產生,不是
  // 真实收支活动)——年度报告只关心 expense/income/transfer 叙事,把它整个
  // 排除在外(而不是像 aggregate.ts 现有逻辑那样只在 sum 时隐式跳过),避免
  // 它混进"总笔数"之类没有显式按 txType 过滤的统计口径。
  return aggregate({
    thisYearTxs: thisYear.filter(isReportableTx).map(toLite),
    prevYearTxs: prevYear.filter(isReportableTx).map(toLite),
    year,
    ledger,
    stock,
    holidays: holidays.holidays,
    holidayPrimary: holidays.primary,
  })
}

/** 依 profile appearance 的節日設定(App 同步上來的)抓該年度節日並去重。 */
async function fetchReportHolidays(
  token: string,
  year: number,
  locale: string,
): Promise<{ holidays: Record<string, HolidayLite[]>; primary: string | null }> {
  const profile = await fetchProfileMe(token).catch(() => null)
  const settings = holidaySettingsFromAppearance(profile?.appearance, locale)
  if (!settings.enabled) return { holidays: {}, primary: null }
  const res = await fetchHolidays(token, { countries: settings.regions, years: [year] })
  return {
    holidays: resolveHolidays(res.entries.map(toHolidayLite), settings.regions, settings.primary),
    primary: settings.primary,
  }
}

function isReportableTx(
  t: WorkspaceTransaction,
): t is WorkspaceTransaction & { tx_type: 'expense' | 'income' | 'transfer' } {
  return t.tx_type !== 'adjustment'
}

async function fetchAllPaged(
  token: string,
  ledgerId: string,
  dateFrom: string,
  dateTo: string,
): Promise<WorkspaceTransaction[]> {
  const all: WorkspaceTransaction[] = []
  let offset = 0
  while (true) {
    const page = await fetchWorkspaceTransactions(token, {
      ledgerId,
      dateFrom,
      dateTo,
      limit: PAGE_SIZE,
      offset,
    })
    all.push(...page.items)
    if (page.items.length < PAGE_SIZE) break
    offset += PAGE_SIZE
    // 安全保护:超过 1 万笔(年度极少见)就停,避免无限循环
    if (offset >= 10000) break
  }
  return all
}

function toLite(t: WorkspaceTransaction & { tx_type: 'expense' | 'income' | 'transfer' }): TransactionLite {
  return {
    id: t.id,
    txType: t.tx_type,
    amount: t.amount,
    happenedAt: t.happened_at,
    note: t.note,
    categoryName: t.category_name,
    categoryKind: t.category_kind,
    accountName: t.account_name,
    tagsList: t.tags_list ?? [],
  }
}

const STYLE_MAP: Record<StockAnnualCurrency['style_tag'], StockStyle> = {
  active_trader: 'activeTrader',
  dividend_hunter: 'dividendHunter',
  long_term_holder: 'longTermHolder',
  swing_trader: 'swingTrader',
  beginner: 'beginner',
}

function toStockAnnual(r: StockAnnualReport): StockAnnual | null {
  if (!r.has_activity || r.currencies.length === 0) return null
  return { currencies: r.currencies.map(toStockCurrency) }
}

function symbolName(s: { security_name: string | null; symbol: string }): string {
  return s.security_name || s.symbol
}

function toSell(s: StockAnnualSell | null): StockSellHighlight | null {
  if (!s) return null
  return {
    market: s.market,
    symbol: s.symbol,
    name: symbolName(s),
    date: s.date,
    pnl: s.pnl,
    proceeds: s.proceeds,
    costBasis: s.cost_basis,
    returnPercent: s.return_percent,
  }
}

function toSymbol(s: StockAnnualSymbol | null): StockSymbolHighlight | null {
  if (!s) return null
  return { market: s.market, symbol: s.symbol, name: symbolName(s), count: s.count ?? 0, amount: s.amount ?? 0 }
}

function toStockCurrency(c: StockAnnualCurrency): StockCurrencySummary {
  return {
    currency: c.currency,
    buyCount: c.buy_count,
    sellCount: c.sell_count,
    dividendCount: c.dividend_count,
    symbolCount: c.symbol_count,
    buyAmount: c.buy_amount,
    sellAmount: c.sell_amount,
    fees: c.fees,
    taxes: c.taxes,
    dividends: c.dividends,
    realizedPnl: c.realized_pnl,
    winCount: c.win_count,
    lossCount: c.loss_count,
    winRate: c.win_rate,
    bestSell: toSell(c.best_sell),
    worstSell: toSell(c.worst_sell),
    topSymbolByTrades: toSymbol(c.top_symbol_by_trades),
    topDividendSymbol: toSymbol(c.top_dividend_symbol),
    monthlyRealizedPnl: c.monthly_realized_pnl,
    monthlyDividends: c.monthly_dividends,
    marketBreakdown: Object.entries(c.market_breakdown).map(([market, count]) => ({ market, count })),
    style: STYLE_MAP[c.style_tag] ?? 'swingTrader',
  }
}
