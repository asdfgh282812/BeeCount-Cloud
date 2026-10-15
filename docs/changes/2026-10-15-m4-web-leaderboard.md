# M4 Web：只提供農場排行榜

使用者將 M4 的 Web 範圍改為僅排行榜；農場場景、瀏覽其他農友、種植／收成／社交操作都保留在 App。本次不建立 Web 農場遊戲介面，也不向任何農場操作或拜訪 API 發送請求。

## 入口與讀取契約

- 右上頭像選單 → 工具 → **農場排行榜**；搜尋／⌘K 的頁面導航亦可進入，獨立路由 `/app/farm-leaderboard`。
- `packages/api-client/src/game.ts` 定義四榜與 camelCase 回應，透過既有 JWT／授權處理呼叫 `GET /game/leaderboard?board=…&week=…`。省略週次時由伺服器決定最近已結算週，Web 不用瀏覽器日期推算週界。
- `FarmLeaderboardPage.tsx` 提供記帳、豐收、神偷、守護四榜、歷史週次、自己的榜單分數；**記帳榜**另外顯示記帳 S 平均與自己的 S 跟平均差距；目前可見有效玩家少於 5 人時說明獎勵依週結算時人數判斷，不把事後停用帳戶誤解成撤銷既已發出的歷史獎勵。重新整理、載入、失敗重試、空榜與未參與皆有明確狀態。快速切榜時忽略舊請求結果。`averageScore` 與 `myBookkeepingScore` 專指記帳 S，`myValue` 是當前所選榜的數值；豐收／神偷／守護榜不把不同單位的數值與 S 相減。
- 公開列只呈現暱稱／頭像／遊戲分數／名次／遊戲獎勵，不顯示 userId，也不產生拜訪連結。`userId == null` 的匿名列強制使用在地化「匿名農友」，並忽略頭像，即使回應誤附名稱與頭像也不顯示。真實金額、錢包與 email 不屬於型別化榜單契約；既有登入帳戶的頭像選單仍沿用原本自身 profile 顯示。
- 選擇器沿用 GlassSegmented，卡片／表格／狀態使用既有 CSS tokens；保留此次工作之前的 `styles.css` 與 `primary-color-script.ts` 改動。文案同步 en／zh-TW／zh-CN。
- Web 沒有 App 的 TourTarget／What's New 模組；M4 的 App 導覽與更新公告由 App 同批更新。

## 驗證及範圍

- `pnpm -C apps/web build` 通過；既有套件 directive 與 bundle size 警告仍在，不影響建置。
- Web Vitest 全套 **30 檔、262 測試**通過，包含新增 4 項：JWT 讀取／週次參數、最新週交由伺服器、錯誤不當空資料、全域路由往返。既有三語 keys 對齊測試通過。平均分契約調整後再跑定向 3 檔、25 測試與 build 通過，並在 :5189 的建置實際確認非記帳榜沒有 S 平均／差距。
- **第一輪瀏覽器手動操作使用合成 mock，沒有正式帳號、憑證或 DB**。在全新 `127.0.0.1:5188`、`:5189`／最終 `:5190` 來源上載入實際 production `dist`，由臨時 Node HTTP server 提供同源合成 API；測過登入深連結、四榜、歷史週次、少於 5 人、記帳 S 差距（其他榜不顯示記帳平均）、匿名列（故意誤附姓名／頭像仍不呈現）、503 → 重試恢復、空榜、自己未參與、工具選單／命令面板與中英文文案，並實際查看深色完整畫面。此輪驗證只確認前端與已約定回應形狀；真 Cloud API 整合驗證另見下列補驗。
- Vite dev 在舊來源曾遇 PWA 舊檔與新 prebundle 混用造成 React duplicate hook。改用全新來源的正式建置完成驗證，沒有為測試而改動產品配置。最終少人提示再於 :5190 的建置實際操作確認；所有臨時 server 與瀏覽器頁面已關閉。


### 真 Cloud API 隔離整合補驗

在全新 `127.0.0.1:8099` 來源，由真 FastAPI 提供既有 production `dist`，完全使用暫存目錄的 SQLite／`DATA_DIR`／附件與備份目錄；所有環境變數在任何 `src` import 前設定（development、開放註冊、停用授權限制、隨機 JWT secret），不讀正式 `.env` 或既有 DB。以 `Base.metadata.create_all` 建立隔離 schema、關閉臨時 server 的排程 lifecycle，再透過真註冊 API 建立 6 個合成玩家。兩週的合成 `GameSettlement` 與收成／偷菜事件由測試腳本種入隔離 DB，排行榜則由正式 `social.build_boards` 生成，沒有 mock 排行榜 HTTP 回應。

- 瀏覽器以真登入表單登入合成帳戶後保留深連結，依序操作四榜、歷史週選單及重新整理。最近已結算週 `2026-10-05` 的記帳榜顯示 6 人、平均 S 65、自己 90、差距 +25；歷史週 `2026-09-28` 顯示平均 55、自己 80、差距 +25。
- 豐收榜自己的數值為 100、神偷為 0、守護為 3，三者都不顯示 S 平均／差距。實際守護 API 同時回傳 `averageScore: 65`、`myBookkeepingScore: 90`、`myValue: 3`，驗證前端沒有混用不同榜單的單位。
- 由真 API 回傳的匿名列包含 `userId: null`、`avatarUrl: null`，UI 顯示在地化「匿名農友」。查看榜單 wire，沒有 email、真實金額、錢包或庫存欄位。
- UI 操作的遊戲資源請求全部為 `GET /api/v1/game/leaderboard`（含 board／歷史 week 參數）。另用同一個 Web JWT 做診斷讀取：`GET /game/farm` 得 403，未結束週 `2026-10-12` 得 400 `INVALID_WEEK`，確認排行榜授權與遊戲私有讀取範圍分離。這兩個錯誤是刻意的診斷請求，沒有農場操作呼叫。
- 重新整理前後，隔離 DB 的農場／週結算／榜單／錢包／社交事件列數相同，`game_action_receipts` 仍為 0。檢視真實深色畫面，沒有發現額外前端修正需求。這次驗證覆蓋登入到真榜單 API 的接線與讀取權限；合成週結算是直接 seed，因此不替代後端評分／排程演算法測試或 App 到 Cloud 的實機驗證。
- 驗證後已關閉自己建立的瀏覽器分頁與臨時 API server。隔離腳本與畫面僅留在 `/tmp`，不進產品 source；沒有正式帳戶、偏好、secret、DB 或雲端環境變更。


### 小偷標記補驗

最後追加的公開 `thiefUntil` 徽章已在新來源 `127.0.0.1:8101` 用真 Cloud API／最新正式建置再驗一次。另建全新 tempfile DB，seed 自己與匿名農友的 `thief_until = server now + 12h`：兩列實際顯示「🦝 小偷標記」，提示文字為「被看門狗抓到後，此標記保留24小時。」；匿名列仍只顯示「匿名農友」，wire 的 `userId`／`avatarUrl` 為 null。再僅將這兩個隔離 DB 時刻調至 now − 1s，按排行榜「重新整理」後兩個徽章都消失，真 API 的 `thiefUntil` 都回 null。此補驗沒有新增前端修改或農場操作 API；臨時 server／分頁已關閉。

本次不提交、不合併 main、不部署。後端排行榜的授權／匿名／週界／有效玩家計算由 Cloud 同批實作及 pytest 驗證。

驗收畫面：[真 API 排行榜](screenshots/2026-10-15-m4/web-leaderboard.png)、[匿名與小偷標記](screenshots/2026-10-15-m4/web-thief-mark.png)。
