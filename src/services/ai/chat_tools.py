"""Financial assistant's read-only allowlist, backed by the normal read APIs.

The model never supplies identity, permissions or ledger IDs. Query defaults are
unwrapped explicitly because calling a FastAPI handler does not run DI.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi.encoders import jsonable_encoder
from fastapi.params import Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import User, UserAccountProjection
from ...routers.read import ledgers, securities, workspace
from ..business_time import business_tz


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["month", "year", "all"] = "month"
    metric: Literal["expense", "income"] = "expense"
    period: str | None = Field(default=None, pattern=r"^\d{4}(-(?:0[1-9]|1[0-2]))?$" )
    day_from: date | None = None
    day_to: date | None = None
    q: str | None = Field(default=None, max_length=200)
    account_name: str | None = Field(default=None, max_length=200)
    account_id: str | None = Field(default=None, max_length=100)
    tx_type: Literal["income", "expense", "transfer"] | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)
    year: int | None = Field(default=None, ge=1990, le=2200)
    symbol: str | None = Field(default=None, max_length=40)
    amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    merchant: str = Field(default="", max_length=200)
    cycle_offset: int = Field(default=0, ge=-60, le=1)

    @model_validator(mode="after")
    def check_ranges(self):
        if self.day_from and self.day_to and self.day_from > self.day_to:
            raise ValueError("day_from must precede day_to")
        if self.period and self.scope != "all":
            if (self.scope == "month" and len(self.period) != 7) or (self.scope == "year" and len(self.period) != 4):
                raise ValueError("period must match scope")
        return self


# name: (handler, permitted model arguments, description). All are read-only.
TOOLS: dict[str, tuple[Any, set[str], str]] = {
    "analytics": (workspace.workspace_analytics, {"scope", "metric", "period"},
                  "收支總額、分類排名、逐日/月序列。scope=month/year/all；period=YYYY-MM/YYYY。自然月，使用帳本本位幣；不可用交易清單加總代替。"),
    "transactions": (workspace.list_workspace_transactions, {"day_from", "day_to", "q", "account_name", "tx_type", "limit", "offset"},
                     "交易明細；day_from/day_to=YYYY-MM-DD（含起訖日），q=備註關鍵字，account_name=帳戶名稱。回傳 total 與分頁，不能把一頁當全部。"),
    "accounts": (workspace.list_workspace_accounts, {"q", "limit", "offset"}, "帳戶與目前餘額，包含帳戶 id，可供信用卡帳單查詢。"),
    "budgets": (ledgers.list_budgets_usage, set(), "預算及實際使用情況。"),
    "debts": (ledgers.list_debts, set(), "應收應付、欠款與收還款。"),
    "recurring": (ledgers.list_recurring_rules, set(), "週期性收支規則。"),
    "installments": (ledgers.list_installment_plans, set(), "分期付款計畫。"),
    "projects": (ledgers.list_projects, set(), "專案與進度。"),
    "holdings": (securities.workspace_holdings, {"account_id"}, "使用者所有帳本的股票持股、成本與快取報價，非即時價格。"),
    "stock_trades": (securities.list_stock_trades, {"account_id", "symbol"}, "目前帳本股票交易。"),
    "realized_pnl": (securities.workspace_realized_pnl, {"account_id", "year", "symbol"}, "使用者所有帳本的股票已實現損益。"),
    "pending_dividends": (securities.list_pending_dividends, {"limit"}, "使用者所有帳本待確認股利。"),
    "card_recommendation": (ledgers.get_card_recommendation, {"amount", "merchant"}, "刷卡推薦，必填 amount>0，可帶 merchant。僅使用者詢問刷哪張卡時使用。"),
    "card_bill": (ledgers.get_account_billing_summary, {"account_id", "account_name", "cycle_offset"}, "信用卡帳單，必填 account_name（完整帳戶名稱）或已知的 account_id，cycle_offset=0 本期/-1 上期。"),
}


async def execute_tool(name: str, arguments: dict, *, db: Session, user: User, ledger_id: str, scopes: set[str]) -> Any:
    if name not in TOOLS:
        raise ValueError("Unknown read tool")
    handler, allowed, _ = TOOLS[name]
    if set(arguments) - allowed:
        raise ValueError("Unsupported tool arguments")
    args = ToolArgs.model_validate(arguments)
    if name == "card_recommendation" and args.amount is None:
        raise ValueError("amount is required")
    values = args.model_dump(include=allowed, exclude_none=True)
    if name == "card_bill":
        if args.account_name and not args.account_id:
            accounts = db.scalars(select(UserAccountProjection).where(
                UserAccountProjection.user_id == user.id,
                UserAccountProjection.name == args.account_name,
                UserAccountProjection.account_type == "credit_card",
            )).all()
            if len(accounts) != 1:
                raise ValueError("Specify an unambiguous credit card name")
            values["account_id"] = accounts[0].sync_id
        values.pop("account_name", None)
        if not values.get("account_id"):
            raise ValueError("account_name or account_id is required")
    # Apply validated defaults, then immutable identity/context. Never forward a
    # model-generated dict directly into a handler's **kwargs.
    kwargs = {}
    for key, parameter in inspect.signature(handler).parameters.items():
        if isinstance(parameter.default, Query):
            kwargs[key] = parameter.default.default
    kwargs.update(values)
    kwargs.update(db=db, current_user=user, _scopes=scopes)
    params = inspect.signature(handler).parameters
    if "ledger_id" in params:
        kwargs["ledger_id"] = ledger_id
    if "ledger_external_id" in params:
        kwargs["ledger_external_id"] = ledger_id
    if "user_id" in params:
        kwargs["user_id"] = None
    if "refresh" in params:
        kwargs["refresh"] = False
    if "natural_month" in params:
        kwargs["natural_month"] = True
    if "tz_offset_minutes" in params:
        offset = datetime.now(timezone.utc).astimezone(business_tz()).utcoffset()
        kwargs["tz_offset_minutes"] = int(offset.total_seconds() // 60) if offset else 0
    result = handler(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return jsonable_encoder(result)


def bounded_result(value: Any) -> Any:
    """Bound provider context structurally; never cut JSON mid-string."""
    if isinstance(value, list):
        if len(value) > 100:
            return {"items": [bounded_result(x) for x in value[:100]], "total": len(value), "truncated": True}
        return [bounded_result(x) for x in value]
    if isinstance(value, dict):
        return {k: bounded_result(v) for k, v in value.items()}
    if isinstance(value, str):
        return value[:2000]
    return value
