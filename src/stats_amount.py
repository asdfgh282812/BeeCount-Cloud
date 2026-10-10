"""拆帳欠款明細(App v67)的收支統計口徑,唯一定義處。

拆帳裡的欠款明細(支出拆帳=應收、收入拆帳=應付)不計入收支統計和預算:錢確實
整筆進出帳戶(帳戶餘額、信用卡帳單、回饋照整筆 amount 算),但那部分是借貸,
不是消費或收入。

統計金額 = 本位幣金額(native_amount ?? amount)× 非欠款占比
         = native × (amount − debt_split_amount) / amount

對齊 App `lib/utils/debt_split_stats.dart` 的 `statsAmountOf`/`statsAmountSql`,
兩邊改一邊要同步改另一邊。`debt_split_amount` 由 `projection.upsert_tx` 從 splits
算出(見 `ReadTxProjection.debt_split_amount`)。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import case, func, or_

from .models import ReadTxProjection


def stats_amount(amount: float | None, native_amount: float | None, debt_split_amount: float | None) -> float:
    raw = float(amount or 0.0)
    native = float(native_amount) if native_amount is not None else raw
    debt = float(debt_split_amount or 0.0)
    if debt == 0 or raw == 0:
        return native
    return native * (raw - debt) / raw


def stats_amount_expr(model: Any = ReadTxProjection):
    """[stats_amount] 的 SQL 版本,[model] 是 ReadTxProjection 或它的 aliased。"""
    native = func.coalesce(model.native_amount, model.amount)
    ratio = case(
        (or_(model.debt_split_amount == 0, model.amount == 0), 1.0),
        else_=(model.amount - model.debt_split_amount) / model.amount,
    )
    return native * ratio


def debt_split_total(splits: Any) -> float:
    """splits(camelCase dict 列表,或它的 JSON 字串)裡欠款明細的金額合計。"""
    if isinstance(splits, str):
        try:
            splits = json.loads(splits)
        except (TypeError, ValueError):
            return 0.0
    if not isinstance(splits, list):
        return 0.0
    total = 0.0
    for entry in splits:
        if isinstance(entry, dict) and entry.get("debtId"):
            try:
                total += float(entry.get("amount") or 0.0)
            except (TypeError, ValueError):
                continue
    return total
