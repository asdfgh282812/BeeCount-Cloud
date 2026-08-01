"""借還款追蹤 write endpoints(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/debts。`principal_amount`/
`direction` 建立后不可改(见 `WriteDebtUpdateRequest` docstring),PATCH 只
暴露 counterparty_name/due_at/note。DELETE 只允许在这笔欠款还没收到任何
还款交易时执行(跟 §2.3 installment_plan 的删除限制同一取舍),校验直接查
`read_tx_projection.debt_sync_id` 反查交易,不需要额外的 remaining_amount
缓存列。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


def _assert_debt_has_no_repayments(db: Session, *, ledger_id: str, debt_id: str) -> None:
    has_repayment = db.scalar(
        select(ReadTxProjection.sync_id).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.debt_sync_id == debt_id,
        ).limit(1)
    )
    if has_repayment is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot delete a debt that already has repayment transactions",
        )


@router.post(
    "/ledgers/{ledger_id}/debts",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_debt_api(
    ledger_id: str,
    req: WriteDebtCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_create",
        mutate=lambda snapshot: create_debt(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/debts/{debt_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_debt_api(
    ledger_id: str,
    debt_id: str,
    req: WriteDebtUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_update",
        mutate=lambda snapshot: (update_debt(snapshot, debt_id, mutate_payload), debt_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/debts/{debt_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_debt_api(
    ledger_id: str,
    debt_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    _assert_debt_has_no_repayments(db, ledger_id=ledger.id, debt_id=debt_id)
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_delete",
        mutate=lambda snapshot: (delete_debt(snapshot, debt_id, mutate_payload), debt_id),
    )
