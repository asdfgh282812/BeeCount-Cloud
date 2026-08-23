"""POST /ledgers/{ledger_id}/debts/rename-counterparty —— 對象改名(§5.4)。

`counterpartyName` 目前只是自由文字欄位,沒有獨立實體。對齐 Moze「在設定裡
改對象名稱會連動該對象所有記錄」,這個端點一次改同一帳本下所有
`counterparty_name == old_counterparty_name` 的欠款,不是像
`PATCH .../debts/{debt_id}` 那樣只改一筆(那個端點的 `counterparty_name`
語意維持不變——只改單筆,不 cascade,避免其他呼叫者被暗地的批次副作用
影響)。

跟 `transactions_batch_delete.py` 同模式:不走共用的單實體 `_commit_write`
(因為要一次影響多筆同類型實體),手捲跟 `_shared._commit_write` 內部一致
的 lock + build snapshot + diff + commit 流程。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import snapshot_builder
from ...concurrency import lock_ledger_for_materialize
from ...database import get_db
from ...deps import get_current_user
from ...models import AuditLog, SyncPushIdempotency, User
from ...snapshot_mutator import rename_debt_counterparty
from ._shared import (
    _OWNER_ONLY_ROLES,
    _WRITE_RESPONSES,
    _WRITE_SCOPE_DEP,
    _emit_entity_diffs,
    _hash_request,
    _load_idempotent_response,
    _payload_with_actor,
    _prepare_write,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class BulkRenameDebtCounterpartyRequest(BaseModel):
    old_counterparty_name: str = Field(min_length=1, max_length=255)
    new_counterparty_name: str = Field(min_length=1, max_length=255)
    base_change_id: int = 0


class BulkRenameDebtCounterpartyResponse(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    renamed_debt_ids: list[str] = Field(default_factory=list)


@router.post(
    "/ledgers/{ledger_id}/debts/rename-counterparty",
    response_model=BulkRenameDebtCounterpartyResponse,
    responses=_WRITE_RESPONSES,
)
async def rename_debt_counterparty_api(
    ledger_id: str,
    req: BulkRenameDebtCounterpartyRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkRenameDebtCounterpartyResponse:
    payload_for_idem = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        # 跟单笔 debt create/update/delete 一致:只有 ledger owner 能寫欠款。
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload_for_idem,
    )
    if replay:
        from sqlalchemy import select as _select

        row = db.scalar(
            _select(SyncPushIdempotency).where(
                SyncPushIdempotency.user_id == current_user.id,
                SyncPushIdempotency.device_id == device_id,
                SyncPushIdempotency.idempotency_key == idempotency_key,
            )
        )
        if row is not None and row.response_json:
            return BulkRenameDebtCounterpartyResponse.model_validate(row.response_json)
        return BulkRenameDebtCounterpartyResponse(
            ledger_id=ledger.external_id,
            base_change_id=req.base_change_id,
            new_change_id=replay.new_change_id,
            server_timestamp=replay.server_timestamp,
            renamed_debt_ids=[],
        )

    lock_ledger_for_materialize(db, ledger.id)
    snapshot = snapshot_builder.build(db, ledger)
    prev_snapshot = {**snapshot}
    for _k in (
        "items", "accounts", "categories", "tags", "budgets",
        "recurringRules", "installmentPlans", "installmentPeriods",
        "debts", "txTemplates",
    ):
        arr = snapshot.get(_k)
        if isinstance(arr, list):
            prev_snapshot[_k] = [dict(e) if isinstance(e, dict) else e for e in arr]

    mutate_payload = _payload_with_actor(payload_for_idem, current_user, ledger=ledger)
    try:
        snapshot, renamed_ids = rename_debt_counterparty(snapshot, mutate_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if not renamed_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no debt found with the given counterparty name",
        )

    now = datetime.now(timezone.utc)
    emitted_change_ids = _emit_entity_diffs(
        db,
        ledger=ledger,
        current_user=current_user,
        device_id=device_id,
        prev=prev_snapshot,
        next_snapshot=snapshot,
        now=now,
    )
    new_change_id = max(emitted_change_ids) if emitted_change_ids else (
        snapshot_builder.latest_change_id(db, ledger.id)
    )

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_debt_rename_counterparty",
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": req.base_change_id,
                "newChangeId": new_change_id,
                "oldCounterpartyName": req.old_counterparty_name,
                "newCounterpartyName": req.new_counterparty_name,
                "renamedCount": len(renamed_ids),
                "renamedIds": renamed_ids,
            },
        )
    )

    response = BulkRenameDebtCounterpartyResponse(
        ledger_id=ledger.external_id,
        base_change_id=req.base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        renamed_debt_ids=renamed_ids,
    )

    request_hash = _hash_request(request.method, request.url.path, payload_for_idem)
    if idempotency_key:
        db.add(
            SyncPushIdempotency(
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_json=response.model_dump(mode="json"),
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if idempotency_key:
            replay_resp = _load_idempotent_response(
                db,
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay_resp is not None:
                return (
                    BulkRenameDebtCounterpartyResponse(**replay_resp.model_dump())
                    if hasattr(replay_resp, "model_dump")
                    else replay_resp
                )  # type: ignore[return-value]
        raise

    logger.info(
        "debt.rename_counterparty ledger=%s renamed=%d change_id=%d device=%s user=%s",
        ledger.external_id, len(renamed_ids), new_change_id, device_id, current_user.id,
    )

    from ...websocket_manager import broadcast_to_ledger
    await broadcast_to_ledger(
        db=db,
        ws_manager=request.app.state.ws_manager,
        ledger_id=ledger.id,
        payload={
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": new_change_id,
            "serverTimestamp": now.isoformat(),
        },
    )
    return response
