import { describe, expect, it } from 'vitest'
import { ledgerFieldPatch } from './ledgerFieldEdit'

describe('ledger setting field patches', () => {
  const values = { ledger_name: '  Home  ', currency: 'usd', month_start_day: 12 }
  it('renaming sends only a trimmed name', () => expect(ledgerFieldPatch('ledger_name', values)).toEqual({ledger_name:'Home'}))
  it('currency selection sends only currency', () => expect(ledgerFieldPatch('currency', values)).toEqual({currency:'USD'}))
  it('month start selection sends only its selected day', () => expect(ledgerFieldPatch('month_start_day', values)).toEqual({month_start_day:12}))
})
