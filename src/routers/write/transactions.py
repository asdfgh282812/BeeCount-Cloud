"""Transactions write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/transactions(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...snapshot_mutator import create_transaction as _mutate_create_tx
from ...services import recurring_schedule

router = APIRouter()


@router.post(
    "/ledgers/{ledger_id}/transactions",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_tx(
    ledger_id: str,
    req: WriteTransactionCreateRequest,
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
    # 主帳戶(§2.9 Phase 4):account_group 是純管理容器,不能被一般交易挂上。
    for field in ("account_id", "from_account_id", "to_account_id"):
        _assert_account_not_group(db, user_id=current_user.id, account_id=payload.get(field), field_name=field)
    # 跨幣別轉帳(2026-08):轉出/轉入帳戶幣別不同時必須帶 to_amount。
    _assert_transfer_to_amount_valid(
        db, user_id=current_user.id, ledger_id=ledger.id, tx_id=None, payload=payload,
    )
    # 手續費/折扣(2026-08 使用者需求):在 mutate_payload 复制前重算 amount,
    # 之后所有分支(fast path / recurring inline)读到的都是校正后的总额。
    _normalize_fee_discount_amount(db=db, ledger_id=ledger.id, tx_id=None, payload=payload)
    # 旧架构这里要跑 _resolve_tx_dictionary_payload 去 UserAccount/Category/Tag
    # 三张投影表里查 id / 建 row。新架构所有实体都是 snapshot 里的 syncId,
    # web UI 下拉选项也从 snapshot 读,account_id / category_id / tag_ids 直接
    # 是 syncId,不再需要任何投影表。payload 直接传给 snapshot_mutator。
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    if req.splits and req.recurring is not None:
        # 拆帳(§2.4)跟週期性收支(§2.12.2)組合尚未支援:兩者都各自有整批
        # 生成/重算的邏輯,疊在一起會讓「這期以後」跟「splits 校驗」的語意
        # 打架。先擋掉,之後要支援得先確定 recurring 產生的每期 occurrence
        # 各自怎麼拆。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="creating a split transaction together with a recurring rule is not supported yet",
        )

    if req.recurring is None:
        # issue #31 A1b:单笔新建走 fast path,不再全量 build snapshot
        # (O(账本交易数)→O(1))。create 没有 diff 需求(新行即唯一变更),
        # 语义与 _commit_write 一致。
        return await _commit_create_tx_fast(
            request=request,
            db=db,
            current_user=current_user,
            ledger=ledger,
            base_change_id=req.base_change_id,
            request_payload=payload,
            idempotency_key=idempotency_key,
            device_id=device_id,
            audit_action="web_tx_create",
            mutate_payload=mutate_payload,
        )

    # Phase 1.5(§2.12.2):建交易当下顺便把它设成週期性收支的起点 —— 这笔
    # 交易本身就是 occurrence #1,不重新合成一份,跟 recurring_rules.py 的
    # POST 结构一致但省了"第一笔"那次生成。走多实体单一 commit(不能用上面
    # 的单 tx fast path,因为要同时写规则 + 可能的更多 occurrence)。
    recurring_req = req.recurring

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        rule_payload = dict(mutate_payload)
        rule_payload.update({
            "tx_type": req.tx_type,
            # 手續費/折扣(2026-08 使用者回饋):用 mutate_payload 裡經過
            # _normalize_fee_discount_amount(56 行)重算後的權威 amount,不能
            # 用 req.amount(前端送來的原始值,沒扣手續費/折扣)——否則規則本身
            # 跟往後所有 occurrence 的金額都會是錯的。
            "amount": mutate_payload.get("amount"),
            "note": req.note,
            "category_id": req.category_id,
            "account_id": req.account_id,
            "from_account_id": req.from_account_id,
            "to_account_id": req.to_account_id,
            "frequency": recurring_req.frequency,
            "interval": recurring_req.interval,
            "next_run_at": req.happened_at,
            "end_at": recurring_req.end_at,
            "advanced_rule_json": recurring_req.advanced_rule_json,
        })

        # 自動扣繳(tx_type=="transfer",2026-08-02 补):这笔交易本身(现在
        # 正在记的这笔)照常立刻创建,是使用者当下的真实操作,不需要检查
        # 余额。但未来各期不再提前批次生成——推 rule_payload 只推进到这一笔
        # 为止(generated_until_at=req.happened_at),之后每一期都交给
        # recurring_materializer.materialize_due_transfer_rules 到期当下才
        # 生成 + 检查来源帐户余额(见该函式 docstring;架构原因跟
        # write/recurring_rules.py::create_recurring_rule_ep 同一段说明)。
        if req.tx_type == "transfer":
            rule_payload["generated_until_at"] = req.happened_at
            next_snapshot, rule_id = create_recurring_rule(snapshot, rule_payload)
            first_tx_payload = dict(mutate_payload)
            first_tx_payload["recurring_rule_id"] = rule_id
            next_snapshot, first_tx_id = _mutate_create_tx(next_snapshot, first_tx_payload)
            return next_snapshot, first_tx_id

        occurrences, generated_until_at, fully_generated = recurring_schedule.plan_initial_generation(
            start=req.happened_at,
            end=recurring_req.end_at,
            frequency=recurring_req.frequency,
            interval=recurring_req.interval,
            advanced_rule=recurring_req.advanced_rule_json,
        )
        rule_payload["generated_until_at"] = generated_until_at
        if fully_generated:
            rule_payload["enabled"] = False
        next_snapshot, rule_id = create_recurring_rule(snapshot, rule_payload)

        actor_fields = {
            "__actor_user_id": mutate_payload.get("__actor_user_id"),
            "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
            "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
        }
        first_tx_payload = dict(mutate_payload)
        first_tx_payload["recurring_rule_id"] = rule_id
        next_snapshot, first_tx_id = _mutate_create_tx(next_snapshot, first_tx_payload)

        for occ in occurrences[1:]:
            tx_payload = {
                "tx_type": req.tx_type,
                # 手續費/折扣(2026-08 使用者回饋):同上,用 mutate_payload
                # 重算後的權威 amount,不能用 req.amount。
                "amount": mutate_payload.get("amount"),
                "happened_at": occ,
                "note": req.note,
                "merchant": req.merchant,
                "category_id": req.category_id,
                "category_name": req.category_name,
                "category_kind": req.category_kind,
                "account_id": req.account_id,
                "account_name": req.account_name,
                "from_account_id": req.from_account_id,
                "from_account_name": req.from_account_name,
                "to_account_id": req.to_account_id,
                "to_account_name": req.to_account_name,
                "recurring_rule_id": rule_id,
                # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,
                # 每一期(含第一期)都要繼承 —— 第一筆走 first_tx_payload =
                # dict(mutate_payload) 已經天然帶有,這裡補上第二筆起。
                "base_amount": mutate_payload.get("base_amount"),
                "fee_amount": mutate_payload.get("fee_amount"),
                "fee_label": mutate_payload.get("fee_label"),
                "discount_amount": mutate_payload.get("discount_amount"),
                "discount_label": mutate_payload.get("discount_label"),
                "reward_rule_ids": mutate_payload.get("reward_rule_ids"),
                **actor_fields,
            }
            next_snapshot, _tx_id = _mutate_create_tx(next_snapshot, tx_payload)
        return next_snapshot, first_tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_create_with_recurring",
        mutate=_mutate,
    )


@router.patch(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_tx(
    ledger_id: str,
    tx_id: str,
    req: WriteTransactionUpdateRequest,
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
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    for field in ("account_id", "from_account_id", "to_account_id"):
        _assert_account_not_group(db, user_id=current_user.id, account_id=payload.get(field), field_name=field)
    # 跨幣別轉帳(2026-08):轉出/轉入帳戶幣別不同時必須帶 to_amount。
    _assert_transfer_to_amount_valid(
        db, user_id=current_user.id, ledger_id=ledger.id, tx_id=tx_id, payload=payload,
    )
    # 手續費/折扣(2026-08 使用者需求):PATCH 缺鍵的分量 fallback 現有 DB 值。
    _normalize_fee_discount_amount(db=db, ledger_id=ledger.id, tx_id=tx_id, payload=payload)
    # 跟 create_tx 同样改动:account/category/tag 的 id 直接走 snapshot syncId,
    # 不再经 UserAccount 投影表。
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_update",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="upsert",
    )


@router.delete(
    "/ledgers/{ledger_id}/transactions/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_tx(
    ledger_id: str,
    tx_id: str,
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
        required_roles=_TRANSACTION_WRITE_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    _assert_can_modify_entity(
        db=db,
        ledger=ledger,
        current_user=current_user,
        entity_sync_id=tx_id,
    )
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_tx_delete",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="delete",
    )


