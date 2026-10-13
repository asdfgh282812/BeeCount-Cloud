# 4.0 Web 選項與確認按鈕的 Liquid Glass

配合 App 的日期篩選、記帳分段選擇與確認勾勾，延伸既有 A／B／C 材質。

- `packages/ui/src/ui/button.tsx`：主／次／危險動作用染色玻璃；outline 用中性玻璃。ghost 不加表面。
- `tabs.tsx`：底座模糊，選取項使用较厚玻璃滑塊，不疊第二層模糊；保留原有 click 並補 `aria-pressed`。
- `glass-choice.tsx`：日期等互斥選項的共用按鈕；交易頁日期預設接上此元件，日期算法與篩選狀態不變。
- `CurrencySelector.tsx` 與 `TransactionsPanel.tsx`：記帳幣別入口、附加金額加號同步材質。
- `styles.css`：延用原來的玻璃偏好；選項染色 20%、滑塊 8%、主要動作 90%，對齊 App。減少透明度／高對比用不透明底座混色，保留選取狀態；forced-colors 使用系統按鈕色。

入口：交易頁上方日期預設；新增／編輯交易的幣別與附加金額；全站共用 Tabs 與確認 Button。材質設定仍是右上角頭像 → 個人資料 → 同步偏好 → Liquid Glass。不新增設定、API、資料表或 wire 欄位。

驗證：29 檔／258 項測試與 production build 通過（仍有既有 chunk size 提示）。臨時本機 QA 頁引用上述正式元件與正式 CSS，實際操作 A／B／C、亮暗色、日期、分段選擇、確認與幣別切換；430×900 無溢出。QA 入口驗證後移除，不進正式 build。App 的 `appearance.glass` tour 與 4.0 What's New 在同任務更新。

限制：Flutter／CSS 視覺近似，不是原生折射；未做真機效能測量。App 詳細理由與驗證见對應 repo 的 `docs/changes/2026-10-13-v4-glass-controls.md`。沒有部署。
