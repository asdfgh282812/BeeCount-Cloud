/** 帳本設定只送出使用者選的欄位，避免覆寫同時由另一端修改的資料。 */
export type LedgerEditableField = 'ledger_name' | 'currency' | 'month_start_day'
export type LedgerFieldValues = { ledger_name: string; currency: string; month_start_day: number }
export function ledgerFieldPatch(field: LedgerEditableField, values: LedgerFieldValues) {
  switch (field) {
    case 'ledger_name': return { ledger_name: values.ledger_name.trim() }
    case 'currency': return { currency: values.currency.toUpperCase() }
    case 'month_start_day': return { month_start_day: values.month_start_day }
  }
}
