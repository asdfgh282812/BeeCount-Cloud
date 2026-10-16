# 4.0 M2 第 5 批／5A：首頁、日曆與交易詳情

對齊 App `docs/changes/2026-10-16-v4-m2-5a-home-transactions.md`。入口：首頁、`/app/calendar`、`/app/transactions`，點交易開詳情。

- HomeHero 以中性結餘為主要數字，移除多組延遲進場／滾輪，保留 Amount 的數值變更淡入。
- CalendarPage 不再把月／日 API 失敗偽裝成沒有紀錄，兩處各自可重試；請求序號及 effect 清理擋掉較晚回來的舊請求。切日先進入載入狀態，避免上一天的紀錄冒充目前選取。
- 詳情限制 90dvh，內文捲動、操作列可換行且維持可達；長金額與欄位可換行。
- 全部交易搜尋框在窄版可縮小，避免擠出 viewport。
- 未改 schema／後端／同步 API，未修改 token 值；App 原生 Hero 不搬到 Web。App 此次另外修復既有隱藏金額偏好的遮蔽漏洞，Web 未新增另一套隱私偏好。

驗證：275 項 Web 測試通過，tsc＋Vite build 通過。Safari Technology Preview 操作隔離假資料 fixture：390×844／1280×900、亮暗色／1.75 倍字體、首頁視角切換、開關交易詳情、月份／當日 API 失敗及兩處重試；非正式後端或跨裝置驗收。更完整紀錄與待辦見 App 文件。本次僅開始第 5 批 5A，5B～5H 尚未完成。未部署或合併。
