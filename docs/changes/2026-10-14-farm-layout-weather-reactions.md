# App 單機農場：配置與天氣素材對齊

App v75 新增本機 `FarmLayouts` 單筆 JSON 配置，不屬於記帳同步 entity。入口：首頁農場卡片／我的 → 農場 → 佈置農場。田地可移動而保留 stable index、生長 seed、播種與收成狀態；店鋪可命名，選攤位／店面，點擊分別進入既有種子商店、賣場、倉庫，各店共用原本經濟，不增加獨立營業或收益規則。

Web 尚未提供農場，既定 M4 才實作權威遊戲 API 與 Web 農場。本次為 App 本機農場功能，沒有更動 Cloud sync wire contract 或 Web token，也不建立未接真實玩法的 Web 頁。

M4 對齊基準：

- 配置 version=1：plots 為 stable index 字串 → `{x, y}`；buildings 陣列為 `{id, kind, position:{x,y}, style, name}`。kind=`shop|market|storage`，style=`stall|store`。
- 原生座標寬 176、格距 16、高上限 640；上方 80 保留農舍／季節樹，下方預留圍欄 16。田地／攤位 32×32、店面 64×48。不能重疊／超界，完整田地集合必須與 backend 一致；最多 12 店。
- 種植規則保持原 stable index；區域道具按配置位置比較，橫／縱向各 48 原生像素內，不能再由 index 推算舊 3 欄鄰接。擴建找空位；配置隨單機帳本備份匯出／還原。
- 本地已購素材 v32 只提供玉米 shake／tempest 連續動作及 frozen 生長階段；不可把 frozen strip 當循環動畫，也不可把玉米覆蓋成其他作物。全作物可共用雨滴／濕土／雪邊特效，日夜都生效，減少動態保留靜態幀。
- 店面與攤位是既有付費素材，不可將原包／切圖公開提交。用途依圖示及使用者名稱區分；後續 Web 合法資產部署須遵守素材授權。

詳細實作及驗證：App `docs/changes/2026-10-14-farm-layout-weather-reactions.md`；模型：`lib/farm/farm_layout.dart`；呈現：`lib/pages/farm/farm_crop_weather_art.dart`、`farm_scene.dart`；純規則算法未變。兩端仍保留 `feat/v4-*` 分支，不合併 main。
