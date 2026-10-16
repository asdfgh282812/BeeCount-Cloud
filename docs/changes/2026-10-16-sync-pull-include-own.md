# /sync/pull 新增 include_own

日期:2026-10-16。完整背景見 App repo `docs/changes/2026-10-16-sync-replay-include-own-device.md`。

## 為什麼

`GET /sync/pull` 帶 `device_id` 時預設濾掉該裝置自己推的 change(它本地已有)。但裝置若因別的
原因少了自己當初推的實體,連從 `since=0` 重放也拉不回來,App 端同步狀態會永遠顯示「雲端比
本機多」。

## 改動

- `src/routers/sync/pull.py`:新增 query `include_own: bool = false`。true 時不加
  `updated_by_device_id != device_id` 條件。其餘(device 驗證、heartbeat、per-device cursor)
  不變。預設 false → 舊 App 行為完全不變。
- 測試 `tests/test_sync_pull_include_own.py`。

## 範圍外

- 不改 Web(Web 不走 `/sync/pull`)。
- 不改 `sync_changes` 寫入端;沒有 DB migration。

## 部署

先於 App。無 alembic。
