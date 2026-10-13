# 4.0 M3-1：農場單機規則的 Cloud 契約備份

分支：`feat/v4-m3-farm-contract`，基於既有 `feat/v4-m1-rebrand`，不併 main。

App repo 的 `lib/farm/` 已開始實作單機純 Dart 規則。這裡保存 `src/services/game/balance_v1.json` 與 `tests/game/golden/` 的完全相同副本，讓 M4 的 Python 規則有固定的輸入、期望輸出與平衡設定；設定目前不被任何正式服務載入。

`test_farm_contract.py` 檢查 SHA-256 manifest 及初版設定結構，**不是** Python 遊戲規則測試；M4 還需要用 `rules_v1.json` 做實際輸入→輸出驗證。App 的 `scripts/check_farm_contract.py APP_ROOT CLOUD_ROOT` 可直接比對兩個 checkout 的檔案；目前沒有接跨 repo 的 GitHub checkout／CI，避免綁定尚未推送的 feature branch。

沒有新增 API、alembic、排程或 Web 畫面。這批都是規則與測資，沒有新的使用者入口；4.0 Web 的唯讀農場與排行榜依既有路線圖在 M4 上線。App 端整個 M3 也還沒完成，接下來是 `Transactions.createdAt` 三端契約、Drift 單機存檔與 Flame 場景。

測試：`.venv/bin/python -m pytest tests/game/test_farm_contract.py -q`（4 項通過）；三份契約檔案與 App 完全一致。
