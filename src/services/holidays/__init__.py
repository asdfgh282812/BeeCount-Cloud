"""節日資料(2026-10-05,docs/HOLIDAYS_SD.md):server 預先產生各國節日,App/Web 唯讀取用。

- `catalog`:節慶目錄——穩定 key ↔ 名稱/emoji/主題色/日期規則,以及
  `holidays` 套件英文假日名 → key 的對照表。
- `generator`:`generate_year(year)` 把 `holidays` 套件的國定假日(含補假)
  跟目錄裡的非放假節慶(情人節、七夕、聖誕節…)合併成一份清單。純函式,不碰 DB。
- `dataset`:落地到 `holiday_entries` + `holiday_dataset_meta`,內容有變才把
  version +1;`holiday_dataset_refresh` 排程與讀端點都走這裡。

節日資料是全域市場資料(跟 `ExchangeRateCache`/`Security` 同款),不分 user、
不進 sync、不經 ChangeTracker。
"""
