"""read_tx_projection.deferred_posting_at + read_tx_projection.reconciled_at

§2.10 分析與對帳(MOZE_FEATURE_GAP_SD.md Phase 5)的新欄位,按 CLAUDE.md
「新增 entity」checklist。`deferred_posting_at` 是延後入帳(對帳模式的必要
前置,見文件 §2.10「延後入帳」小節)。`reconciled_at`(2026-08-09 改版:
對帳模式重新設計為 Moze 式「逐筆核對本期帳單交易」的勾選清單,不是原本
v1 的「單筆餘額比對記錄」——不再需要獨立的 reconciliation entity/表,
一筆交易只需要記「有沒有被使用者在對帳模式裡勾選確認過」這一個布爾狀態,
所以直接是 `read_tx_projection` 自己的欄位,跟 `deferred_posting_at` 同一種
"tx 自身的核對狀態"語意)。

Revision ID: 0035_deferred_posting_and_reconciliation
Revises: 0034_tx_reward_source
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op


revision = "0035_deferred_posting_and_reconciliation"
down_revision = "0034_tx_reward_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_tx_projection",
        sa.Column("deferred_posting_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "read_tx_projection",
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "reconciled_at")
    op.drop_column("read_tx_projection", "deferred_posting_at")
