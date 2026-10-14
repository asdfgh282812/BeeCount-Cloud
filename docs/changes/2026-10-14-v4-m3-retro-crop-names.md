# M3 像素農場作物名稱契約

使用者提供已購買的 Super Retro Ranch Premium Tier v32，並授權缺少的品種直接換成包內作物。App 的單機農場換成統一俯視像素風；本 repo 同步 `src/services/game/balance_v1.json` 的16個 `nameZh` 名稱（含竹筍→竹子），以及 `tests/game/golden/contract_sha256.json` 的設定 hash。

作物 ID 保留為既有19個穩定槽位；價格、季節、等級、生長、採收、隨機 seed、規則和黃金測資不變，因此既有存檔／庫存／行情仍可使用。新版 UI 品種對照與原因見 App repo `docs/changes/2026-10-14-v4-m3-retro-ranch.md`；不要為了讓 ID 符合新作物名稱直接改 ID，否則會破壞舊存檔。

Web 的唯讀農場及排行榜仍屬 M4；本批是 App 單機呈現的例外，沒有新增用手機本機存檔偽造的 Web 農場。付費原始素材及抽取圖不放入本 repo，未修改 UI token，也未改任何 sync wire 欄位。

驗證：`tests/game/test_farm_contract.py` 四項通過，App 的 `scripts/check_farm_contract.py` 比對兩端設定及黃金測資 hash 一致。修改維持 `feat/v4-dev`，未併 main／未部署。
