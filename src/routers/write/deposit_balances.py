"""訂金尾款追蹤 write endpoints(doc.moze.app/record/deposit)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/deposit-balances,外加
`POST .../{id}/complete`(補尾款:同一次寫入建立真實交易並把尾款翻成
completed)。語意見 `ReadDepositBalanceProjection` docstring;mutator 在
`snapshot_mutator.py` 的「訂金尾款追蹤」段。mutator 內的 ValueError 由
`_commit_write` 統一轉成 400。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...schemas import (
    WriteDepositBalanceCompleteRequest,
    WriteDepositBalanceCreateRequest,
    WriteDepositBalanceUpdateRequest,
)
from ...snapshot_mutator import (
    complete_deposit_balance,
    create_deposit_balance,
    delete_deposit_balance,
    update_deposit_balance,
)

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/deposit-balances",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_deposit_balance_api(
    ledger_id: str,
    req: WriteDepositBalanceCreateRequest,
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
        audit_action="web_deposit_balance_create",
        mutate=lambda snapshot: create_deposit_balance(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/deposit-balances/{balance_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_deposit_balance_api(
    ledger_id: str,
    balance_id: str,
    req: WriteDepositBalanceUpdateRequest,
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
        audit_action="web_deposit_balance_update",
        mutate=lambda snapshot: (
            update_deposit_balance(snapshot, balance_id, mutate_payload),
            balance_id,
        ),
    )


@router.delete(
    "/ledgers/{ledger_id}/deposit-balances/{balance_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_deposit_balance_api(
    ledger_id: str,
    balance_id: str,
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
        audit_action="web_deposit_balance_delete",
        mutate=lambda snapshot: (
            delete_deposit_balance(snapshot, balance_id, mutate_payload),
            balance_id,
        ),
    )


@router.post(
    "/ledgers/{ledger_id}/deposit-balances/{balance_id}/complete",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def complete_deposit_balance_api(
    ledger_id: str,
    balance_id: str,
    req: WriteDepositBalanceCompleteRequest,
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
    # 主帳戶(account_group)不能當扣款帳戶,同一般交易。
    _assert_account_not_group(
        db, user_id=current_user.id, account_id=req.account_id, field_name="account_id",
    )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    # 沒指定入帳日 → 今天(UTC 當下);mutator 的 _to_iso8601(None) 也會落到現在。
    if mutate_payload.get("happened_at") is None:
        mutate_payload["happened_at"] = _utcnow()
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_deposit_balance_complete",
        mutate=lambda snapshot: complete_deposit_balance(snapshot, balance_id, mutate_payload)[:2],
    )
