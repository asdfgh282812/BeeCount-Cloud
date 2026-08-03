"""信用卡紅利回饋規則 write endpoints(§2.9.5 Phase 4.5 MOZE_FEATURE_GAP_SD.md)。

POST / PATCH / DELETE for
`/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules`。規則綁定
的是 user-global 的 `UserAccountProjection`(不是 ledger-scoped 實體,見
`ReadCardRewardRuleProjection` docstring)——endpoint 仍掛在
`/ledgers/{id}/...` 底下,只是為了複用既有的 `_prepare_write`/
`_commit_write` 引擎(auth / idempotency / LWW 都靠它),owner-only(比照
recurring_rules / debts / tx_templates)。`account_id` 必須是一張真實的
`credit_card` 帳戶 —— 回饋規則綁定實體卡片,`account_group` 純管理容器
自己不會被刷卡,不能掛規則。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...models import CardRewardPayout
from ...snapshot_mutator import create_transaction as _mutate_create_tx

router = APIRouter()


def _assert_account_is_credit_card(db: Session, *, user_id: str, account_id: str) -> None:
    account_type = db.scalar(
        select(UserAccountProjection.account_type).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account_type is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="account not found")
    if account_type != "credit_card":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="account_id must reference a credit_card account",
        )


def _assert_rule_belongs_to_account(db: Session, *, user_id: str, rule_id: str, account_id: str) -> None:
    owner_account_id = db.scalar(
        select(ReadCardRewardRuleProjection.account_sync_id).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.sync_id == rule_id,
        )
    )
    if owner_account_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card reward rule not found")
    if owner_account_id != account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="rule_id does not belong to this account",
        )


@router.post(
    "/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_card_reward_rule_api(
    ledger_id: str,
    account_id: str,
    req: WriteCardRewardRuleCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    payload["account_id"] = account_id
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
    _assert_account_is_credit_card(db, user_id=current_user.id, account_id=account_id)
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
        audit_action="web_card_reward_rule_create",
        mutate=lambda snapshot: create_card_reward_rule(snapshot, mutate_payload),
    )


@router.patch(
    "/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules/{rule_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_card_reward_rule_api(
    ledger_id: str,
    account_id: str,
    rule_id: str,
    req: WriteCardRewardRuleUpdateRequest,
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
    _assert_rule_belongs_to_account(db, user_id=current_user.id, rule_id=rule_id, account_id=account_id)
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
        audit_action="web_card_reward_rule_update",
        mutate=lambda snapshot: (update_card_reward_rule(snapshot, rule_id, mutate_payload), rule_id),
    )


@router.delete(
    "/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules/{rule_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_card_reward_rule_api(
    ledger_id: str,
    account_id: str,
    rule_id: str,
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
    _assert_rule_belongs_to_account(db, user_id=current_user.id, rule_id=rule_id, account_id=account_id)
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
        audit_action="web_card_reward_rule_delete",
        mutate=lambda snapshot: (delete_card_reward_rule(snapshot, rule_id, mutate_payload), rule_id),
    )


@router.post(
    "/ledgers/{ledger_id}/accounts/{account_id}/card-reward-rules/{rule_id}/manual-payout",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def manual_card_reward_payout_ep(
    ledger_id: str,
    account_id: str,
    rule_id: str,
    req: WriteCardRewardManualPayoutRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """手動入帳(§2.9.5.4 補強,2026-08-03 使用者反饋):`settlement_type ==
    "manual"` 的規則刻意不進 `services.card_reward_payout` 的自動掃描範圍
    (見該模組 docstring),使用者要自己決定何時、把多少金額、存進哪個帳戶
    ——這個端點就是那個「按一下就記一筆」的手動動作,本質是產生一筆
    `tx_type="income"` 的交易,語意跟 `card_payment_ep`(信用卡繳款)同一個
    「語意化端點包一筆交易」模式。`amount`/`reward_account_id` 每次呼叫
    臨時指定,不要求跟規則上的 `reward_account_id` 一致(manual 規則的這個
    欄位本來就允許是 null,建立時 `needsRewardAccount` 前端邏輯也不收集)。
    跟自動入帳引擎共用同一份 `CardRewardPayout` 台帳(方便使用者在交易明細
    彈窗的「已入帳」流水看到手動入帳記錄),`dedup_key` 用產生出來的交易
    `sync_id`(手動觸發沒有「同一期只能結算一次」的天然邊界,不做防重複
    校驗——使用者自己控制要不要重複按)。走一般交易寫權限
    (`_TRANSACTION_WRITE_ROLES`),不是 owner-only,跟繳款/還款交易同一個
    權限模型。"""
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

    _assert_account_is_credit_card(db, user_id=current_user.id, account_id=account_id)
    _assert_rule_belongs_to_account(db, user_id=current_user.id, rule_id=rule_id, account_id=account_id)
    rule = db.scalar(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == current_user.id,
            ReadCardRewardRuleProjection.sync_id == rule_id,
        )
    )
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card reward rule not found")

    _assert_account_not_group(
        db, user_id=current_user.id, account_id=req.reward_account_id, field_name="reward_account_id",
    )
    to_name = _resolve_account_display(db, user_id=current_user.id, account_id=req.reward_account_id)
    if to_name is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reward_account_id not found")

    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    note = req.note or f"信用卡回饋入帳（手動）：{rule.label}"

    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    actor_fields = {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        tx_payload = {
            "tx_type": "income",
            "amount": req.amount,
            "happened_at": now,
            "note": note,
            "account_id": req.reward_account_id,
            "account_name": to_name,
            **actor_fields,
        }
        next_snapshot, tx_id = _mutate_create_tx(snapshot, tx_payload)
        db.add(CardRewardPayout(
            user_id=current_user.id,
            rule_sync_id=rule_id,
            dedup_key=f"manual:{tx_id}",
            amount=req.amount,
            payout_tx_sync_id=tx_id,
            created_at=now,
        ))
        return next_snapshot, tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_card_reward_manual_payout",
        mutate=_mutate,
    )
