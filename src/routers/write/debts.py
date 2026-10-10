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
from ...schemas import (
    WriteDebtRepayRequest,
    WriteDebtStopRequest,
    WriteDebtWriteOffRequest,
)
from ...snapshot_mutator import create_transaction as _mutate_create_tx
from ...services.debt_schedule import installment_date_at, split_installment_amounts
from ...services.debt_status import debt_repayment_totals

router = APIRouter()


def _actor_fields(mutate_payload: dict) -> dict:
    return {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }


def _iso_future(raw: object, now: datetime) -> bool:
    if not isinstance(raw, str) or not raw:
        return False
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > now


def _drop_future_schedule(snapshot: dict, debt_id: str, now: datetime) -> None:
    """刪掉這筆欠款分期排程裡還沒到日期的收還款(停止追蹤、轉為支出),同 App
    `LocalRepository._deleteFutureRepayments`。"""
    items = snapshot.get("items") or []
    items[:] = [
        it for it in items
        if not (
            isinstance(it, dict)
            and it.get("debtId") == debt_id
            and _iso_future(it.get("happenedAt"), now)
        )
    ]
    snapshot["count"] = len(items)


def _debt_tx_payload(
    *,
    tx_type: str,
    amount: float,
    happened_at: datetime,
    account_id: str | None,
    account_name: str | None,
    merchant: str,
    note: str | None,
    actor: dict,
    category: tuple[str | None, str | None, str | None] = (None, None, None),
    debt_id: str | None = None,
    counted: bool = False,
    reward_rule_ids: list[str] | None = None,
) -> dict:
    """借還款相關交易的共用 payload。`counted=False`(預設)= 不計收支/預算,
    只有轉為支出/收入的那一筆是 `counted=True`。"""
    category_id, category_name, category_kind = category
    payload: dict = {
        "tx_type": tx_type,
        "amount": amount,
        "happened_at": happened_at,
        "account_id": account_id,
        "account_name": account_name,
        "merchant": merchant,
        "note": note,
        "category_id": category_id,
        "category_name": category_name,
        "category_kind": category_kind,
        "exclude_from_stats": not counted,
        "exclude_from_budget": not counted,
        **actor,
    }
    if debt_id:
        payload["debt_id"] = debt_id
    if reward_rule_ids:
        payload["reward_rule_ids"] = reward_rule_ids
    return payload


