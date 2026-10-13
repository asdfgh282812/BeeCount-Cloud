# V4 玻璃分段選項拖曳

使用者希望記帳頁「一般／週期／分期／訂金」像底部導覽列一樣可按住拖動，並微微放大。

## Web 元件與入口

- `frontend/packages/ui/src/ui/glass-segmented.tsx`：可受控的共用等寬玻璃分段元件。按下浮起至 1.06，水平位移超過 8px 後直接跟手；放開才通知切換，不在滑過中間模式時重建表單。Pointer capture 保持追蹤，取消或父層拒絕切換時返回受控值。單一指標、停用項目、RTL、尺寸變動均可處理。
- 以 requestAnimationFrame 只更新 transform；吸附使用 response 0.3 秒、臨界阻尼彈簧，縮回使用 0.8 阻尼。釋放位置的速度銜接到彈簧，不用動量推算另一個選項，避免越過使用者選中的模式。
- `TransactionsPanel.tsx`：入口為新增／編輯交易表單的日期下方，將週期／分期／訂金獨立开關收斂到互斥分段列，對齊 App。收入不顯示分期或訂金；編輯不顯示週期／分期；已補尾款維持唯讀；owner 權限與原訂金限制保留。選擇模式原子更新既有三個旗標，資料及 API 不變。
- 原生 button 提供 Tab、Enter、Space，另支援方向鍵／Home／End。鍵盤切換立即定位；系統或 App 減少動態時不放大、不回彈，但保留拖動；touch-action: pan-y 保留垂直捲動。

## App 對應

App `BeeSegmented` 採同樣的放大、跟手、放開切換與取消行為；外觀仍沿用 A／B／C。App 新導覽 id 為 `editor.glassDrag`，入口為記帳頁及我的 → 功能說明。未修改底部導覽列或可排序項目。

## 驗證

- Web 29 個測試檔、258 項測試通過，TypeScript／Vite build 通過；既有大 chunk 警告仍存在。
- 本機瀏覽器以實際共用元件及實際 TransactionsPanel 操作點按、拖動、週期／分期／訂金欄位互斥、垂直取消、RTL、減少動態與鍵盤切換。驗證頁已移除。
- App 82 項 widget／表單／tour 測試通過，涵蓋放大、只在放開通知一次、取消、父層拒絕、RTL、邊界與垂直捲動。
- 沒有啟動 iOS Simulator，避免先前 datamigrator 崩潰再出現；手機實際手感仍需實機確認。未部署、未改 main。
