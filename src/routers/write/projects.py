"""專案(Phase 13,docs/PH13_PROJECT_SD.md)write endpoints。

POST / PATCH / DELETE for /ledgers/{ledger_id}/projects。跟 budgets.py 同款
boilerplate。DELETE 比照 §2.9.5.4 對信用卡回饋規則的既有先例:專案底下已經
有交易掛著時(`read_tx_projection.project_sync_id` 反查非空),物理刪除會讓
這些歷史交易的 `project_id` 反查變成懸空引用——改成軟刪除(`enabled=false`),
清單保留這條專案但不再出現在挑選器/總覽;沒有任何交易掛著時允許直接物理
刪除。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


def _project_has_transactions(db: Session, *, ledger_id: str, project_id: str) -> bool:
    return db.scalar(
        select(ReadTxProjection.sync_id).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.project_sync_id == project_id,
        ).limit(1)
    ) is not None


@router.post(
    "/ledgers/{ledger_id}/projects",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_project_api(
    ledger_id: str,
    req: WriteProjectCreateRequest,
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
        audit_action="web_project_create",
        mutate=lambda snapshot: create_project(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/projects/{project_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_project_api(
    ledger_id: str,
    project_id: str,
    req: WriteProjectUpdateRequest,
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
        audit_action="web_project_update",
        mutate=lambda snapshot: (update_project(snapshot, project_id, mutate_payload), project_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/projects/{project_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_project_api(
    ledger_id: str,
    project_id: str,
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
    if _project_has_transactions(db, ledger_id=ledger.id, project_id=project_id):
        disable_payload = {**mutate_payload, "enabled": False}
        return await _commit_write(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_project_soft_delete",
            mutate=lambda snapshot: (update_project(snapshot, project_id, disable_payload), project_id),
        )
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_project_delete",
        mutate=lambda snapshot: (delete_project(snapshot, project_id, mutate_payload), project_id),
    )
