"""新帳本預設分類種子資料。

Web 端建立新帳本(`POST /ledgers`)時沒有 mobile 的
`lib/services/data/seed_service.dart` 可以在本地先種好分類再同步上來 ——
使用者建完帳本進來看到空分類清單,記第一筆帳前得先手動建一輪分類,體驗很差。

本模組提供「開箱即用」的預設分類樹(食衣住行育樂等常見支出/收入項目),參考
`docs/beecount-categories-template.csv` 整理、調整少數過於瑣碎或跟群組同名的
項目,並統一指定清晰易辨識、彼此不重複的 Material Symbols 圖示名(值全部落在
`frontend/packages/web-features/src/lib/categoryIconMap.ts` 的 `KNOWN_NAMES`
集合裡,保證 web/mobile 都能正確渲染,不會 fallback 成預設的 `category` 圖示)。

呼叫端(`src/routers/write/ledgers.py::create_ledger`、
`scripts/backfill_default_categories.py`)透過
`build_default_category_payloads()` 拿到攤平後的 payload list,依序丟給
`snapshot_mutator.create_category()` ——父分類(level=1)一定排在對應子分類
(level=2)前面,因為子分類靠 `parent_name` 字串引用父分類名字。
"""
from __future__ import annotations

# 群組定義:(父分類名, 父分類圖示, [(子分類名, 子分類圖示), ...])
# 子分類 list 為空 → 這個群組只有自己一項,不建立子分類,父分類本身(level=1)
# 就是可以直接記帳用的分類(對齊範本裡「父分類欄位=自己名字」的單項群組:
# 手續費/利息支出/其他/折扣/紅利回饋)。
_Group = tuple[str, str, list[tuple[str, str]]]

EXPENSE_GROUPS: list[_Group] = [
    ("生活", "weekend", [
        ("住宿", "bed"),
        ("按摩", "spa"),
        ("派對", "celebration"),
        ("美容美髮", "content_cut"),
        ("旅行", "explore"),
    ]),
    ("交通", "traffic", [
        ("手扶梯", "move_down"),
        ("火車", "train"),
        ("加油費", "local_gas_station"),
        ("汽車", "directions_car"),
        ("計程車", "local_taxi"),
        ("停車費", "local_parking"),
        ("捷運", "directions_subway"),
        ("船票", "directions_boat"),
        ("電池費用", "electric_bolt"),
        ("摩托車", "motorcycle"),
        ("機票", "flight"),
    ]),
    ("個人", "face", [
        ("投資", "show_chart"),
        ("社交", "group"),
        ("保險", "security"),
        ("借款", "currency_exchange"),
        ("捐款", "volunteer_activism"),
        ("通話費", "ring_volume"),
        ("稅金", "account_balance"),
        ("請客", "restaurant_menu"),
    ]),
    ("娛樂", "theater_comedy", [
        ("音樂", "music_note"),
        ("展覽", "palette"),
        ("消遣", "nightlife"),
        ("遊樂園", "attractions"),
        ("遊戲", "sports_esports"),
        ("電影", "movie"),
        ("影音", "videocam"),
    ]),
    ("家居", "house", [
        ("日常用品", "shopping_basket"),
        ("洗衣費", "local_laundry_service"),
        ("修繕費", "handyman"),
        ("家具", "chair"),
        ("家電", "kitchen"),
        ("電費", "lightbulb"),
    ]),
    ("家庭", "family_restroom", [
        ("生活費", "payments"),
    ]),
    ("飲食", "restaurant", [
        ("午餐", "lunch_dining"),
        ("水果", "nutrition"),
        ("早餐", "breakfast_dining"),
        ("晚餐", "dinner_dining"),
        ("飲料", "local_cafe"),
        ("點心", "cookie"),
    ]),
    ("學習", "school", [
        ("書籍", "menu_book"),
        ("課程", "auto_stories"),
        ("證書", "workspace_premium"),
    ]),
    ("應收款項", "request_quote", [
        ("代付", "payment"),
        ("借出", "call_made"),
        ("報帳", "receipt_long"),
    ]),
    ("購物", "shopping_cart", [
        ("紀念品", "redeem"),
        ("訂閱", "subscriptions"),
        ("配件", "watch"),
        ("電子產品", "devices"),
        ("鞋子", "checkroom"),
        ("應用軟體", "smartphone"),
        ("禮物", "card_giftcard"),
    ]),
    ("醫療", "local_hospital", [
        ("門診", "medical_services"),
        ("醫療用品", "healing"),
        ("藥品", "medication"),
    ]),
    ("手續費", "price_change", []),
    ("利息支出", "trending_down", []),
    ("其他", "inventory_2", []),
]

INCOME_GROUPS: list[_Group] = [
    ("收入", "savings", [
        ("生活費", "account_balance_wallet"),
        ("回饋金", "loyalty"),
        ("收款", "call_received"),
        ("利息", "monetization_on"),
        ("投資", "trending_up"),
        ("家教", "psychology"),
        ("買賣", "sell"),
        ("獎金", "military_tech"),
        ("薪水", "work"),
    ]),
    ("折扣", "local_offer", []),
    ("紅利回饋", "emoji_events", []),
]


def build_default_category_payloads() -> list[dict]:
    """展開成 `snapshot_mutator.create_category()` 能吃的 payload list。

    父分類一定排在自己的子分類前面(呼叫端依序 create_category 時,子分類的
    `parent_name` 才有東西可以引用 —— 雖然 create_category 本身不校驗 parent
    是否存在,但 sort_order 遞增的順序視覺上也該父在前子在後)。
    """
    payloads: list[dict] = []
    sort_order = 0
    for kind, groups in (("expense", EXPENSE_GROUPS), ("income", INCOME_GROUPS)):
        for parent_name, parent_icon, children in groups:
            sort_order += 1
            payloads.append({
                "name": parent_name,
                "kind": kind,
                "level": 1,
                "sort_order": sort_order,
                "icon": parent_icon,
                "icon_type": "material",
                "parent_name": None,
            })
            for child_name, child_icon in children:
                sort_order += 1
                payloads.append({
                    "name": child_name,
                    "kind": kind,
                    "level": 2,
                    "sort_order": sort_order,
                    "icon": child_icon,
                    "icon_type": "material",
                    "parent_name": parent_name,
                })
    return payloads
