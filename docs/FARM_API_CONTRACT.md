# M4 農場 API 契約

Base：`/api/v1/game`。JSON 欄位 camelCase，瞬間 UTC ISO（Z），週次為業務時區的週一 `YYYY-MM-DD`。所有 HTTP 端點都經過 `get_current_user` 與授權碼檢查；農場／社交操作只接受 `app_write`，排行榜與設定讀取接受 App 或 Web scope。遊戲資料不進記帳 sync/ChangeTracker。

## 錯誤與重送

全站 handler 正規化 HTTP 400/403/404/409：`{error:{code,message,request_id},detail:message,code}`；有 domain code 時兩個 code 一致。401/402/403 不能降級為本地操作。所有 action 傳 UUID `requestId`，成功回應和指紋原子保存；同 id/同 payload 原樣回覆、同 id/不同 payload 409。操作失敗整筆 rollback。原樣回覆可能是先前的 snapshot，頁面之後可讀取新狀態。

## 自己的農場

- `GET /config`：共用 `balance_v1.json` 加上 social policy。
- `GET /farm?refreshWeather=true`：無農場回 null；有農場先推進至伺服器時間。refreshWeather 選填，僅更新自己的粗略城市快取，已有一小時內快取時不重抓。
- `POST /farm {importSave?,scoringLedgerId?}`：新建／恢復；scoringLedgerId 為帳本 external sync ID，需自有非共享帳本。已有雲端農場時不接受 importSave 覆蓋。移入只處理 saveVersion=1；保留田地、佈置、庫存、有效攤位、等級及當週大頭菜數量；好幣最多 5,000，超額給「開墾先驅」，不移入歷史結算或排名。大頭菜當週未來序列由伺服器重新計算，不信任 client prices。
- `PATCH /farm {enabled?,cityCode?,scoringLedgerId?,anonymousOnBoard?,notificationSettings?}`。通知固定 settlement/ranking/stolen/guardian/harvest/season；除 harvest 預設關閉，其餘開。
- `POST /actions {requestId,action,...params}` 回完整 snapshot。action：buySeeds(cropId,quantity)、buyItem(itemId,quantity)、plant(index,cropId)、water(index)、weed(index)、removePests(index)、clear(index)、useItem(index,itemId)、harvest(index)、sell(cropId,quantity)、listForSale(slot,cropId,quantity)、collectListing(slot)、claimSettlement(week)、expandPlots、expandStorage、buyTurnips(quantity)、sellTurnips(quantity)、saveLayout(layout)、guardDog、feedGuardDog。

Snapshot：`{ownerId,state,balance,water,scoringAvailable,weather,layout,plots,inventory,settlements,claimedSettlementWeeks,listings,anonymousOnBoard,scoringLedgerSyncId,social,serverTime}`。

`ownerId` 是登入帳號 id，用於 App 快取身分驗證。`state.playerId` 是確定性隨機 seed 身分，移入時可保留單機值，**不可用作登入／快取擁有者身分**。state 為 App FarmStateRow 對應欄位（id=1/mode=cloud/enabled/enabledAt/lastSeenAt/playerId/cityCode/scoringLedgerId=null/configVersion/xp/capacity/waterDay/waterRemaining/waterBonus），可帶私有額外 metadata。App 用頂層 scoringLedgerSyncId 映射本地 ledger id。

water：`{day,remaining,bonus}`；weather：null 或 `{at,temperature,precipitation,cloud,gust,humidity,isDay}`；plots 依 `FarmSaveCodec` saveVersion=1；inventory：`[{id,itemId,quantity,expiresAt}]`；settlements：`[{week,score,coins,streak,detailsJson,createdAt}]`；listings：`[{slot,listingId,cropId,quantity,unitPrice,listedAt,sellsAt,expiresAt}]`。social：`{conscience,newbieUntil,guardDogOwned,guardDogUntil,thiefUntil,title,decorCredits,notificationSettings}`。snapshot 不含大頭菜 pattern／未來報價。

