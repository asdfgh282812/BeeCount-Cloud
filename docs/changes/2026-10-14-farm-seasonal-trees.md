# App 農場四季樹色與 Web 後續對齊

App 的首頁農場卡片／我的 → 農場，兩側大樹與旁邊的小樹改為四季配色：春天亮綠、夏天深綠、秋天橙紅、冬天冰藍，使用既有 Super Retro Ranch 的原生四季素材。依 `FarmTime.season(snapshot.state.lastSeenAt)` 判定，以農場固定 Asia/Taipei 的3／6／9／12月起始切換；夜色及城市天氣仍疊在季節素材上方。冬色不等於真實降雪。

Web 農場場景尚未實作，仍屬 M4，因此本次沒有可修改的對應 Web 頁，不新增假資料或提前引入完整農場 UI。後續 Web 實作需採用同一季節判定與配色，延續 App／Web 對齊；本次沒有變更 API、遊戲契約或共用 token。

App 新導覽 id 為 `farm.seasons`。完整素材座標、驗證與畫面在 `/Users/andy/BeeCount-main/BeeCount-main/docs/changes/2026-10-14-farm-seasonal-trees.md`。沒有部署或合併 main。
