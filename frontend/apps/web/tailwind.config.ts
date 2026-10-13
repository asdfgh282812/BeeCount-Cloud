import type { Config } from 'tailwindcss'

const config: Config = {
  darkMode: ['class'],
  content: [
    './index.html',
    './src/**/*.{ts,tsx}',
    '../../packages/ui/src/**/*.{ts,tsx}',
    '../../packages/web-features/src/**/*.{ts,tsx}'
  ],
  theme: {
    extend: {
      // 標題圓體(見 styles.css 的 @font-face);只有 400,配 font-normal 使用
      fontFamily: {
        display: [
          '"TallyCheck Rounded"',
          'system-ui',
          '-apple-system',
          '"Segoe UI"',
          '"PingFang TC"',
          '"Noto Sans TC"',
          '"Microsoft JhengHei"',
          'sans-serif'
        ]
      },
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        // 色碼全部來自 src/styles.css 的 CSS 變數(= App BeePalette),這裡只做對應。
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
          // App BeeTokens.primarySoft / primaryStrong:選中底、按下態
          soft: 'hsl(var(--primary-soft))',
          strong: 'hsl(var(--primary-strong))'
        },
        // 收成金(App BeeTokens.accent)。shadcn 的 `accent` 已被 hover 底佔用,
        // 所以品牌金色叫 harvest。不跟隨主題色。
        harvest: {
          DEFAULT: 'hsl(var(--harvest))',
          strong: 'hsl(var(--harvest-strong))',
          foreground: 'hsl(var(--harvest-foreground))'
        },
        info: 'hsl(var(--info))',
        success: 'hsl(var(--success))',
        warning: 'hsl(var(--warning))',
        'border-strong': 'hsl(var(--border-strong))',
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))'
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))'
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))'
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))'
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))'
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))'
        },
        // 收支语义色：跟 mobile `incomeExpenseColorSchemeProvider` 同步，
        // 通过 <html data-income-color="red|green"> 切换底层 CSS var。
        income: 'rgb(var(--income-rgb) / <alpha-value>)',
        expense: 'rgb(var(--expense-rgb) / <alpha-value>)',
        // 股票漲跌語義色:獨立於收支,透過 <html data-stock-color> 切換。
        'stock-up': 'rgb(var(--stock-up-rgb) / <alpha-value>)',
        'stock-down': 'rgb(var(--stock-down-rgb) / <alpha-value>)'
      },
      // 圓角對齊 App BeeRadius(xs 6 / sm 10 / md 14 / lg 20 / xl 28):
      // 按鈕、輸入框(rounded-md)= 14;卡片(.card 的 rounded-xl、.bc-panel 的
      // rounded-2xl)= 20。tailwind 的 lg 停在 14,保持 sm ≤ md ≤ lg ≤ xl 單調。
      borderRadius: {
        xs: 'var(--radius-xs)',
        sm: 'var(--radius-sm)',
        md: 'var(--radius-md)',
        lg: 'var(--radius)',
        xl: 'var(--radius-lg)',
        '2xl': 'var(--radius-lg)',
        '3xl': 'var(--radius-xl)'
      },
      boxShadow: {
        soft: '0 10px 30px -16px hsl(var(--shadow) / 0.55)',
        // App BeeElevation 0~3 階(暗色三階都是 none,見 styles.css)
        'elevation-1': 'var(--elevation-1)',
        'elevation-2': 'var(--elevation-2)',
        'elevation-3': 'var(--elevation-3)'
      }
    }
  },
  plugins: []
}

export default config
