"""匯入範本產生(CSV / Excel,2026-08 新增)。

三種 entity type 各自固定欄位:
- transactions:直接沿用既有匯出/匯入的 BeeCount 12 欄格式(`read/workspace.py`
  的 `_CSV_HEADERS_BY_LANG`),填好範本上傳後可以直接走現有交易匯入流程
  round-trip,不需要新的解析邏輯。
- categories / accounts:對應 `simple_parser.py` 認得的固定欄位。

CSV 走 stdlib `csv` 模組(欄位含中文逗號時自動加引號);Excel 走 `openpyxl`
寫入(讀取路徑本來就依賴這個套件,寫入不需要額外依賴)。
"""
from __future__ import annotations

import csv
import io
from typing import Literal

Lang = Literal["zh-CN", "zh-TW", "en"]

_TX_HEADERS: dict[Lang, list[str]] = {
    "zh-CN": ["类型", "分类", "二级分类", "金额", "币种", "账户", "转出账户",
              "转入账户", "备注", "时间", "标签", "附件"],
    "zh-TW": ["類型", "分類", "二級分類", "金額", "幣種", "帳戶", "轉出帳戶",
              "轉入帳戶", "備註", "時間", "標籤", "附件"],
    "en":    ["Type", "Category", "Subcategory", "Amount", "Currency",
              "Account", "From Account", "To Account", "Note", "Time",
              "Tags", "Attachments"],
}

_TX_EXAMPLES: dict[Lang, list[list[str]]] = {
    "zh-TW": [
        ["支出", "餐飲", "", "120", "TWD", "現金", "", "", "午餐", "2026-08-01 12:30:00", "", ""],
        ["收入", "薪資", "", "50000", "TWD", "銀行", "", "", "", "2026-08-05 09:00:00", "", ""],
    ],
    "zh-CN": [
        ["支出", "餐饮", "", "120", "TWD", "现金", "", "", "午餐", "2026-08-01 12:30:00", "", ""],
        ["收入", "薪资", "", "50000", "TWD", "银行", "", "", "", "2026-08-05 09:00:00", "", ""],
    ],
    "en": [
        ["expense", "Food", "", "120", "TWD", "Cash", "", "", "Lunch", "2026-08-01 12:30:00", "", ""],
        ["income", "Salary", "", "50000", "TWD", "Bank", "", "", "", "2026-08-05 09:00:00", "", ""],
    ],
}

_CATEGORY_HEADERS: dict[Lang, list[str]] = {
    "zh-CN": ["名称", "类型", "父分类"],
    "zh-TW": ["名稱", "類型", "父分類"],
    "en":    ["Name", "Type", "Parent Category"],
}

_CATEGORY_EXAMPLES: dict[Lang, list[list[str]]] = {
    "zh-TW": [["餐飲", "支出", ""], ["日用品", "支出", ""], ["薪資", "收入", ""]],
    "zh-CN": [["餐饮", "支出", ""], ["日用品", "支出", ""], ["薪资", "收入", ""]],
    "en": [["Food", "expense", ""], ["Groceries", "expense", ""], ["Salary", "income", ""]],
}

_ACCOUNT_HEADERS: dict[Lang, list[str]] = {
    "zh-CN": ["名称", "类型", "币别", "期初余额", "主账户名称", "信用额度", "账单日", "还款日"],
    "zh-TW": ["名稱", "類型", "幣別", "期初餘額", "主帳戶名稱", "信用額度", "帳單日", "繳款日"],
    "en":    ["Name", "Type", "Currency", "Initial Balance", "Parent Account Name",
              "Credit Limit", "Billing Day", "Payment Due Day"],
}

# 附注:「類型」欄可以填 UI 上顯示的名稱(如「現金」「信用卡」)或內部值
# (cash/credit_card/...),`simple_parser.py::_ACCOUNT_TYPE_ALIASES` 同時接受
# 兩種、也接受簡繁英三語系標籤。
_ACCOUNT_EXAMPLES: dict[Lang, list[list[str]]] = {
    "zh-TW": [
        ["現金", "現金", "TWD", "1000", "", "", "", ""],
        ["國泰信用卡", "信用卡", "TWD", "0", "", "50000", "5", "20"],
    ],
    "zh-CN": [
        ["现金", "现金", "TWD", "1000", "", "", "", ""],
        ["招商信用卡", "信用卡", "TWD", "0", "", "50000", "5", "20"],
    ],
    "en": [
        ["Cash", "Cash", "TWD", "1000", "", "", "", ""],
        ["Chase Credit Card", "Credit card", "TWD", "0", "", "50000", "5", "20"],
    ],
}


def _normalize_lang(lang: str | None) -> Lang:
    if not lang:
        return "en"
    s = lang.strip().lower().replace("_", "-")
    if s.startswith("zh-tw") or s in {"zh-hant", "zh-hk", "zh-mo"}:
        return "zh-TW"
    if s.startswith("zh"):
        return "zh-CN"
    return "en"


def _build_csv(headers: list[str], examples: list[list[str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in examples:
        writer.writerow(row)
    # BOM 前缀:跟既有匯出端點一致,Excel 開啟中文不亂碼
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _build_xlsx(headers: list[str], examples: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in examples:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


EntityType = Literal["transactions", "categories", "accounts"]

_HEADERS_BY_ENTITY: dict[EntityType, dict[Lang, list[str]]] = {
    "transactions": _TX_HEADERS,
    "categories": _CATEGORY_HEADERS,
    "accounts": _ACCOUNT_HEADERS,
}
_EXAMPLES_BY_ENTITY: dict[EntityType, dict[Lang, list[list[str]]]] = {
    "transactions": _TX_EXAMPLES,
    "categories": _CATEGORY_EXAMPLES,
    "accounts": _ACCOUNT_EXAMPLES,
}


def build_template(
    *, entity_type: EntityType, fmt: Literal["csv", "xlsx"], lang: str | None,
) -> tuple[bytes, str]:
    """回傳 (file_bytes, filename)。"""
    lang_key = _normalize_lang(lang)
    headers = _HEADERS_BY_ENTITY[entity_type][lang_key]
    examples = _EXAMPLES_BY_ENTITY[entity_type][lang_key]
    body = _build_csv(headers, examples) if fmt == "csv" else _build_xlsx(headers, examples)
    filename = f"beecount-{entity_type}-template.{fmt}"
    return body, filename
