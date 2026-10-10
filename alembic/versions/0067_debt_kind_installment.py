"""應收應付款項 MOZE 化(App v68):欠款投影補上款項類型與分期欄位

App v68 的 debt payload 多了 `kind`(new / existing)、`startedAt`、
`installmentCount`、`installmentNo`、`installmentGroupId`。以前
`_LEDGER_MERGE_SPECS["debt"]` 沒有這幾個鍵,投影時被丟掉;pull 回傳原始
payload,所以 App↔App 沒掉資料,只是 Web 看不到。

回填:每筆欠款投影的 `source_change_id` 指向最後一次寫它的 SyncChange,
那筆 payload 就是 App 送來的原始 JSON,從裡面取出這幾個鍵。

Revision ID: 0067_debt_kind_installment
Revises: 0066_split_debt_lines
Create Date: 2026-10-10
"""

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0067_debt_kind_installment"
down_revision = "0066_split_debt_lines"
branch_labels = None
depends_on = None


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_dt(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def upgrade() -> None:
    op.add_column(
        "read_debt_projection",
        sa.Column("kind", sa.String(16), nullable=False, server_default="new"),
    )
    op.add_column(
        "read_debt_projection",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "read_debt_projection",
        sa.Column("installment_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "read_debt_projection",
        sa.Column("installment_no", sa.Integer(), nullable=True),
    )
    op.add_column(
        "read_debt_projection",
        sa.Column("installment_group_id", sa.String(255), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT d.ledger_id, d.sync_id, c.payload_json "
        "FROM read_debt_projection d "
        "JOIN sync_changes c ON c.change_id = d.source_change_id"
    )).all()
    for ledger_id, sync_id, payload_json in rows:
        payload = payload_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        kind = payload.get("kind")
        values = {
            "kind": kind if kind in ("new", "existing") else "new",
            "started_at": _as_dt(payload.get("startedAt")),
            "installment_count": _as_int(payload.get("installmentCount")),
            "installment_no": _as_int(payload.get("installmentNo")),
            "installment_group_id": (
                str(payload["installmentGroupId"]) if payload.get("installmentGroupId") else None
            ),
        }
        if values == {
            "kind": "new", "started_at": None, "installment_count": None,
            "installment_no": None, "installment_group_id": None,
        }:
            continue
        bind.execute(sa.text(
            "UPDATE read_debt_projection SET kind = :kind, started_at = :started_at, "
            "installment_count = :installment_count, installment_no = :installment_no, "
            "installment_group_id = :installment_group_id "
            "WHERE ledger_id = :ledger_id AND sync_id = :sync_id"
        ), {**values, "ledger_id": ledger_id, "sync_id": sync_id})


def downgrade() -> None:
    op.drop_column("read_debt_projection", "installment_group_id")
    op.drop_column("read_debt_projection", "installment_no")
    op.drop_column("read_debt_projection", "installment_count")
    op.drop_column("read_debt_projection", "started_at")
    op.drop_column("read_debt_projection", "kind")
