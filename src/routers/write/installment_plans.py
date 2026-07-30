"""Installment plans write endpoints(§2.3 MOZE_FEATURE_GAP_SD.md）。

POST / PATCH / DELETE for /ledgers/{ledger_id}/installment-plans。跟
budgets.py 同款 boilerplate,但 POST 比较特殊:按文档「建立計畫，通常伴隨
建立第一期交易」,在同一个 `_commit_write` 事务里先建计画、再建第一期交易
(带 `installment_plan_id` 反查),两条 SyncChange 一次提交,mobile/web 刷新
只需要一次 pull。剩余各期由 `services.recurring_materializer` 到期时补上。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...snapshot_mutator import create_transaction as _mutate_create_tx

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/installment-plans",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_installment_plan_ep(
    ledger_id: str,
    req: WriteInstallmentPlanCreateRequest,
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

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        next_snapshot, plan_id = create_installment_plan(snapshot, mutate_payload)
        plans = next_snapshot.get("installmentPlans") or []
        plan = next((p for p in plans if p.get("syncId") == plan_id), None)
        first_period_amount = plan["periodAmount"] if plan else req.total_amount / req.periods
        first_period_at = plan["firstPeriodAt"] if plan else payload.get("first_period_at")
        tx_payload = {
            "tx_type": "expense",
            "amount": first_period_amount,
            "happened_at": first_period_at,
            "note": req.note,
            "category_id": req.category_id,
            "account_id": req.account_id,
            "__actor_user_id": mutate_payload.get("__actor_user_id"),
            "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
            "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
        }
        next_snapshot, tx_id = _mutate_create_tx(next_snapshot, tx_payload)
        items = next_snapshot.get("items") or []
        tx_item = next((it for it in items if it.get("syncId") == tx_id), None)
        if tx_item is not None:
            tx_item["installmentPlanId"] = plan_id
        return next_snapshot, plan_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_installment_plan_create",
        mutate=_mutate,
    )


@router.patch(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_installment_plan_ep(
    ledger_id: str,
    plan_id: str,
    req: WriteInstallmentPlanUpdateRequest,
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
        audit_action="web_installment_plan_update",
        mutate=lambda snapshot: (update_installment_plan(snapshot, plan_id, mutate_payload), plan_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_installment_plan_ep(
    ledger_id: str,
    plan_id: str,
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
        audit_action="web_installment_plan_delete",
        mutate=lambda snapshot: (delete_installment_plan(snapshot, plan_id, mutate_payload), plan_id),
    )
