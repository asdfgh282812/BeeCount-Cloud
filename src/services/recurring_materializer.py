"""週期性收支(§2.2)/ 分期付款(§2.3)到期物化(MOZE_FEATURE_GAP_SD.md Phase 1)。

到期规则/计划怎么变成一笔真正的交易,是这个模块唯一的职责 —— 跟 mobile
push / web write 生成交易走的是同一份 projection.upsert_tx,只是 SyncChange
的 `updated_by_device_id` 固定成 `_MATERIALIZER_DEVICE_ID`,方便 admin 日志 /
debug 时区分"这笔交易是排程自动生成的,不是某台设备推的"。

调用入口:
  - `materialize_all_due(db)`:一次性跑完当前所有到期项,内部自己 commit。
    给 `POST /internal/tasks/materialize-recurring`(见
    src/routers/internal_tasks.py)和 main.py 的周期性 asyncio loop 用。
  - 单独的 `materialize_due_recurring_rules` / `materialize_due_installment_plans`
    给测试直接调用,不 commit(方便测试自己控制事务边界 + 断言）。

**不**做 WS 广播:这是背景任务,不挂在任何 HTTP request / websocket 连接
上,没有天然的"打给谁"的上下文。跟 SYNC_ARCHITECTURE.md §5 描述一致,
mobile/web 各自的 30s 轮询兜底会在下一次 poll 捞到这批新交易,不需要这里
额外补一次推送。

**追赶逻辑**:一个 rule/plan 到期太久没被物化(比如 server 停机一週的 daily
规则),会在一次调用里连续生成多笔交易,直到 next_run_at 追上 now 为止;
每个 rule/plan 设置 `_MAX_CATCHUP_PERIODS` 上限防止极端配置(比如 interval
写成很小的值)一次吃满整个 worker。剩余的留到下一次物化调用继续追。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import notifications as notification_service
from .. import projection
from ..concurrency import lock_ledger_for_materialize
from ..models import ReadInstallmentPlanProjection, ReadRecurringRuleProjection, SyncChange
from ..snapshot_mutator import add_months, next_run_from

logger = logging.getLogger(__name__)

_MATERIALIZER_DEVICE_ID = "server-recurring-materializer"
_MAX_CATCHUP_PERIODS = 366


def _new_sync_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite 存 `DateTime(timezone=True)` 但驱动读回来是 naive —— 跟 `now`
    (tz-aware)比较前先补 UTC 标记,不然 `<=` 直接 TypeError。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _emit_tx(
    db: Session,
    *,
    ledger_id: str,
    user_id: str,
    now: datetime,
    item: dict[str, Any],
) -> str:
    """写一条 transaction SyncChange + 同事务 projection.upsert_tx。返回新 tx 的 sync_id。"""
    tx_sync_id = str(item["syncId"])
    change_row = SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=tx_sync_id,
        action="upsert",
        payload_json=item,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_tx(
        db, ledger_id=ledger_id, user_id=user_id,
        source_change_id=change_row.change_id, payload=item,
    )
    return tx_sync_id


def _emit_recurring_rule_update(
    db: Session, *, rule: ReadRecurringRuleProjection, now: datetime,
) -> None:
    """把 rule 当前(已推进 next_run_at / enabled)状态写一条 SyncChange,
    保证 mobile/web pull 能看到规则被系统更新过(不然本地缓存的 next_run_at
    永远停在旧值)。"""
    payload = {
        "syncId": rule.sync_id,
        "txType": rule.tx_type,
        "amount": rule.amount,
        "note": rule.note,
        "categoryId": rule.category_sync_id,
        "accountId": rule.account_sync_id,
        "fromAccountId": rule.from_account_sync_id,
        "toAccountId": rule.to_account_sync_id,
        "frequency": rule.frequency,
        "interval": rule.interval,
        "nextRunAt": rule.next_run_at.isoformat(),
        "endAt": rule.end_at.isoformat() if rule.end_at else None,
        "enabled": rule.enabled,
    }
    change_row = SyncChange(
        user_id=rule.user_id,
        ledger_id=rule.ledger_id,
        scope="ledger",
        entity_type="recurring_rule",
        entity_sync_id=rule.sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=rule.user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_recurring_rule(
        db, ledger_id=rule.ledger_id, user_id=rule.user_id,
        source_change_id=change_row.change_id, payload=payload,
    )


def _emit_installment_plan_update(
    db: Session, *, plan: ReadInstallmentPlanProjection, now: datetime,
) -> None:
    payload = {
        "syncId": plan.sync_id,
        "totalAmount": plan.total_amount,
        "periods": plan.periods,
        "periodAmount": plan.period_amount,
        "firstPeriodAt": plan.first_period_at.isoformat(),
        "nextPeriodAt": plan.next_period_at.isoformat(),
        "paidPeriods": plan.paid_periods,
        "accountId": plan.account_sync_id,
        "categoryId": plan.category_sync_id,
        "note": plan.note,
        "status": plan.status,
    }
    change_row = SyncChange(
        user_id=plan.user_id,
        ledger_id=plan.ledger_id,
        scope="ledger",
        entity_type="installment_plan",
        entity_sync_id=plan.sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_MATERIALIZER_DEVICE_ID,
        updated_by_user_id=plan.user_id,
    )
    db.add(change_row)
    db.flush()
    projection.upsert_installment_plan(
        db, ledger_id=plan.ledger_id, user_id=plan.user_id,
        source_change_id=change_row.change_id, payload=payload,
    )


def materialize_due_recurring_rules(
    db: Session, *, now: datetime | None = None,
) -> int:
    """扫 `enabled=True AND next_run_at <= now` 的规则,各自生成到期交易并
    推进 next_run_at;超过 end_at 时自动 disable。返回生成的交易笔数。
    不 commit —— 调用方决定事务边界。"""
    now = now or datetime.now(timezone.utc)
    rules = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.enabled.is_(True),
            ReadRecurringRuleProjection.next_run_at <= now,
        )
    ).all()

    generated = 0
    for rule in rules:
        lock_ledger_for_materialize(db, rule.ledger_id)
        rule.next_run_at = _ensure_aware(rule.next_run_at)
        if rule.end_at is not None:
            rule.end_at = _ensure_aware(rule.end_at)
        iterations = 0
        while (
            rule.enabled
            and rule.next_run_at <= now
            and iterations < _MAX_CATCHUP_PERIODS
        ):
            occurrence_at = rule.next_run_at
            tx_sync_id = _new_sync_id("tx")
            item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": rule.tx_type,
                "amount": rule.amount,
                "happenedAt": occurrence_at.isoformat(),
                "createdByUserId": rule.user_id,
                "updatedByUserId": rule.user_id,
            }
            if rule.note:
                item["note"] = rule.note
            if rule.category_sync_id:
                item["categoryId"] = rule.category_sync_id
            if rule.account_sync_id:
                item["accountId"] = rule.account_sync_id
            if rule.from_account_sync_id:
                item["fromAccountId"] = rule.from_account_sync_id
            if rule.to_account_sync_id:
                item["toAccountId"] = rule.to_account_sync_id
            _emit_tx(db, ledger_id=rule.ledger_id, user_id=rule.user_id, now=now, item=item)
            generated += 1

            new_next_run = next_run_from(occurrence_at, rule.frequency, rule.interval)
            rule.next_run_at = new_next_run
            if rule.end_at is not None and new_next_run > rule.end_at:
                rule.enabled = False
            iterations += 1

        notification_service.create_notification(
            db,
            user_id=rule.user_id,
            category="reminder",
            title="週期性记账已自动生成",
            body=(rule.note or "") or "一笔週期性收支已到期并自动记账",
            payload={"ledgerId": rule.ledger_id, "recurringRuleId": rule.sync_id},
        )
        _emit_recurring_rule_update(db, rule=rule, now=now)

    if generated:
        logger.info("recurring_materializer: generated %d transactions", generated)
    return generated


def materialize_due_installment_plans(
    db: Session, *, now: datetime | None = None,
) -> int:
    """扫 `status='active' AND next_period_at <= now` 的分期计划,各自生成
    到期那期交易,推进 paid_periods / next_period_at;付满 periods 后
    status 改 'settled'。返回生成的交易笔数。不 commit。"""
    now = now or datetime.now(timezone.utc)
    plans = db.scalars(
        select(ReadInstallmentPlanProjection).where(
            ReadInstallmentPlanProjection.status == "active",
            ReadInstallmentPlanProjection.next_period_at <= now,
        )
    ).all()

    generated = 0
    for plan in plans:
        lock_ledger_for_materialize(db, plan.ledger_id)
        plan.next_period_at = _ensure_aware(plan.next_period_at)
        iterations = 0
        while (
            plan.status == "active"
            and plan.paid_periods < plan.periods
            and plan.next_period_at <= now
            and iterations < _MAX_CATCHUP_PERIODS
        ):
            occurrence_at = plan.next_period_at
            tx_sync_id = _new_sync_id("tx")
            item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": "expense",
                "amount": plan.period_amount,
                "happenedAt": occurrence_at.isoformat(),
                "installmentPlanId": plan.sync_id,
                "createdByUserId": plan.user_id,
                "updatedByUserId": plan.user_id,
            }
            if plan.note:
                item["note"] = plan.note
            if plan.category_sync_id:
                item["categoryId"] = plan.category_sync_id
            if plan.account_sync_id:
                item["accountId"] = plan.account_sync_id
            _emit_tx(db, ledger_id=plan.ledger_id, user_id=plan.user_id, now=now, item=item)
            generated += 1

            plan.paid_periods += 1
            if plan.paid_periods >= plan.periods:
                plan.status = "settled"
            else:
                plan.next_period_at = add_months(occurrence_at, 1)
            iterations += 1

        if plan.status == "settled":
            notification_service.create_notification(
                db,
                user_id=plan.user_id,
                category="reminder",
                title="分期付款已结清",
                body=(plan.note or "") or "一个分期付款计划已付满全部期数",
                payload={"ledgerId": plan.ledger_id, "installmentPlanId": plan.sync_id},
            )
        _emit_installment_plan_update(db, plan=plan, now=now)

    if generated:
        logger.info("installment_materializer: generated %d transactions", generated)
    return generated


def materialize_all_due(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """一次性跑完 recurring rule + installment plan 的到期物化并 commit。
    给 internal endpoint / 周期性 asyncio loop 用 —— 自成一个事务,失败整体
    回滚不留半截数据。"""
    now = now or datetime.now(timezone.utc)
    recurring_count = materialize_due_recurring_rules(db, now=now)
    installment_count = materialize_due_installment_plans(db, now=now)
    db.commit()
    return {"recurring_transactions": recurring_count, "installment_transactions": installment_count}
