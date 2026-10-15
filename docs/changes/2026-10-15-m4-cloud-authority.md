# M4：Cloud 農場權威、社交與週榜

## 範圍與入口

本次沿用 M3 遊戲規則，接上自架帳好嗎 Cloud。使用者明確決定 **Web 只提供排行榜**，不建立唯讀農場或 Web 玩法／農場管理頁。App 入口是首頁／我的 → 農場 → 設定 → 雲端農場（明確新建或搬遷），農場 → 社交 → 農友／排行榜／事件；Web 入口是頭像選單 → 工具 → 農場排行榜（`/app/farm-leaderboard`，亦可用⌘K）。詳細 wire 見 [FARM_API_CONTRACT](../FARM_API_CONTRACT.md)；App 行為見 App repo `docs/changes/2026-10-15-m4-cloud-farm.md`，Web 驗收見 [同批 Web 紀錄](2026-10-15-m4-web-leaderboard.md)。

## 純規則與存檔

`src/services/game/{random,time,growth,market,turnips,scoring,layout}.py` 對照 App `lib/farm/`，包含確定性 SplitMix64、逐小時推進、季節／道具、價格、大頭菜、計分與佈置。Dart ISO 毫秒格式與整數 epoch seed 也是契約，不能以 Python 預設 ISO／浮點時間替代。兩端同一份 balance 與 golden；33筆規則測資及六種完整12週經濟模擬均精確相同。業務日期經 `business_time`；本輪跨端驗證採 Asia/Taipei。

Alembic `0074_game_farms`（接 `0073_tx_created_at`）新增11張 `game_*` 表。Farm 的 state/layout/當週大頭菜、slot 規則狀態與 UI 結算明細採受 API 控制的 JSON；庫存、錢包、週榜、攤位、操作憑證、社交事件與天氣／市場快取分表。這是獨立遊戲資料，不新增記帳 sync entity，不實作 Cloud bookkeeping repository。模型由 `src/models.py` 登記，既有整庫備份會包含這些表。SQLite 建表前明確 BEGIN，測試中途第5張表失敗能整批 rollback 並成功重跑；PostgreSQL沿用原生 transactional DDL。

`FarmService` 只用伺服器時間。SQLite BEGIN IMMEDIATE／PostgreSQL 排序 advisory locks 把推進、消耗、領取、雙方社交更新放在同一 transaction。錢包 append-only、`(user,reason,ref)`唯一；`requestId` 保存指紋與完整回應，斷線重送不再扣款或發獎。

## 搬遷與跨裝置

移入保留原單機隨機種子、田地、佈置、庫存、等級、有效攤位及大頭菜實物／週購買額度；**不是只搬庫存**，因為已上架作物早已離開庫存。大頭菜未來序列由 server 重算，不接受傳入prices。好幣封頂5,000、超額給開墾先驅，不移歷史結算。Codec 必要欄位、bool／整數、日期、效果、庫存與位置完整驗證，失敗不留下半座農場；超前的單機時間拒絕移入，原本地存檔仍在。

頂層 snapshot.ownerId 是登入歸屬；state.playerId 是遊戲seed，兩者不可混用。scoringLedgerSyncId 用 external id，App再映射本地int，避免另一部裝置把帳本選錯。已有 Cloud 農場不接受本地覆蓋。App快取按server+account，離線不排操作佇列、不降級成本地執行。

## 記帳、行情與獎勵

H 用不可變 createdAt／firstSeenAt；只有在週結束48小時內到達的建立紀錄計H。寬限只影響尚未凍結的補算，已算的週不因補登或編輯重開；舊匯入的 createdAt 不改成接收當日。即使 happenedAt 很久以前，本週新記一筆仍能算習慣日；退款原交易也排除，含退款日期在目前查詢區間外者。金額走既有 `stats_amount` 拆帳欠款口徑。計分只取一本自有非共享帳本；Cloud會為所有私人帳本建立owner membership，只有其他成員才視為共享，並檢查ledger_snapshot最新tombstone排除軟刪除。共享／刪除時通知一次，可從App通知中心回設定改選。M3已移除總預算入口，B保持缺項重分配，不拿分類或專案預算替代。

