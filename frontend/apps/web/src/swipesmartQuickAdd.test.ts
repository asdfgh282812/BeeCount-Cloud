import { describe, expect, it } from 'vitest'

import { findAccountBySwipesmartCardId, matchCategoryByName } from '@beecount/web-features'

type Account = {
  account_type?: string | null
  hidden?: boolean
  swipesmart_card_id?: string | null
  name: string
}

describe('findAccountBySwipesmartCardId', () => {
  const accounts: Account[] = [
    { account_type: 'credit_card', hidden: false, swipesmart_card_id: 'card-1', name: '玉山信用卡' },
    { account_type: 'credit_card', hidden: true, swipesmart_card_id: 'card-2', name: '已隱藏卡' },
    { account_type: 'cash', hidden: false, swipesmart_card_id: 'card-3', name: '現金' },
  ]

  it('命中未隱藏的信用卡帳戶', () => {
    expect(findAccountBySwipesmartCardId(accounts, 'card-1')?.name).toBe('玉山信用卡')
  })

  it('命中但帳戶已隱藏 → 視同沒對照到', () => {
    expect(findAccountBySwipesmartCardId(accounts, 'card-2')).toBeNull()
  })

  it('cardId 沒有任何帳戶對照 → null', () => {
    expect(findAccountBySwipesmartCardId(accounts, 'card-999')).toBeNull()
  })

  it('cardId 為空字串 → null', () => {
    expect(findAccountBySwipesmartCardId(accounts, '')).toBeNull()
  })
})

describe('matchCategoryByName', () => {
  type Category = { name: string }
  const categories: Category[] = [{ name: '餐飲' }, { name: '交通' }, { name: '餐飲外送' }]

  it('剛好一筆命中(完全相同)才帶入', () => {
    expect(matchCategoryByName(categories, '交通')?.name).toBe('交通')
  })

  it('比對到多筆(包含式模糊比對命中兩筆)→ null', () => {
    expect(matchCategoryByName(categories, '餐飲')).toBeNull()
  })

  it('比對不到(0 筆)→ null', () => {
    expect(matchCategoryByName(categories, '娛樂')).toBeNull()
  })

  it('傳入 null → null', () => {
    expect(matchCategoryByName(categories, null)).toBeNull()
  })

  it('忽略大小寫與空白', () => {
    expect(matchCategoryByName([{ name: ' Coffee ' }], 'coffee')?.name).toBe(' Coffee ')
  })
})
