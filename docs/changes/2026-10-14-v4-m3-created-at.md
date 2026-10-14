# 4.0 M3-2：交易建立時間的 App／Cloud／Web 契約

配合 App 單機農場的記帳日判定，alembic `0073_tx_created_at` 新增 `read_tx_projection.client_created_at` 與 `first_seen_at`；舊列保留 null，不把交易發生日或 migration 時間當成建立時間。

App wire 使用 `createdAt`（UTC ISO）。projection 與 partial merge 保留已知建立時間，完整 snapshot、write 的 projection→快照轉接，以及 ledger/workspace read 均帶入它。Web 透過既有新建交易流程，由 `snapshot_mutator.create_transaction` 寫伺服器當下時間；表單不多一個可編輯日期欄，編輯／套用既有內容不重設建立時間。前端 API 型別以 `created_at?: string | null` 相容舊伺服器與舊資料。

`first_seen_at` 是首次 projection materialize 的伺服器時間。SyncChange.updated_at 可由 client 指定，不能用作可信接收時間；既有列首次接收時間未知則保留 null。一般更新與 projection rebuild 不重寫兩種時間。M4 再實作農場伺服器規則與「結算後48小時才同步」的門檻，不先建立空 API。

入口仍是 Web／App 原有新增交易按鈕，沒有新 UI 控制；唯讀農場與排行榜依 App 09 §5 留在 M4。App 的七張本地農場表不進 ChangeTracker，也不進 Cloud 記帳 projection。

驗證：`tests/test_tx_created_at.py` 加上多幣種、projection consistency、退款／拆帳、備份還原與 game 契約，共90個測試通過；Web `pnpm -C frontend/apps/web exec tsc -b` 通過。新增測試與 migration 的 ruff 通過。尚未部署，兩端保持 `feat/v4-dev`。

完整本地持久化說明在 App 的 `docs/changes/2026-10-14-v4-m3-farm-persistence.md`。

App 同一工作接續 M3-3 單機頁面，見 App 的 `docs/changes/2026-10-14-v4-m3-farm-playtest.md`。這階段 UI 只操作本地農場；依已定案09 §5，Web 的唯讀農場與排行榜仍在M4接權威資料後推出，不加入空頁面。
