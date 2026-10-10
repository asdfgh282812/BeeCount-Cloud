"""分類 kind 的唯一列舉。

除了 expense / income / transfer(虛擬轉帳分類),2026-10-10 起多了欠款分類
receivable / payable(App 記帳頁「應收」「應付」分頁上方的分類網格,MOZE 的
借出/代付/報帳、借入/信貸/車貸/房貸)。欠款起點交易的 tx_type 仍是
expense(應收)/ income(應付),但掛的分類 kind 是 receivable / payable,所以
任何地方都不能假設 `category.kind == tx_type`。
"""

from typing import Literal

CategoryKind = Literal["expense", "income", "transfer", "receivable", "payable"]

CATEGORY_KINDS: frozenset[str] = frozenset(
    {"expense", "income", "transfer", "receivable", "payable"}
)

DEBT_CATEGORY_KINDS: frozenset[str] = frozenset({"receivable", "payable"})
