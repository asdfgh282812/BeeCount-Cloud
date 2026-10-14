# M3 農場：每季五種入門作物

App 入口：首頁農場卡片／我的 → 農場 → 商店。使用者希望每季一級至少有五種商品，因此同步 App 的 balance_v1.json：調低基礎作物 minLevel，番茄增加夏、竹子增加秋、萵苣／胡蘿蔔增加冬、小麥增加春。

春：萵苣／竹子／玉米／胡蘿蔔／番茄；夏：芹菜／茄子／甜椒／洋蔥／番茄；秋：竹子／蘿蔔／馬鈴薯／南瓜／甜菜根；冬：萵苣／胡蘿蔔／韭蔥／花椰菜／莓果。4級春夏小麥、4級秋葡萄、5級冬青花菜保留解鎖。

既有19個ID、價格／產量／時數與PRNG版本不變；不用改 sync wire 或DB。多季播種仍遵循原換季枯萎／保留券規則。Cloud目前只保存M3規則契約、Web遊戲為M4，沒有新增Web農場頁面或待售API。

更新 tests/game/golden/contract_sha256.json 的balance hash，加入四季一級至少五種且有高級新品的契約測試。`.venv/bin/python -m pytest tests/game/test_farm_contract.py -q` 5項通過；App跨repo hash檢查3組一致，規則／經濟golden未改。App完整改動見該repo `docs/changes/2026-10-14-v4-m3-farm-polish.md`。未部署／合併main。