每週一00:05補結算、00:10凍結四榜，最多四週，`farm_settlement_rankings`每分鐘檢查。週獎勵手動領取；排名獎勵在凍結交易發放，唯一憑證防重。有效玩家定義是該週有正S、無異常標記且當時農場／帳戶啟用；少於5人只發S≥60保底200。同S先看記帳天數，再看最後記帳建立時間，避免比誰先開結算頁。冠軍有稱號、種子與季節樹木信用，存入 decorCredits；佈置時原子扣除信用抵該新增樹木價格，預覽與server一致。異常flag保存在結算表；依使用者Web範圍，本輪沒有另建農場管理介面。

市場08:00／20:00一檔，全服固定快照包含前24小時售出量；推進時記錄已成交攤位售出量，實際好幣仍要點攤位領取。歷史未曾產生的快照採中性供需規則備援，已產生的價格不重算。狗罰金／保險也用同一伺服器當期價格。天氣每5分鐘檢查、有一小時內快取不重抓；外部I/O前歸還 DB 連線，失敗保留已知快取與中性缺時降級。

## 社交與隱私

只能看啟用且帳戶啟用的農友，直連也檢查，不能用已知userId繞過列表。成熟作物才可偷；7天保護、每日10次、同戶4小時、每週5次、每次floor20%最少1、主人至少ceil60%。回禮只有被偷本人、12h內、沒用過、非被狗抓的事件；需選對方田地，只豁免在家／每日／冷卻，每週防騷擾仍在。幫忙需要實際有水分／雜草／蟲害可改善，每日10次、雙方5幣。

正常偷菜良心−2、被抓−10、幫忙+1、每個業務日期恢復+3；低於50出售收10%費、低於20不能偷。看門狗25%抓到、當期價×3罰金、24h🦝公開標記、被偷保險30%。事前須能支付最高罰金，避免餘額不足反覆重骰；狗500／糧50／72h是本次內測預設值。

presence 每裝置／WS連線獨立，home45秒過期，away只清自己，App前景農場每20秒送。**目前單uvicorn process；多worker必須先替換共享presence儲存**，不能直接加workers。

排行榜只回遊戲值／名次／暱稱／頭像，不回真實收支或email；匿名的id／頭像為null。averageScore／myBookkeepingScore僅指記帳S，不能和豐收價值／偷菜次數混算差距。人數是目前可見有效玩家，歷史獎勵按凍結當時人數，不因之後停用撤銷。公開拜訪沒有錢包或庫存。App/Web未到期🦝標記都會顯示，過期不回榜單。

## 通知、驗證與部署

category=farm 接既有通知中心，六類設定生效；收成預設關、每代一次；換季前3／1天台北20點後一次；偷菜2h合併、回禮按事件授權再檢查。App通知中心可跳同一農場／榜／事件；OS通知沿用開App的既有限制。

Cloud純規則／API／遷移／授權稽核／排程／既有錯誤契約測試通過；App與Web結果及真API瀏覽器驗收分別見同批文件。所有API／遷移驗證使用隔離SQLite，包含真sync/push建立私人帳本／owner membership及共享／軟刪除檢查，沒有對正式DB升版；PostgreSQL鎖路徑未做真server壓力驗收。App真實手機與兩部裝置在線互動仍待裝置驗收。原專案的其他warning／info不在本次清理範圍。

兩端均留 feat/v4-dev，未commit／合併main／部署。部署仍 Cloud（含alembic）→ Web → App，需先讓Cloud具備/game API再切App雲端模式；版本號仍3.8.3+1，4.0版本與商店收尾由M5處理。

最終驗證：Cloud上述完整套件 **142 項通過**；App完整 **312 項通過**、設定最後調整另55項UI回歸與通知新增路由增量通過；Web **262 項通過**、徽章及語系最後改動另18項定向通過。兩端hash與diff check乾淨。
