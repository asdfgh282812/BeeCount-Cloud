"""延後入帳(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5,對帳模式的必要前置)。

信用卡消費日跟銀行實際請款日常有時間差,使用者可以把某筆交易標記「延後
入帳」並填入實際入帳日(`ReadTxProjection.deferred_posting_at`)。任何要
按「這筆錢算哪一期帳單/哪個對帳週期」分組的邏輯,排序/篩選鍵都應該用
`COALESCE(deferred_posting_at, happened_at)`,而不是單純 `happened_at`
——這裡是唯一權威實現,SQL 聚合查詢用 `attribution_date_expr()`,單筆
Python 物件(ORM row / 純 datetime pair)用 `attribution_date()`。

目前實際套用這個 COALESCE 的地方(對齊文件「共用同一個 helper」的要求):
對帳模式的「這期帳單」交易篩選/分組(`read/ledgers.py::
get_account_statement`)、對帳模式清除確認的週期邊界計算
(`write/accounts.py::clear_statement_confirmations_ep`)、信用卡帳單彙總
(`services/credit_card_billing.py` 的週期窗口查詢)、信用卡紅利回饋計算
(`services/card_rewards.py::_attribution_date`)。
`workspace_analytics`/`summary` 等主要收支統計刻意維持 `happened_at`
口徑不變(對齐文件 §2.10 用詞「若也要看『入帳日』口徑」的「若」——這是
可選需求,不是這幾個既有、已測過的核心統計端點的必做項,避免不必要的
大範圍改動)。

因為 `deferred_posting_at` 對既有資料一律是 NULL,`COALESCE(...,
happened_at)` 在沒有人使用這個新欄位之前跟原本直接用 `happened_at` 的行為
完全等價 —— 套用這個 helper 對舊資料是零風險的向後相容改動。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

from ..models import ReadTxProjection


def attribution_date_expr() -> ColumnElement:
    """SQL 表達式:`COALESCE(deferred_posting_at, happened_at)`。用在
    `select(...)`/`.where(...)` 裡代替直接引用 `ReadTxProjection.happened_at`
    的地方。"""
    return func.coalesce(
        ReadTxProjection.deferred_posting_at, ReadTxProjection.happened_at
    )


def attribution_date(tx: ReadTxProjection) -> datetime:
    """Python 端:單一 `ReadTxProjection` row 的歸屬日期。SQLite 讀回來的
    DateTime 可能是 naive 的,補 UTC 才能跟其它 aware datetime 比較。"""
    value = tx.deferred_posting_at or tx.happened_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value