def _assert_debt_has_no_repayments(db: Session, *, ledger_id: str, debt_id: str) -> None:
    # 只看已發生的收還款;分期排程的未來收還款由 delete_debt mutator 一起刪。
    if _debt_has_repayments(db, ledger_id=ledger_id, debt_id=debt_id):
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
    installment = req.installment
    compound = (
        req.origin_tx_id is None
        and (req.kind == "existing" or req.account_id is not None or installment is not None)
    )
    if not compound:
        # 舊行為:只登記欠款(mobile 帶 origin_tx_id,或舊版 Web 不帶帳戶)。
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

    # ---- App v68 完整流程(同 App `LocalRepository.createDebtEntry`)----
    is_new = req.kind == "new"
    card = bool(installment and installment.card)
    if card and (req.direction != "receivable" or not is_new):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="card installment is only for new receivables",
        )
    if is_new and not req.account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="a new debt needs an account",
        )
    account_id = req.account_id if is_new else None
    schedule_account_id = (
        (installment.schedule_account_id or account_id) if installment and not card else None
    )
    for field, acc in (("account_id", account_id), ("schedule_account_id", schedule_account_id)):
        _assert_account_not_group(db, user_id=current_user.id, account_id=acc, field_name=field)
    reward_rule_ids = [str(v) for v in (req.reward_rule_ids or [])] if is_new else []
    if reward_rule_ids and req.direction != "receivable":
        reward_rule_ids = []
    _assert_reward_rules_valid(
        db, user_id=current_user.id, account_id=account_id, reward_rule_ids=reward_rule_ids,
    )
    category_name, category_kind = _resolve_category_display(
        db, user_id=current_user.id, category_id=req.category_id,
    )
    category = (req.category_id, category_name, category_kind) if req.category_id else (None, None, None)
    account_name = _resolve_account_display(db, user_id=current_user.id, account_id=account_id)
    schedule_account_name = _resolve_account_display(
        db, user_id=current_user.id, account_id=schedule_account_id,
    )
    started_at = req.started_at or _utcnow()
    origin_type = "income" if req.direction == "payable" else "expense"
    repay_type = "expense" if req.direction == "payable" else "income"
    counterparty = req.counterparty_name.strip()

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        actor = _actor_fields(mutate_payload)
        debt_base = {
            "direction": req.direction,
            "counterparty_name": counterparty,
            "due_at": req.due_at,
            "note": req.note,
            "category_id": req.category_id,
            "excluded_from_total": req.excluded_from_total,
            **actor,
        }
        if card and installment is not None:
            # 代刷分期:每期一筆應收欠款,起點是刷在卡上的那一期金額。
            group_id = uuid4().hex
            first_debt_id = ""
            amounts = split_installment_amounts(req.principal_amount, installment.count)
            for i, amount in enumerate(amounts):
                at = installment_date_at(installment.first_at, i)
                snapshot, tx_id = _mutate_create_tx(snapshot, _debt_tx_payload(
                    tx_type="expense", amount=amount, happened_at=at,
                    account_id=account_id, account_name=account_name,
                    merchant=counterparty, note=req.note, actor=actor,
                    category=category, reward_rule_ids=reward_rule_ids,
                ))
                snapshot, debt_id = create_debt(snapshot, {
                    **debt_base,
                    "principal_amount": amount,
                    "origin_tx_id": tx_id,
                    "kind": "new",
                    "started_at": at,
                    "installment_count": installment.count,
                    "installment_no": i + 1,
                    "installment_group_id": group_id,
                })
                first_debt_id = first_debt_id or debt_id
            return snapshot, first_debt_id

        origin_tx_id = None
        if is_new:
            snapshot, origin_tx_id = _mutate_create_tx(snapshot, _debt_tx_payload(
                tx_type=origin_type, amount=req.principal_amount, happened_at=started_at,
                account_id=account_id, account_name=account_name,
                merchant=counterparty, note=req.note, actor=actor,
                category=category, reward_rule_ids=reward_rule_ids,
            ))
        snapshot, debt_id = create_debt(snapshot, {
            **debt_base,
            "principal_amount": req.principal_amount,
            "origin_tx_id": origin_tx_id,
            "kind": req.kind,
            "started_at": started_at,
            "installment_count": installment.count if installment else None,
        })
        if installment is not None:
            # 單筆欠款分期:每月一筆未來日期的收還款(到期才動帳戶)。
            amounts = split_installment_amounts(req.principal_amount, installment.count)
            for i, amount in enumerate(amounts):
                snapshot, _ = _mutate_create_tx(snapshot, _debt_tx_payload(
                    tx_type=repay_type, amount=amount,
                    happened_at=installment_date_at(installment.first_at, i),
                    account_id=schedule_account_id, account_name=schedule_account_name,
                    merchant=counterparty, note=req.note, actor=actor, debt_id=debt_id,
                ))
        return snapshot, debt_id

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
        mutate=_mutate,
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
    _assert_debt_not_from_split(db, ledger_id=ledger.id, debt_id=debt_id)
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


# ============================================================================
# App v68 MOZE 化:多筆收還款、停止追蹤、轉為支出/收入。邏輯對齊 App
# `LocalRepository.repayDebts`/`stopTrackingDebt`/`writeOffDebt`,一次寫入
# 內完成(`_commit_write` snapshot 流程,每個變動的實體各一條 SyncChange)。
# ============================================================================


def _load_debts(db: Session, *, ledger_id: str, debt_ids: list[str]) -> dict[str, ReadDebtProjection]:
    rows = db.scalars(
        select(ReadDebtProjection).where(
            ReadDebtProjection.ledger_id == ledger_id,
            ReadDebtProjection.sync_id.in_(debt_ids),
        )
    ).all()
    found = {row.sync_id: row for row in rows}
    missing = [d for d in debt_ids if d not in found]
    if missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="debt not found")
    return found


def _close_debt_in_snapshot(snapshot: dict, debt_id: str, now: datetime, actor: dict) -> dict:
    _drop_future_schedule(snapshot, debt_id, now)
    return update_debt(snapshot, debt_id, {"closed_at": now, **actor})


