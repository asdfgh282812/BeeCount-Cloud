"""交易範本 write endpoints(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/tx-templates,外加一个
`POST .../apply` 端点直接把範本内容套成一笔新交易(复用建交易的 fast path,
語義跟一般 `POST .../transactions` 建立单笔交易完全一致,只是欄位來源是
範本而不是 client 逐欄位填)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/tx-templates",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_tx_template_api(
    ledger_id: str,
    req: WriteTxTemplateCreateRequest,
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
        audit_action="web_tx_template_create",
        mutate=lambda snapshot: create_tx_template(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/tx-templates/{template_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_tx_template_api(
    ledger_id: str,
    template_id: str,
    req: WriteTxTemplateUpdateRequest,
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
        audit_action="web_tx_template_update",
        mutate=lambda snapshot: (
            update_tx_template(snapshot, template_id, mutate_payload), template_id,
        ),
    )


@router.delete(
    "/ledgers/{ledger_id}/tx-templates/{template_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_tx_template_api(
    ledger_id: str,
    template_id: str,
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
        audit_action="web_tx_template_delete",
        mutate=lambda snapshot: (
            delete_tx_template(snapshot, template_id, mutate_payload), template_id,
        ),
    )


@router.post(
    "/ledgers/{ledger_id}/tx-templates/{template_id}/apply",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def apply_tx_template_api(
    ledger_id: str,
    template_id: str,
    req: WriteTxTemplateApplyRequest,
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
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay

    template = db.execute(
        select(
            ReadTxTemplateProjection.tx_type,
            ReadTxTemplateProjection.amount,
            ReadTxTemplateProjection.note,
            ReadTxTemplateProjection.category_sync_id,
            ReadTxTemplateProjection.account_sync_id,
            ReadTxTemplateProjection.from_account_sync_id,
            ReadTxTemplateProjection.to_account_sync_id,
        ).where(
            ReadTxTemplateProjection.ledger_id == ledger.id,
            ReadTxTemplateProjection.sync_id == template_id,
        )
    ).first()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    (tx_type, tpl_amount, tpl_note, category_id, account_id,
     from_account_id, to_account_id) = template
    for field, value in (
        ("account_id", account_id), ("from_account_id", from_account_id), ("to_account_id", to_account_id),
    ):
        _assert_account_not_group(db, user_id=current_user.id, account_id=value, field_name=field)

    category_name, category_kind = _resolve_category_display(
        db, user_id=current_user.id, category_id=category_id,
    )
    account_name = _resolve_account_display(db, user_id=current_user.id, account_id=account_id)
    from_account_name = _resolve_account_display(
        db, user_id=current_user.id, account_id=from_account_id,
    )
    to_account_name = _resolve_account_display(
        db, user_id=current_user.id, account_id=to_account_id,
    )

    tx_payload = {
        "tx_type": tx_type,
        "amount": req.amount if req.amount is not None else tpl_amount,
        "happened_at": req.happened_at,
        "note": req.note if req.note is not None else tpl_note,
        "category_id": category_id,
        "category_name": category_name,
        "category_kind": category_kind,
        "account_id": account_id,
        "account_name": account_name,
        "from_account_id": from_account_id,
        "from_account_name": from_account_name,
        "to_account_id": to_account_id,
        "to_account_name": to_account_name,
    }
    mutate_payload = _payload_with_actor(tx_payload, current_user, ledger=ledger)
    return await _commit_create_tx_fast(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_template_apply",
        mutate_payload=mutate_payload,
    )
