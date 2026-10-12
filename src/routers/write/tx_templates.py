"""交易範本 write endpoints(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)。

POST / PATCH / DELETE for /ledgers/{ledger_id}/tx-templates,外加一个
`POST .../apply` 端点直接把範本内容套成一笔新交易(复用建交易的 fast path,
語義跟一般 `POST .../transactions` 建立单笔交易完全一致,只是欄位來源是
範本而不是 client 逐欄位填)。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

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

    template = db.scalar(
        select(ReadTxTemplateProjection).where(
            ReadTxTemplateProjection.ledger_id == ledger.id,
            ReadTxTemplateProjection.sync_id == template_id,
        )
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    tx_type = template.tx_type or "expense"
    account_id = template.account_sync_id
    from_account_id = template.from_account_sync_id
    to_account_id = template.to_account_sync_id
    category_id = template.category_sync_id
    # amount=0 的範本是「套用時再填金額」:request 沒給金額就沒辦法建交易。
    amount = req.amount if req.amount is not None else float(template.amount or 0)
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="this template has no amount; amount is required when applying",
        )
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

    def _id_list(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [str(v) for v in parsed] if isinstance(parsed, list) else []

    # 範本引用的專案被刪掉時不讓整個範本「套不了」:直接不掛專案。
    project_id = template.project_sync_id
    if project_id and db.scalar(
        select(ReadProjectProjection.sync_id).where(
            ReadProjectProjection.ledger_id == ledger.id,
            ReadProjectProjection.sync_id == project_id,
        )
    ) is None:
        project_id = None
    # 同理:回饋規則被刪、或已不屬於範本帳戶那張卡 → 不勾,不擋套用。
    reward_rule_ids = _id_list(template.reward_rule_sync_ids_json)
    if reward_rule_ids:
        try:
            _assert_reward_rules_valid(
                db, user_id=ledger.user_id, account_id=account_id,
                reward_rule_ids=reward_rule_ids,
            )
        except HTTPException:
            reward_rule_ids = []

    tx_payload: dict[str, Any] = {
        "tx_type": tx_type,
        "amount": amount,
        "happened_at": req.happened_at,
        "note": req.note if req.note is not None else template.note,
        "merchant": template.merchant,
        "category_id": category_id,
        "category_name": category_name,
        "category_kind": category_kind,
        "account_id": account_id,
        "account_name": account_name,
        "from_account_id": from_account_id,
        "from_account_name": from_account_name,
        "to_account_id": to_account_id,
        "to_account_name": to_account_name,
        "tag_ids": _id_list(template.tag_sync_ids_json),
        "project_id": project_id if tx_type in ("expense", "income") else None,
        "reward_rule_ids": reward_rule_ids if tx_type == "expense" else [],
        "exclude_from_stats": bool(template.exclude_from_stats),
        "exclude_from_budget": bool(template.exclude_from_budget),
        "currency_code": template.currency_code,
        # 外幣範本:本位幣金額由用戶端按匯率算好帶入,server 不查匯率。
        "native_amount": req.native_amount if template.currency_code else None,
        "to_amount": template.to_amount if tx_type == "transfer" else None,
        "fee_label": template.fee_label,
        "discount_label": template.discount_label,
    }
    if template.fee_amount is not None or template.discount_amount is not None:
        # expense/income:amount 是使用者輸入的原始金額 → 當 base_amount,server 重算
        # 實際入帳總額;transfer 的 amount 維持「本體」,不重算(見
        # _normalize_fee_discount_amount docstring)。
        if tx_type in ("expense", "income"):
            tx_payload["base_amount"] = amount
        tx_payload["fee_amount"] = template.fee_amount
        tx_payload["discount_amount"] = template.discount_amount
    _assert_transfer_to_amount_valid(
        db, user_id=current_user.id, ledger_id=ledger.id, tx_id=None, payload=tx_payload,
    )
    _normalize_fee_discount_amount(db=db, ledger_id=ledger.id, tx_id=None, payload=tx_payload)
    mutate_payload = _payload_with_actor(tx_payload, current_user, ledger=ledger)

    def _bump_usage(txn_db: Session, now: datetime, _tx_id: str) -> None:
        """「記憶」:套用成功就在同一個 DB transaction 內累加使用次數、更新最近使用
        時間,並寫一筆 tx_template SyncChange 讓 App/Web 排序一致。"""
        row = txn_db.scalar(
            select(ReadTxTemplateProjection).where(
                ReadTxTemplateProjection.ledger_id == ledger.id,
                ReadTxTemplateProjection.sync_id == template_id,
            )
        )
        if row is None:
            return
        row.usage_count = int(row.usage_count or 0) + 1
        row.last_used_at = now
        change = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="tx_template",
            entity_sync_id=template_id,
            action="upsert",
            payload_json=projection.tx_template_row_to_payload(row),
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
        txn_db.add(change)
        txn_db.flush()
        row.source_change_id = change.change_id

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
        extra_in_txn=_bump_usage,
    )
