/**
 * 收支配色 / 股票漲跌色套到 <html> 的 data 屬性(styles.css 的 CSS 變數依此切換)。
 * 從 AppShell 抽出來,讓首次登入歡迎引導(WelcomeOnboarding)選色時也能即時預覽。
 */
export function applyStockColorScheme(stockUpIsRed: boolean) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.stockColor = stockUpIsRed ? 'red' : 'green'
}

export function applyIncomeColorScheme(incomeIsRed: boolean) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.incomeColor = incomeIsRed ? 'red' : 'green'
}
