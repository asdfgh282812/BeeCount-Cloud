"""Accounts write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/accounts(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...snapshot_mutator import create_transaction as _mutate_create_tx
from ...services import credit_card_billing, recurring_materializer
from ...services.deferred_posting import attribution_date_expr

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/accounts",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_acc(
    ledger_id: str,
    req: WriteAccountCreateRequest,
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
        audit_action="web_account_create",
        mutate=lambda snapshot: create_account(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/accounts/{account_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_acc(
    ledger_id: str,
    account_id: str,
    req: WriteAccountUpdateRequest,
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
        audit_action="web_account_update",
        mutate=lambda snapshot: (update_account(snapshot, account_id, mutate_payload), account_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/accounts/{account_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_acc(
    ledger_id: str,
    account_id: str,
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
        audit_action="web_account_delete",
        mutate=lambda snapshot: (delete_account(snapshot, account_id, mutate_payload), account_id),
    )


@router.post(
    "/ledgers/{ledger_id}/accounts/{account_id}/card-payment",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def card_payment_ep(
    ledger_id: str,
    account_id: str,
    req: WriteCardPaymentRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """信用卡繳款(§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md,2026-08-02 改版為群組
    分攤模型,同日第二輪放寬到單卡):`account_id` 必須通過
    `credit_card_billing.is_billing_root` 檢查 —— 要嘛是 `account_type ==
    "account_group"` 的合併帳單容器,要嘛是一張沒有掛靠任何群組的獨立信用卡
    (這時它自己既是「群組」也是唯一「子帳戶」)。`req.amount` 是使用者輸入
    的一次性繳款總額。分攤規則(對齊使用者需求,不是等比例打折):
    1. 算出每個子帳戶自己截至本期結帳日為止的應繳金額(該子帳戶終身消費
       減終身已收款,下限 0)。
    2. 繳款總額 >= 全部子帳戶應繳總和:每個子帳戶各自拿到「完整付清」的
       金額(各自產生一筆 transfer),剩下的餘額(溢繳)另外產生一筆
       transfer 記在群組自己身上,代表結轉到未來各期的信用額度(§2.9
       billing-summary 的終身餘額算法會自動把這筆「打到群組」的錢從未來
       `remaining_due` 裡扣掉)。
    2b. 繳款總額 < 應繳總和:按各子帳戶應繳金額比例分攤(無法讓每張卡都
        繳清時,不製造"群組溢繳"假象),最後一個子帳戶用減法拿餘數,避免
        四捨五入加總對不上輸入金額。
    這筆交易的來源帳戶(`from_account_id`)不能是任何 account_group(群組
    沒有自己的資金,不能拿來當繳費來源),也不能是這個群組自己。走一般
    交易寫權限(`_TRANSACTION_WRITE_ROLES`),不是 owner-only —— 跟 §2.5
    借還款的還款交易同一個權限模型。"""
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

    group = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if not credit_card_billing.is_billing_root(group):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "target account is not billable directly; use an account_group, or a "
                "credit_card with no parent_account_id"
            ),
        )
    if group.billing_day is None or group.payment_due_day is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="account_group has no billing_day/payment_due_day configured",
        )
    if req.from_account_id == account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_account_id must differ from the card account",
        )
    _assert_account_not_group(
        db, user_id=current_user.id, account_id=req.from_account_id, field_name="from_account_id",
    )
    from_account_name = _resolve_account_display(
        db, user_id=current_user.id, account_id=req.from_account_id,
    )
    if from_account_name is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="from_account_id not found")

    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    children = credit_card_billing.resolve_billing_children(db, account=group)

    billing = credit_card_billing.compute_group_billing(
        db, ledger_id=ledger.id, group=group, children=children, now=now,
    )
    remaining_due_by_child = billing["per_child_remaining_due"]

    note = req.note
    if not note:
        cycle_start, cycle_end = billing["cycle_start"], billing["cycle_end"]
        note = f"信用卡繳款(帳單 {cycle_start.isoformat()}~{cycle_end.isoformat()})"

    # 分攤金額:key 是子帳戶 sync_id,群組自己的溢繳結轉用 account_id 當 key。
    # 分攤規則抽到 credit_card_billing.compute_card_payment_allocations,跟
    # 自動扣繳(services.credit_card_autopay)共用同一份,不重複維護。
    allocations = credit_card_billing.compute_card_payment_allocations(
        group_sync_id=account_id, remaining_due_by_child=remaining_due_by_child, amount=req.amount,
    )

    if not allocations:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payment amount must be positive")

    child_by_id = {c.sync_id: c for c in children}
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    actor_fields = {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        next_snapshot = snapshot
        last_tx_id = ""
        for target_id, amount in allocations.items():
            if target_id == account_id:
                to_name = group.name
                tx_note = f"{note}(溢繳結轉)" if not req.note else note
            else:
                to_name = child_by_id[target_id].name
                tx_note = note
            tx_payload = {
                "tx_type": "transfer",
                "amount": amount,
                "happened_at": now,
                "note": tx_note,
                "from_account_id": req.from_account_id,
                "from_account_name": from_account_name,
                "to_account_id": target_id,
                "to_account_name": to_name,
                **actor_fields,
            }
            next_snapshot, last_tx_id = _mutate_create_tx(next_snapshot, tx_payload)
        return next_snapshot, last_tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_card_payment",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/accounts/{account_id}/balance-adjustment",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def balance_adjustment_ep(
    ledger_id: str,
    account_id: str,
    req: WriteBalanceAdjustmentRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """餘額調整(§2.10 Phase 5 MOZE_FEATURE_GAP_SD.md):語意化端點 —— 使用者
    輸入「這個帳戶現在應該是多少錢」,server 用
    `recurring_materializer.compute_account_balance`(跟 `list_workspace_accounts`
    算餘額同一公式,已包含 income/expense/transfer/既有 adjustment 交易)
    算出當下記帳餘額,差額 `target_balance - 當下餘額` 寫成一筆
    `tx_type=adjustment` 的交易(`amount` 可正可負)。差額為 0 時仍允許送出
    (使用者可能只是想留一筆 note 备注「已核對」),不擋。跟信用卡繳款/回饋
    手動入帳同一個「語意化端點包一筆交易」模式,走一般交易寫權限
    (`_TRANSACTION_WRITE_ROLES`,不是 owner-only)——餘額調整本質是記一筆
    交易,不是帐户级设置變更。"""
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

    _assert_account_not_group(
        db, user_id=current_user.id, account_id=account_id, field_name="account_id",
    )
    account_name = _resolve_account_display(db, user_id=current_user.id, account_id=account_id)
    if account_name is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account not found")

    current_balance = recurring_materializer.compute_account_balance(
        db, user_id=current_user.id, account_sync_id=account_id,
    )
    diff = round(req.target_balance - current_balance, 2)

    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    note = req.note or f"餘額調整(核對後由 {current_balance:.2f} 調整為 {req.target_balance:.2f})"

    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    actor_fields = {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        tx_payload = {
            "tx_type": "adjustment",
            "amount": diff,
            "happened_at": now,
            "note": note,
            "account_id": account_id,
            "account_name": account_name,
            **actor_fields,
        }
        return _mutate_create_tx(snapshot, tx_payload)

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_balance_adjustment",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/accounts/{account_id}/statement/clear-confirmations",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def clear_statement_confirmations_ep(
    ledger_id: str,
    account_id: str,
    req: WriteStatementClearConfirmationsRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """對帳模式(§2.10 Phase 5,2026-08-09 改版為 Moze 式逐筆核對清單)選單
    「取消全部選取」(對齊原文「清除所有已選項目,恢復到未對帳狀態」):把
    `account_id`(account_group 或沒掛靠群組的獨立信用卡,同 `card_payment_ep`
    的 `is_billing_root` 檢查)在 `cycle_offset` 這期窗口裡、目前已勾選確認
    的所有交易一次清空 `reconciled_at`。跟其它語意化端點(card_payment_ep/
    balance_adjustment_ep)一样走 `_commit_write` 全量 diff 路徑(不是只改
    單筆的 fast path)——這裡雖然不新建交易,而是批次更新既有多筆交易,同
    一個原因:fast path 只支援單一 tx。走一般交易寫權限(不是 owner-only)
    ——對帳本身是記帳動作,跟「確認/取消確認」單筆交易走的通用
    `PATCH .../transactions/{id}` 同一個權限模型。"""
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

    group = db.scalar(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == current_user.id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    if not credit_card_billing.is_billing_root(group):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "target account is not billable directly; use an account_group, or a "
                "credit_card with no parent_account_id"
            ),
        )
    if group.billing_day is None or group.payment_due_day is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="account_group has no billing_day/payment_due_day configured",
        )

    children = credit_card_billing.resolve_billing_children(db, account=group)
    member_ids = credit_card_billing.billing_member_ids(group, children)
    now = _utcnow()
    billing = credit_card_billing.compute_cycle_period_billing(
        db, ledger_id=ledger.id, group=group, children=children, now=now,
        cycle_offset=req.cycle_offset,
    )
    cycle_start_dt = credit_card_billing.date_to_utc_dt(billing["cycle_start"], end_of_day=True)
    cycle_end_dt = credit_card_billing.date_to_utc_dt(billing["cycle_end"], end_of_day=True)

    tx_ids = list(db.scalars(
        select(ReadTxProjection.sync_id).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.account_sync_id.in_(member_ids),
            ReadTxProjection.tx_type.in_(["expense", "income"]),
            ReadTxProjection.reconciled_at.isnot(None),
            attribution_date_expr() > cycle_start_dt,
            attribution_date_expr() <= cycle_end_dt,
        )
    ).all())

    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    actor_fields = {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }

    def _mutate(snapshot: dict) -> tuple[dict, str | None]:
        next_snapshot = snapshot
        for tx_id in tx_ids:
            next_snapshot = update_transaction(
                next_snapshot, tx_id, {"reconciled_at": None, **actor_fields},
            )
        return next_snapshot, (tx_ids[-1] if tx_ids else None)

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_account_statement_clear_confirmations",
        mutate=_mutate,
    )


