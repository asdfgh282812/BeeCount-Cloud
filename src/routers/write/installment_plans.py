"""Installment plans write endpoints(§2.3 / Phase 1.5 修正版 §2.12.1
MOZE_FEATURE_GAP_SD.md）。

POST 建計畫時依 `services.installment_amortization` 一次算出**全部**期数,
同一个 `_commit_write` 事务内为每期各写一笔 `read_tx_projection`(带
`installment_plan_sync_id` 反查)+ 一笔 `read_installment_period_projection`,
不再依赖排程逐期生成(旧版 `recurring_materializer.materialize_due_installment_plans`
整段已删除,见 services/recurring_materializer.py)。

差异化编辑端点(§2.12.1):
- `PATCH .../periods/{period_no}`:编辑单期,`overridden=true`。
- `POST .../rebalance-from/{period_no}`:调利率(可选换攤還方式),對未
  `overridden` 的未来期重算。
- `POST .../early-repay-principal`:部分还本,重算未来期 + 生成一笔还本交易。
- `POST .../payoff`:提前结清,生成结清交易 + 删除未到期期。
- `POST .../terminate-future`:终止未来分期,删除未到期期,不生成结清交易。
- `POST .../refund-period`:单期退款(§2.6),按 tx_id 定位某一期,建一笔
  income 退款交易 + 把该期状态标成 'refunded',原交易保留不动。跟"整笔
  退款"(前端改叫 `DELETE .../{plan_id}` 整个计划)是互斥的两个选项。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...snapshot_mutator import create_transaction as _mutate_create_tx
from ...services import installment_amortization
from ...services import notifications as notification_service

router = APIRouter()


def _find_plan(snapshot: dict, plan_id: str) -> dict:
    plans = snapshot.get("installmentPlans") or []
    plan = next((p for p in plans if p.get("syncId") == plan_id), None)
    if plan is None:
        raise KeyError("installment plan not found")
    return plan


def _plan_periods(snapshot: dict, plan_id: str) -> list[dict]:
    periods_list = snapshot.get("installmentPeriods") or []
    return sorted(
        (p for p in periods_list if p.get("planId") == plan_id),
        key=lambda p: int(p.get("periodNo") or 0),
    )


def _actor_fields(mutate_payload: dict) -> dict:
    return {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }


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

    # 攤還算法是纯函式,建 endpoint 时就能算好,不用等进 _mutate closure
    # (validation 错误在这里抛也会被 _commit_write 的 except ValueError 接住,
    # 因为整个 endpoint body 都在 async def 里,ValueError 会往外传到
    # FastAPI —— 所以特意把这段挪到 _mutate 内部,统一走 _commit_write 的
    # 错误处理路径,变成 400 而不是 500)。
    def _mutate(snapshot: dict) -> tuple[dict, str]:
        schedule = installment_amortization.compute_periods(
            total_amount=req.total_amount,
            periods=req.periods,
            first_period_at=req.first_period_at,
            repayment_method=req.repayment_method,
            interest_period=req.interest_period,
            interest_rate=req.interest_rate,
            round_amounts=req.round_amounts,
            remainder_position=req.remainder_position,
            grace_period_months=req.grace_period_months,
        )
        next_snapshot, plan_id = create_installment_plan(snapshot, mutate_payload)
        actor_fields = _actor_fields(mutate_payload)
        category_name, category_kind = _resolve_category_display(
            db, user_id=current_user.id, category_id=req.category_id,
        )
        account_name = _resolve_account_display(
            db, user_id=current_user.id, account_id=req.account_id,
        )
        for p in schedule:
            tx_payload = {
                "tx_type": "expense",
                "amount": p.total_amount,
                "happened_at": p.due_at,
                "note": req.note,
                "category_id": req.category_id,
                "category_name": category_name,
                "category_kind": category_kind,
                "account_id": req.account_id,
                "account_name": account_name,
                **actor_fields,
            }
            next_snapshot, tx_id = _mutate_create_tx(next_snapshot, tx_payload)
            items = next_snapshot.get("items") or []
            tx_item = next((it for it in items if it.get("syncId") == tx_id), None)
            if tx_item is not None:
                tx_item["installmentPlanId"] = plan_id
            period_payload = {
                "plan_id": plan_id,
                "period_no": p.period_no,
                "due_at": p.due_at,
                "principal_amount": p.principal_amount,
                "interest_amount": p.interest_amount,
                "total_amount": p.total_amount,
                "status": "generated",
                "tx_id": tx_id,
                **actor_fields,
            }
            next_snapshot, _period_id = create_installment_period(next_snapshot, period_payload)
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


@router.patch(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/periods/{period_no}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_installment_period_ep(
    ledger_id: str,
    plan_id: str,
    period_no: int,
    req: WriteInstallmentPeriodUpdateRequest,
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

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        all_periods = _plan_periods(snapshot, plan_id)
        target = next((p for p in all_periods if int(p.get("periodNo") or 0) == period_no), None)
        if target is None:
            raise KeyError("installment period not found")
        period_id = target["syncId"]
        actor_fields = _actor_fields(mutate_payload)

        period_payload: dict = dict(actor_fields)
        period_payload["status"] = "overridden"
        tx_updates: dict = dict(actor_fields)
        has_tx_update = False
        if "amount" in payload and payload.get("amount") is not None:
            new_total = float(payload["amount"])
            interest = float(target.get("interestAmount") or 0.0)
            period_payload["total_amount"] = new_total
            period_payload["principal_amount"] = max(new_total - interest, 0.0)
            tx_updates["amount"] = new_total
            has_tx_update = True
        if "due_at" in payload and payload.get("due_at") is not None:
            period_payload["due_at"] = payload["due_at"]
            tx_updates["happened_at"] = payload["due_at"]
            has_tx_update = True
        if "note" in payload:
            tx_updates["note"] = payload.get("note")
            has_tx_update = True

        next_snapshot = update_installment_period(snapshot, period_id, period_payload)
        tx_id = target.get("txId")
        if tx_id and has_tx_update:
            next_snapshot = update_transaction(next_snapshot, tx_id, tx_updates)
        return next_snapshot, period_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_installment_period_update",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/refund-period",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def refund_installment_period_ep(
    ledger_id: str,
    plan_id: str,
    req: WriteInstallmentPeriodRefundRequest,
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
    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        plan = _find_plan(snapshot, plan_id)
        all_periods = _plan_periods(snapshot, plan_id)
        target = next((p for p in all_periods if p.get("txId") == req.tx_id), None)
        if target is None:
            raise KeyError("installment period not found for tx_id")
        if target.get("status") == "refunded":
            raise ValueError("write validation failed: period already refunded")
        actor_fields = _actor_fields(mutate_payload)

        # 单期退款:建一笔 income 交易指回原本那期的 expense tx(跟一般交易
        # 退款走同一套 refund_of_id 契约,§2.6),不额外打上 installmentPlanId
        # —— 这笔退款交易本身不是计划管理的"一期",只是普通引用,才不会被
        # `_commit_write_fast_tx` 的"分期交易不能直接删"防呆挡住用户之后
        # 想删掉/改掉这笔退款记录。
        refund_tx_payload = {
            "tx_type": "income",
            "amount": req.amount if req.amount is not None else float(target.get("totalAmount") or 0.0),
            "happened_at": now,
            "note": req.note or "分期退款",
            "account_id": plan.get("accountId"),
            "refund_of_id": req.tx_id,
            **actor_fields,
        }
        next_snapshot, refund_tx_id = _mutate_create_tx(snapshot, refund_tx_payload)
        next_snapshot = update_installment_period(
            next_snapshot, target["syncId"], {"status": "refunded", **actor_fields}
        )
        return next_snapshot, refund_tx_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_installment_period_refund",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/rebalance-from/{period_no}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def rebalance_installment_plan_ep(
    ledger_id: str,
    plan_id: str,
    period_no: int,
    req: WriteInstallmentRebalanceRequest,
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
        plan = _find_plan(snapshot, plan_id)
        all_periods = _plan_periods(snapshot, plan_id)
        target = next((p for p in all_periods if int(p.get("periodNo") or 0) == period_no), None)
        if target is None:
            raise KeyError("installment period not found")
        rebalance_targets = [
            p for p in all_periods
            if int(p.get("periodNo") or 0) >= period_no and p.get("status") != "overridden"
        ]
        if not rebalance_targets:
            raise ValueError("write validation failed: no periods left to rebalance")
        # 剩余待攤還本金 = 總金額 - 已經過去的期數本金 - overridden 期數本金
        # (overridden 的期数金额是使用者手动锁定的,既不重算也不能被"隐性"
        # 挪用它的本金份额去分给其它期数,否則重算後的期數本金加總會超過
        # 實際剩餘應攤還本金)。
        prior_principal = sum(
            float(p.get("principalAmount") or 0.0)
            for p in all_periods if int(p.get("periodNo") or 0) < period_no
        )
        overridden_in_range_principal = sum(
            float(p.get("principalAmount") or 0.0)
            for p in all_periods
            if int(p.get("periodNo") or 0) >= period_no and p.get("status") == "overridden"
        )
        remaining_principal = (
            float(plan.get("totalAmount") or 0.0) - prior_principal - overridden_in_range_principal
        )
        if remaining_principal <= 0:
            raise ValueError("write validation failed: no remaining principal to rebalance")
        repayment_method = req.repayment_method or plan.get("repaymentMethod") or "equal_principal"
        first_due = datetime.fromisoformat(target["dueAt"])
        schedule = installment_amortization.compute_periods(
            total_amount=remaining_principal,
            periods=len(rebalance_targets),
            first_period_at=first_due,
            repayment_method=repayment_method,
            interest_period=plan.get("interestPeriod") or "monthly",
            interest_rate=req.interest_rate,
            round_amounts=bool(plan.get("roundAmounts", True)),
            remainder_position=plan.get("remainderPosition") or "last",
            grace_period_months=0,
        )
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot
        for entry, computed in zip(rebalance_targets, schedule):
            period_payload = {
                "principal_amount": computed.principal_amount,
                "interest_amount": computed.interest_amount,
                "total_amount": computed.total_amount,
                **actor_fields,
            }
            next_snapshot = update_installment_period(next_snapshot, entry["syncId"], period_payload)
            tx_id = entry.get("txId")
            if tx_id:
                next_snapshot = update_transaction(
                    next_snapshot, tx_id, {"amount": computed.total_amount, **actor_fields}
                )
        plan_payload = {"interest_rate": req.interest_rate, **actor_fields}
        if req.repayment_method:
            plan_payload["repayment_method"] = req.repayment_method
        next_snapshot = update_installment_plan(next_snapshot, plan_id, plan_payload)
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
        audit_action="web_installment_plan_rebalance",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/early-repay-principal",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def early_repay_installment_principal_ep(
    ledger_id: str,
    plan_id: str,
    req: WriteInstallmentEarlyRepayRequest,
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
    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        plan = _find_plan(snapshot, plan_id)
        all_periods = _plan_periods(snapshot, plan_id)
        happened_principal = sum(
            float(p.get("principalAmount") or 0.0)
            for p in all_periods if datetime.fromisoformat(p["dueAt"]) <= now
        )
        rebalance_targets = [
            p for p in all_periods
            if datetime.fromisoformat(p["dueAt"]) > now and p.get("status") != "overridden"
        ]
        # overridden 的未来期数金额是使用者手动锁定的,既不参与重算也不能被
        # 提前还本"隐性"冲抵 —— 从可分配本金池里先扣掉,理由同 rebalance-from。
        overridden_future_principal = sum(
            float(p.get("principalAmount") or 0.0)
            for p in all_periods
            if datetime.fromisoformat(p["dueAt"]) > now and p.get("status") == "overridden"
        )
        remaining_before = float(plan.get("totalAmount") or 0.0) - happened_principal
        distributable_before = remaining_before - overridden_future_principal
        remaining_after = round(distributable_before - req.payment_amount, 2)
        if remaining_after < 0:
            raise ValueError("write validation failed: payment_amount exceeds remaining principal")
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot

        repay_tx_payload = {
            "tx_type": "expense",
            "amount": req.payment_amount,
            "happened_at": now,
            "note": "分期部分还本",
            "account_id": req.account_id or plan.get("accountId"),
            "category_id": plan.get("categoryId"),
            **actor_fields,
        }
        next_snapshot, repay_tx_id = _mutate_create_tx(next_snapshot, repay_tx_payload)
        items = next_snapshot.get("items") or []
        repay_item = next((it for it in items if it.get("syncId") == repay_tx_id), None)
        if repay_item is not None:
            repay_item["installmentPlanId"] = plan_id

        if remaining_after <= 0.005 or not rebalance_targets:
            # 可分配的(未 overridden 的)本金已经还清 —— 删除这些期数。
            for entry in rebalance_targets:
                next_snapshot = delete_installment_period(next_snapshot, entry["syncId"])
                tx_id = entry.get("txId")
                if tx_id:
                    next_snapshot = delete_transaction(next_snapshot, tx_id)
            # 只有连 overridden 的未来期数也没有剩余本金时,才算真的结清;
            # 使用者手动锁定的 overridden 期数仍然代表真实欠款,不能被这次
            # 还本静默抹掉,计画维持 active、那些期数原样保留。
            if overridden_future_principal <= 0.005:
                next_snapshot = update_installment_plan(
                    next_snapshot, plan_id, {"status": "settled", **actor_fields}
                )
                notification_service.create_notification(
                    db,
                    user_id=ledger.user_id,
                    category="reminder",
                    title="分期付款已结清",
                    body=(plan.get("note") or "") or "一个分期付款计划已透过部分还本提前结清",
                    payload={"ledgerId": ledger.external_id, "installmentPlanId": plan_id},
                )
        else:
            first_due = datetime.fromisoformat(rebalance_targets[0]["dueAt"])
            schedule = installment_amortization.compute_periods(
                total_amount=remaining_after,
                periods=len(rebalance_targets),
                first_period_at=first_due,
                repayment_method=plan.get("repaymentMethod") or "equal_principal",
                interest_period=plan.get("interestPeriod") or "monthly",
                interest_rate=float(plan.get("interestRate") or 0.0),
                round_amounts=bool(plan.get("roundAmounts", True)),
                remainder_position=plan.get("remainderPosition") or "last",
                grace_period_months=0,
            )
            for entry, computed in zip(rebalance_targets, schedule):
                period_payload = {
                    "principal_amount": computed.principal_amount,
                    "interest_amount": computed.interest_amount,
                    "total_amount": computed.total_amount,
                    **actor_fields,
                }
                next_snapshot = update_installment_period(next_snapshot, entry["syncId"], period_payload)
                tx_id = entry.get("txId")
                if tx_id:
                    next_snapshot = update_transaction(
                        next_snapshot, tx_id, {"amount": computed.total_amount, **actor_fields}
                    )
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
        audit_action="web_installment_plan_early_repay",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/payoff",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def payoff_installment_plan_ep(
    ledger_id: str,
    plan_id: str,
    req: WriteInstallmentPayoffRequest,
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
    now = req.happened_at or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        plan = _find_plan(snapshot, plan_id)
        all_periods = _plan_periods(snapshot, plan_id)
        happened_principal = sum(
            float(p.get("principalAmount") or 0.0)
            for p in all_periods if datetime.fromisoformat(p["dueAt"]) <= now
        )
        remaining_principal = float(plan.get("totalAmount") or 0.0) - happened_principal
        future_periods = [p for p in all_periods if datetime.fromisoformat(p["dueAt"]) > now]
        # 当期应计利息用"下一个尚未到期的那一期"原排程利息金额近似(提前结清
        # 时该期还没真正发生,没有更精细的按日计息基准可用 —— 已在
        # docs/MOZE_FEATURE_GAP_SD.md §2.12.1 实作笔记里标注为近似值)。
        accrued_interest = float(future_periods[0].get("interestAmount") or 0.0) if future_periods else 0.0
        settle_amount = round(remaining_principal + accrued_interest, 2)
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot

        if settle_amount > 0:
            settle_tx_payload = {
                "tx_type": "expense",
                "amount": settle_amount,
                "happened_at": now,
                "note": "分期提前结清",
                "account_id": req.account_id or plan.get("accountId"),
                "category_id": plan.get("categoryId"),
                **actor_fields,
            }
            next_snapshot, settle_tx_id = _mutate_create_tx(next_snapshot, settle_tx_payload)
            items = next_snapshot.get("items") or []
            settle_item = next((it for it in items if it.get("syncId") == settle_tx_id), None)
            if settle_item is not None:
                settle_item["installmentPlanId"] = plan_id

        for entry in future_periods:
            next_snapshot = delete_installment_period(next_snapshot, entry["syncId"])
            tx_id = entry.get("txId")
            if tx_id:
                next_snapshot = delete_transaction(next_snapshot, tx_id)
        next_snapshot = update_installment_plan(
            next_snapshot, plan_id, {"status": "settled", **actor_fields}
        )
        notification_service.create_notification(
            db,
            user_id=ledger.user_id,
            category="reminder",
            title="分期付款已结清",
            body=(plan.get("note") or "") or "一个分期付款计划已提前结清",
            payload={"ledgerId": ledger.external_id, "installmentPlanId": plan_id},
        )
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
        audit_action="web_installment_plan_payoff",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/installment-plans/{plan_id}/terminate-future",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def terminate_installment_plan_future_ep(
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
    now = _utcnow()

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        _find_plan(snapshot, plan_id)  # 404 早退
        all_periods = _plan_periods(snapshot, plan_id)
        future_periods = [p for p in all_periods if datetime.fromisoformat(p["dueAt"]) > now]
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot
        for entry in future_periods:
            next_snapshot = delete_installment_period(next_snapshot, entry["syncId"])
            tx_id = entry.get("txId")
            if tx_id:
                next_snapshot = delete_transaction(next_snapshot, tx_id)
        next_snapshot = update_installment_plan(
            next_snapshot, plan_id, {"status": "terminated", **actor_fields}
        )
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
        audit_action="web_installment_plan_terminate_future",
        mutate=_mutate,
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

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        _find_plan(snapshot, plan_id)  # 404 早退
        all_periods = _plan_periods(snapshot, plan_id)
        next_snapshot = snapshot
        # 整个计画都要删,不像 terminate-future 只砍未到期期 —— 已发生的期数
        # 生成的交易也一并清掉,否则删计画后 Transactions 页还留着一堆挂着
        # 已失效 installmentPlanId 的孤儿交易(见 MOZE_FEATURE_GAP_SD.md
        # §2.12.1 实测发现的"删除不联动"问题)。
        for entry in all_periods:
            next_snapshot = delete_installment_period(next_snapshot, entry["syncId"])
            tx_id = entry.get("txId")
            if tx_id:
                next_snapshot = delete_transaction(next_snapshot, tx_id)
        next_snapshot = delete_installment_plan(next_snapshot, plan_id, mutate_payload)
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
        audit_action="web_installment_plan_delete",
        mutate=_mutate,
    )
