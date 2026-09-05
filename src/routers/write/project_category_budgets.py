"""專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
design.md §5/§7.2)write endpoints。

POST/PATCH/DELETE for /ledgers/{ledger_id}/projects/{project_id}/category-budgets。
跟 `projects.py` 同款 boilerplate。這個實體不被交易反查引用(跟 `project` 本身
不同),DELETE 直接物理刪除,不需要軟刪除分支。

`project_id`/`category_id` 的存在性驗證這個 repo 沒有現成 helper 可用(其它
ledger-scoped 子實體如 `debt`/`installment_plan` 的 `category_sync_id` 都刻意
不做存在性驗證) —— `_assert_project_ref_exists`/`_assert_top_level_category_exists`
是本次新寫的兩個校驗,只給這個 endpoint 用,不放進 `_shared.py`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol

router = APIRouter()


def _assert_project_ref_exists(db: Session, *, ledger_id: str, project_id: str) -> None:
    exists = db.scalar(
        select(ReadProjectProjection.sync_id).where(
            ReadProjectProjection.ledger_id == ledger_id,
            ReadProjectProjection.sync_id == project_id,
        ).limit(1)
    ) is not None
    if not exists:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="project not found")


def _assert_top_level_category_exists(db: Session, *, user_id: str, category_id: str) -> None:
    row = db.execute(
        select(UserCategoryProjection.level).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == category_id,
        ).limit(1)
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="category not found")
    if row[0] != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="category must be a top-level category",
        )


@router.post(
    "/ledgers/{ledger_id}/projects/{project_id}/category-budgets",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_project_category_budget_api(
    ledger_id: str,
    project_id: str,
    req: WriteProjectCategoryBudgetCreateRequest,
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
    _assert_project_ref_exists(db, ledger_id=ledger.id, project_id=project_id)
    _assert_top_level_category_exists(db, user_id=current_user.id, category_id=req.category_id)
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
        audit_action="web_project_category_budget_create",
        mutate=lambda snapshot: create_project_category_budget(snapshot, project_id, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_project_category_budget_api(
    ledger_id: str,
    project_id: str,
    budget_id: str,
    req: WriteProjectCategoryBudgetUpdateRequest,
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
        audit_action="web_project_category_budget_update",
        mutate=lambda snapshot: (
            update_project_category_budget(snapshot, budget_id, mutate_payload), budget_id,
        ),
    )


@router.delete(
    "/ledgers/{ledger_id}/projects/{project_id}/category-budgets/{budget_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_project_category_budget_api(
    ledger_id: str,
    project_id: str,
    budget_id: str,
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
        audit_action="web_project_category_budget_delete",
        mutate=lambda snapshot: (
            delete_project_category_budget(snapshot, budget_id, mutate_payload), budget_id,
        ),
    )
