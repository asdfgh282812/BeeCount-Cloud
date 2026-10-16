# 2026-10-16 農場板條箱獨立於倉庫

板條箱不再是倉庫的共用容量，而是另一個獨立空間：`game_inventory.location`（`warehouse` / `crate`，alembic `0075_game_inventory_location`）。

- 收成、偷菜收穫、買大頭菜走 `FarmService.store`：先放倉庫，滿了才進板條箱，兩邊都放不下回 `STORAGE_FULL`。
- `consume`（賣出、上架、播種、用道具）只扣倉庫；`sell` / `listForSale` 的庫存檢查也只算倉庫。
- 新動作 `moveInventory {id, quantity, to}`：搬指定批次，部分搬會拆批；目的地放不下回 `STORAGE_FULL`（倉庫空間不足）或 `CRATE_FULL`（板條箱空間不足）。
- `saveLayout` 移除板條箱時，放不下的箱內收成（最早到期先）退回倉庫，倉庫也放不下才回 `STORAGE_FULL`。
- 舊資料：`advance` 每次呼叫 `rebalance`，把超出倉庫上限的收成（最晚到期先）搬進板條箱；正常情況下是 no-op。
- 快照 inventory 每列多 `location`；`import_save` 接受並驗證 `location`。

App 端規則一致，完整說明見 App `docs/changes/2026-10-16-farm-crate-storage.md`。Web 沒有農場倉庫頁，不需改動。部署：先 `alembic upgrade head` 再發 App。
