# 農場契約：週日全天大頭菜與主動領取週獎勵

配合 App 同次修正，`src/services/game/balance_v1.json` 的大頭菜購買時段改為台北週日 00:00–24:00；驗證 `buyEndHour` 須接受 24。腐壞及種子 anchor 保留週日 05:00。未來 Python 規則必須把週日 00:00–05:00 購買歸入新週（未來的當日 05:00 anchor），讓報價／400 顆額度整日一致，新菜於下一週日腐壞；不能沿用上一週將到期的批次。

同步 SHA manifest，規則／經濟 golden 保留不變。`tests/game/test_farm_contract.py` 補購買 0–24 與腐壞 05:00 的契約驗證。

App 的週結算改成只建立待領結果，使用者在「農場 → 好幣 → 週結算 → 領取好幣」才寫錢包。以 `(reason=settlement, refId=week)` 唯一流水作領取憑證，舊自動入帳不重發，備份還原保留領取狀態。未來 Cloud 的領取 endpoint 亦需採用原子交易及同一防重複契約；不能在排程結算或讀取時自動發幣。

目前 Cloud/Web 仍未實作農場規則 API 或玩法 UI（M3 本機，M4 接線），因此沒有可修改或瀏覽器驗證的對應頁面。本次不預先新增未接線的 endpoint 或 placeholder 控制。App 道具持續效果標示、精準聚焦與獎勵明細，詳見 App repo `docs/changes/2026-10-14-farm-effects-rewards-turnips.md`。

驗證：農場契約 pytest 6 項通過；三份 App／Cloud SHA 一致。App 農場／引導 266 項通過，最後四項 UI 回歸另重驗通過。