@router.post(
    "/ledgers/{ledger_id}/debts/repay",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def repay_debts_api(
    ledger_id: str,
    req: WriteDebtRepayRequest,
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
    alloc_ids = [a.debt_id for a in req.allocations]
    if len(set(alloc_ids)) != len(alloc_ids):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="duplicate debt in allocations")
    debts = _load_debts(db, ledger_id=ledger.id, debt_ids=list({*alloc_ids, *req.settle_debt_ids}))
    directions = {debts[d].direction for d in alloc_ids}
    if len(directions) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="receivables and payables must be repaid separately",
        )
    totals = debt_repayment_totals(db, alloc_ids, ledger_ids=[ledger.id])
    for a in req.allocations:
        debt = debts[a.debt_id]
        repaid = totals[a.debt_id].repaid if a.debt_id in totals else 0.0
        remaining = max(float(debt.principal_amount or 0) - repaid, 0.0)
        if a.amount > remaining + 0.01:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="repayment exceeds remaining amount",
            )
    _assert_account_not_group(db, user_id=current_user.id, account_id=req.account_id)
    direction = directions.pop()
    category_id = req.category_id
    if category_id:
        category_name, category_kind = _resolve_category_display(
            db, user_id=current_user.id, category_id=category_id,
        )
    else:
        # 沒選分類時沿用單筆收還款的自動分類(收款/還款),同 _commit_create_tx_fast。
        from ...services import card_rewards as _card_rewards
        category_id = _card_rewards.ensure_debt_category(db, user_id=ledger.user_id, direction=direction)
        category_name = _card_rewards.debt_category_name(direction)
        category_kind = _card_rewards.debt_category_kind(direction)
    account_name = _resolve_account_display(db, user_id=current_user.id, account_id=req.account_id)
    tx_type = "expense" if direction == "payable" else "income"
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        actor = _actor_fields(mutate_payload)
        first_tx_id = ""
        for a in req.allocations:
            debt = debts[a.debt_id]
            snapshot, tx_id = _mutate_create_tx(snapshot, _debt_tx_payload(
                tx_type=tx_type, amount=a.amount, happened_at=req.happened_at,
                account_id=req.account_id, account_name=account_name,
                merchant=debt.counterparty_name or "", note=req.note, actor=actor,
                category=(category_id, category_name, category_kind), debt_id=a.debt_id,
            ))
            first_tx_id = first_tx_id or tx_id
        now = _utcnow()
        for debt_id in req.settle_debt_ids:
            snapshot = _close_debt_in_snapshot(snapshot, debt_id, now, actor)
        return snapshot, first_tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_repay",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/debts/{debt_id}/stop",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def stop_tracking_debt_api(
    ledger_id: str,
    debt_id: str,
    req: WriteDebtStopRequest,
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
    _load_debts(db, ledger_id=ledger.id, debt_ids=[debt_id])
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        actor = _actor_fields(mutate_payload)
        return _close_debt_in_snapshot(snapshot, debt_id, _utcnow(), actor), debt_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_stop",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/debts/{debt_id}/write-off",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def write_off_debt_api(
    ledger_id: str,
    debt_id: str,
    req: WriteDebtWriteOffRequest,
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
    debt = _load_debts(db, ledger_id=ledger.id, debt_ids=[debt_id])[debt_id]
    is_payable = debt.direction == "payable"
    counted_type = "income" if is_payable else "expense"
    category_name, category_kind = _resolve_category_display(
        db, user_id=current_user.id, category_id=req.category_id,
    )
    if category_kind != counted_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"write-off category must be an {counted_type} category",
        )
    _assert_account_not_group(db, user_id=current_user.id, account_id=req.account_id)
    account_name = _resolve_account_display(db, user_id=current_user.id, account_id=req.account_id)
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        actor = _actor_fields(mutate_payload)
        _drop_future_schedule(snapshot, debt_id, _utcnow())
        totals = debt_repayment_totals(db, [debt_id], ledger_ids=[ledger.id]).get(debt_id)
        remaining = max(float(debt.principal_amount or 0) - (totals.repaid if totals else 0.0), 0.0)
        if remaining <= 0.004:
            raise ValueError("debt has nothing left to write off")
        common = dict(
            amount=round(remaining, 2), happened_at=req.happened_at,
            account_id=req.account_id, account_name=account_name,
            merchant=debt.counterparty_name or "", note=req.note, actor=actor,
        )
        # 視為已收/已還(不計收支)……
        snapshot, _ = _mutate_create_tx(snapshot, _debt_tx_payload(
            tx_type="expense" if is_payable else "income", debt_id=debt_id, **common,
        ))
        # ……再記一筆同額、計入收支的呆帳支出(應付則是收入),帳戶餘額不變。
        snapshot, tx_id = _mutate_create_tx(snapshot, _debt_tx_payload(
            tx_type=counted_type, counted=True,
            category=(req.category_id, category_name, category_kind), **common,
        ))
        return snapshot, tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_debt_write_off",
        mutate=_mutate,
    )
