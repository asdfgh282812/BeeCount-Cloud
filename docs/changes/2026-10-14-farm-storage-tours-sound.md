# 農場倉庫容量契約更新

配合 App 同一任務的農場玩法調整，`src/services/game/balance_v1.json` 改成初始倉庫 10 格、永久擴容每次 100 好幣增加 10 格；同步更新 `tests/game/golden/contract_sha256.json` 的 balance SHA，其餘演算法與 golden 不變。

App 增加 30 好幣／個的板條箱（放置時提供 10 格）、倉庫頁擴容入口、六組整合引導與獨立點擊音效；詳見 App `docs/changes/2026-10-14-farm-storage-tours-sound.md`。Web 農場仍待 M4，尚無對應頁面，因此本次不新增農場 Web UI，也沒有部署或合併 main。

驗證：App／Cloud 契約比對通過；`tests/game/test_farm_contract.py` 5 項通過。
