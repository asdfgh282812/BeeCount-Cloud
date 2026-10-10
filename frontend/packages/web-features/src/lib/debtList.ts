import type { ReadDebt } from '@beecount/api-client'

/**
 * 應收應付款項清單的純邏輯(App v68 MOZE 化),對齊 App
 * `lib/data/repositories/debt_repository.dart`(`debtTabOf`、`allocateRepayment`、
 * `CounterpartySummary`)與 `getCounterpartyHistory`。
 */

export type DebtTab = 'active' | 'notStarted' | 'completed'
export const DEBT_TABS: readonly DebtTab[] = ['active', 'notStarted', 'completed']

export const isDebtCompleted = (d: ReadDebt): boolean =>
  d.status === 'settled' || d.status === 'closed'

/** 借出/借入日:started_at → 起點交易時間;都沒有 = 0(視為很久以前)。 */
export function debtStartedAtMs(d: ReadDebt): number {
  const raw = d.started_at || d.origin_transaction?.happened_at
  if (!raw) return 0
  const ms = new Date(raw).getTime()
  return Number.isNaN(ms) ? 0 : ms
}

export function debtTabOf(d: ReadDebt, nowMs: number): DebtTab {
  if (isDebtCompleted(d)) return 'completed'
  if (debtStartedAtMs(d) > nowMs) return 'notStarted'
  return 'active'
}

/** 清單上的類型標籤:有欠款分類就用分類名稱(MOZE「代付」標籤),否則依
 *  款項類型退回 借出/借入/既有應收/既有欠款/代刷分期。回傳 i18n key 或文字。 */
export function debtTypeTag(d: ReadDebt): { text: string } | { key: string } {
  if (d.category_name) return { text: d.category_name }
  if (d.installment_no) return { key: 'debts.tag.cardInstallment' }
  if (d.kind === 'existing') {
    return { key: d.direction === 'receivable' ? 'debts.tag.existingReceivable' : 'debts.tag.existingPayable' }
  }
  if (d.from_split) return { key: 'debts.tag.advance' }
  return { key: d.direction === 'receivable' ? 'debts.tag.lend' : 'debts.tag.borrow' }
}

export type DebtGroup = {
  counterparty: string
  debts: ReadDebt[]
  /** 應收正、應付負的剩餘合計(停止追蹤的不算)。 */
  net: number
}

export function groupDebtsByCounterparty(debts: readonly ReadDebt[]): DebtGroup[] {
  const map = new Map<string, ReadDebt[]>()
  for (const d of debts) {
    const key = d.counterparty_name.trim()
    const list = map.get(key)
    if (list) list.push(d)
    else map.set(key, [d])
  }
  const groups: DebtGroup[] = []
  for (const [counterparty, list] of map) {
    list.sort((a, b) => debtStartedAtMs(b) - debtStartedAtMs(a) || a.id.localeCompare(b.id))
    const net = list.reduce((sum, d) => {
      if (d.status === 'closed') return sum
      return sum + (d.direction === 'receivable' ? d.remaining_amount : -d.remaining_amount)
    }, 0)
    groups.push({ counterparty, debts: list, net })
  }
  // 最近有動靜的對象排前面。
  groups.sort(
    (a, b) => debtStartedAtMs(b.debts[0]) - debtStartedAtMs(a.debts[0]) ||
      a.counterparty.localeCompare(b.counterparty)
  )
  return groups
}

const round2 = (v: number) => Math.round(v * 100) / 100

/** 多筆收還款的金額分配:依借出日由舊到新,先把舊的收清再往下分。只回傳分到
 *  金額 > 0 的項目;超過合計的部分不分配(呼叫端應先擋)。 */
export function allocateRepayment(
  debts: readonly ReadDebt[],
  amount: number
): { debt: ReadDebt; amount: number }[] {
  const sorted = [...debts].sort(
    (a, b) => debtStartedAtMs(a) - debtStartedAtMs(b) || a.id.localeCompare(b.id)
  )
  let left = round2(amount)
  const out: { debt: ReadDebt; amount: number }[] = []
  for (const d of sorted) {
    if (left <= 0.004) break
    const take = round2(Math.min(left, d.remaining_amount))
    if (take <= 0) continue
    out.push({ debt: d, amount: take })
    left = round2(left - take)
  }
  return out
}

export type DebtHistoryEntry = {
  debt: ReadDebt
  /** 起點(借出/借入/既有登記)或一筆收還款。 */
  isOrigin: boolean
  txId: string | null
  atMs: number
  amount: number
  scheduled: boolean
  accountName: string | null
  /** 這一列之後的累計淨額(應收正、應付負),時間由舊到新累加。 */
  runningNet: number
}

/** 對象的借還款歷史:每筆欠款的起點 + 收還款,回傳由新到舊。未來排程
 *  (待出帳)也列出,但不算進累計淨額。 */
export function counterpartyHistory(debts: readonly ReadDebt[]): DebtHistoryEntry[] {
  const rows: Omit<DebtHistoryEntry, 'runningNet'>[] = []
  for (const d of debts) {
    rows.push({
      debt: d,
      isOrigin: true,
      txId: d.origin_transaction?.id || null,
      atMs: debtStartedAtMs(d),
      amount: d.principal_amount,
      scheduled: false,
      accountName: d.origin_transaction?.account_name || null
    })
    for (const r of d.repayments) {
      rows.push({
        debt: d,
        isOrigin: false,
        txId: r.id,
        atMs: new Date(r.happened_at).getTime(),
        amount: Math.abs(r.amount),
        scheduled: Boolean(r.scheduled),
        accountName: r.account_name || null
      })
    }
  }
  // 同一時間:起點排在收還款之前(由舊到新)。
  rows.sort((a, b) => a.atMs - b.atMs || (a.isOrigin === b.isOrigin ? 0 : a.isOrigin ? -1 : 1))
  let net = 0
  const withNet = rows.map((r) => {
    if (!r.scheduled) {
      const sign = r.debt.direction === 'receivable' ? 1 : -1
      net = round2(net + (r.isOrigin ? sign : -sign) * r.amount)
    }
    return { ...r, runningNet: net }
  })
  return withNet.reverse()
}

export type CounterpartySummary = {
  payable: number
  repaid: number
  payableRemaining: number
  receivable: number
  collected: number
  receivableRemaining: number
}

/** 對象歷史上方的兩列彙總(停止追蹤的剩餘不算),同 App `CounterpartySummary.of`。 */
export function counterpartySummary(debts: readonly ReadDebt[]): CounterpartySummary {
  const s: CounterpartySummary = {
    payable: 0,
    repaid: 0,
    payableRemaining: 0,
    receivable: 0,
    collected: 0,
    receivableRemaining: 0
  }
  for (const d of debts) {
    const repaid = d.repaid_amount ?? Math.max(d.principal_amount - d.remaining_amount, 0)
    const remaining = d.status === 'closed' ? 0 : d.remaining_amount
    if (d.direction === 'receivable') {
      s.receivable += d.principal_amount
      s.collected += repaid
      s.receivableRemaining += remaining
    } else {
      s.payable += d.principal_amount
      s.repaid += repaid
      s.payableRemaining += remaining
    }
  }
  return s
}