`GET /market`：`{prices:{cropId:int}}`，全服 08:00/20:00 固定價格，依該時段前 24 小時售出量計供需並持久化；未曾存在的歷史時段顯示中性供需的規則備援。
`GET /market/history?cropId=`：`[{at,price}]`。`GET /turnips`：`{buyPrice,sellPrice,quantity,bought,expiresAt}`，沒有未來序列；週日全天買、週一至六賣、舊批週日05:00到期。

## 公開社交

- `GET /farms?filter=active|ready`：`[{userId,displayName,avatarUrl,isHome,newbieUntil,title,guardDogUntil,thiefUntil}]`。僅啟用農場且帳戶啟用者；active 近24小時、ready 有成熟未枯萎作物。
- `GET /farms/{userId}`：以上欄位加 plots/layout/turnipSellPrice/serverTime；沒有錢包／庫存／真實收支／email。
- `POST /farms/{userId}/actions {requestId,action,index?,kind?,emoji?,quantity?,eventId?}`：help（index+kind=water/weed/removePests）、steal（index）、react（提供的五種 emoji）、sellTurnips（quantity）、returnGift（**index+eventId**）。回 `{message,quantity?,caught?,snapshot}`；亦接受 steal+returnGift:eventId 相容寫法。
- `GET /events`：本人最近七天最多100項 `[{id,actorId,ownerId,displayName,action,createdAt,returned,caught,emoji,returnGiftUntil,returnGiftAllowed}]`。回禮只有被偷本人、未捕獲／未使用且未滿12小時才可用；列表 allowed 只是提示，操作時再次驗證。回禮仍需選對方可偷田地，僅豁免在家／每日上限／4小時冷卻。
- `POST /presence {state:home|away,deviceId}`：每裝置獨立。WS `{type:farm_presence,state:home|away}` 每連線獨立。home 45秒過期，某台away不得清掉另一台home。App 每20秒前景且正在農場頁送心跳；Cloud目前單 process，擴充多worker前須換共享presence儲存。

偷菜有7天保護、每日10次、同戶4小時冷卻與每週5次、每次向下20%最少1個、主人至少保留ceil(60%)；幫忙每日10次雙方5幣。看門狗25%抓到，市價×3罰金、24h小偷標記；偷菜前須有足夠可能罰金，不能透過餘額不足反覆重骰。良心與保險30%依伺服器當期市場价。首次狗500、每次狗糧50、72h，是本次可調整的內測數值。

## 週榜

`GET /leaderboard?board=bookkeeping|harvest|thief|guardian&week=YYYY-MM-DD`：`{board,week,availableWeeks,playerCount,averageScore,myBookkeepingScore,myValue,items,serverTime}`。
items：`[{rank,userId,displayName,avatarUrl,value,rewardCoins,title,isMe,thiefUntil}]`；匿名時 userId/avatarUrl=null。**沒有任何真實金額／email**。averageScore／myBookkeepingScore 僅指記帳 S；myValue 是目前榜的遊戲數值，其他榜不得跟 S 相減。

week 必須合法曆日、週一、且已結束；省略時取最新已結算週。playerCount 是目前可見、該週有正 S 且無異常標記的人數；獎勵依凍結當時人數與名次，事後停用不撤銷既付獎勵。

每週一00:05結算、00:10排名，補最近四週、週唯一鍵防重。H 用 immutable createdAt，且 firstSeenAt≤週結束+48h；寬限只適用尚未凍結週，已結算結果不重新計算。總預算入口已於M3移除，B維持缺項重分配，不用分類／專案預算代替。低於5個有效玩家僅 S≥60保底200；名次獎勵暫停。冠軍的季節樹木信用存 social.decorCredits，佈置新增對應樹木自動免該次價格並原子消耗信用。通知／稱號／種子與排名獎勵同交易防重。

計分來源變成共享／軟刪除時建立一次 `farm_scoring_ledger` 通知，App通知中心跳農場設定改選；私人帳本本來就有owner membership，不能以「任何member」判為共享。App所有game HTTP請求帶SDK X-App-Version，402／426經既有授權／更新攔截；舊server回應先經session身份驗證才觸發全域攔截。
