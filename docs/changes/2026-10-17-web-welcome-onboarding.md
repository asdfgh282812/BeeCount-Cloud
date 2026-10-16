# Web 首次登入歡迎引導（對齊 App）

完整的設計、觸發條件與取捨寫在 App repo：`docs/changes/2026-10-17-onboarding-personalize.md`。這裡只記 Web／Cloud 這一側改了什麼。

## 入口

全新帳號（雲端沒有任何帳本、且 `profile.appearance.onboarding_done` 不是 true）第一次進 `/app/*` 時，全螢幕「歡迎」引導自動蓋上來；沒有手動入口。App 或 Web 任一端做完（或按略過）就不再出現。

## 後端

**沒有改動。** `appearance_json` 是無 schema 的 dict，新增 `onboarding_done`、`week_starts_monday` 兩個 key 不需要 migration。`PATCH /profile/me` 的 `appearance` 仍是整體替換，所以 Web 寫 appearance 時一律先展開現有值。

## 前端（`frontend/`）

- `packages/api-client/src/types.ts`：`ProfileAppearance` 加 `onboarding_done`、`week_starts_monday`（後者 App 早就同步，Web 先前沒用）。
- `apps/web/src/components/WelcomeOnboarding.tsx`（新）：四步——語言／主題色／外觀模式；Liquid Glass／造型；每週起始日／餘額顯示格式／收支顏色／股票漲跌色；AI（開關走原本的隱私同意，另有「設定服務商與金鑰」→ 完成後進 `/app/settings/ai/manage`）。語言只提供繁體中文／English（簡中仍可從頁首切換，但不進引導）；不自動改使用者目前的語言。語言、主題色、外觀模式是本機偏好立即套用；其餘選了先預覽（`applyGlassStyle`／`applyIncomeColorScheme`／`applyStockColorScheme`），完成或略過時一次 PATCH：`appearance`（只覆蓋碰過的欄位＋`onboarding_done: true`）、`income_is_red`、以及有改才送的 `theme_primary_color`（讓之後登入 App 拿到同一個顏色）。覆蓋層鎖住背景捲動、換步驟回到頂端。
- `apps/web/src/lib/welcomeOnboarding.ts`（新）：`shouldShowWelcome`（要 profile 與帳本清單都載完才可能為 true）、`resolveWeekStartsMonday`、`WELCOME_LOCALES`。
- `apps/web/src/lib/colorScheme.ts`（新）：把 `AppShell` 內的 `applyStockColorScheme`／`applyIncomeColorScheme` 抽出來讓歡迎引導共用（行為不變）。
- `apps/web/src/app/AppShell.tsx`：追蹤 `ledgersLoaded`，條件成立時掛 `WelcomeOnboarding`；完成後本地先收起，不等 profile 重抓。
- `apps/web/src/pages/sections/CalendarPage.tsx`：每週起始日改讀 `appearance.week_starts_monday`，沒設定才沿用語系推斷；表頭語言與起始日脫鉤（英文瀏覽器選週一不再顯示中文表頭）。
- `SettingsProfileAppearanceSection.tsx`：「外觀」設定頁補「每週起始日」下拉（App↔Web parity；歡迎頁選項因此是真的有作用）。
- i18n：`welcome.*`、`profile.sync.weekStart`，`en`／`zh-TW`／`zh-CN` 三份都補。

## 取捨

- Web 的「造型」只存、不顯示（Web 沒有頁首皮膚），歡迎頁文字明講；與既有設定頁的皮膚下拉一致。
- 歡迎引導是 App 以外唯一會主動推 `theme_primary_color` 的 Web 流程；設定頁的色票照舊只存本機。
- 沒有「重看一次」入口。

## 驗證

`pnpm -C apps/web exec tsc -b` 通過；`vitest`：`src/welcomeOnboarding.test.ts`、`src/i18n.test.ts` 通過。用全新本機 Cloud 帳號實際走完兩輪：預覽即時套用、完成後 `/profile/me` 帶回所有碰過的欄位與 `onboarding_done`、覆蓋層收起並還原捲動；日曆選週日後表頭週日在前。尚未部署、未在真機／窄螢幕逐步驗證版面。
