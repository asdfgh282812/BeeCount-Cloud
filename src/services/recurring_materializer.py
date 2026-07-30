"""週期性收支(§2.2 / Phase 1.5 修正版 §2.12.2)到期物化(MOZE_FEATURE_GAP_SD.md)。

**Phase 1.5 重构说明**:Phase 1 版本这里有两个函式
(`materialize_due_recurring_rules` 逐期扫描 + `materialize_due_installment_plans`
逐期推进分期),每 15 分钟跑一次。§2.12 对照 Moze 原文重新设计后:

- **分期付款**不再需要任何排程 —— 建计画/rebalance/早偿/提前结清都在
  `routers/write/installment_plans.py` 的写入口当下一次算完全部期数,
  `materialize_due_installment_plans` 已整段删除。
- **週期性收支**改成"視窗續產生"语意:建规则时(`routers/write/
  recurring_rules.py` POST 或 `transactions.py` 的 inline `recurring` 分支)
  已经依 `services.recurring_schedule.plan_initial_generation` 批次生成过
  一个视窗(有 `end_at` 全部生成;没有则生成默认窗口),这个模块只负责给
  "没有 end_at 的长期规则"低频(main.py 改成每天一次,不再是 15 分钟)
  续产生下一段视窗,`refill_recurring_windows` 取代原来的
  `materialize_due_recurring_rules`。

到期交易怎么写,还是跟 mobile push / web write 生成交易走同一份
`projection.upsert_tx`,只是 SyncChange 的 `updated_by_device_id` 固定成
`_MATERIALIZER_DEVICE_ID`,方便 admin 日志/debug 时区分"这笔交易是排程自动
生成的,不是某台设备推的"。

调用入口:
  - `materialize_all_due(db)`:一次性跑完当前所有需要续产生的规则并自己
    commit。给 `POST /internal/tasks/materialize-recurring`(见
    src/routers/internal_tasks.py)和 main.py 的周期性 asyncio loop 用。
  - `refill_recurring_windows` 单独暴露给测试直接调用,不 commit(方便测试
    自己控制事务边界 + 断言)。

**不**做 WS 广播:这是背景任务,不挂在任何 HTTP request / websocket 连接
上,没有天然的"打给谁"的上下文。跟 SYNC_ARCHITECTURE.md §5 描述一致,
mobile/web 各自的轮询兜底会在下一次 poll 捞到这批新交易,不需要这里额外
补一次推送。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import notifications as notification_service
from . import recurring_schedule
from .. import projection
from ..concurrency import lock_ledger_for_materialize
from ..models import ReadRecurringRuleProjection, SyncChange
from ..snapshot_mutator import add_months

logger = logging.getLogger(__name__)

_MATERIALIZER_DEVICE_ID = "server-recurring-materializer"


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
    """把 rule 当前(已推进 generated_until_at / enabled)状态写一条
    SyncChange,保证 mobile/web pull 能看到规则被系统更新过(不然本地缓存的
    generated_until_at 永远停在旧值)。"""
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
        "generatedUntilAt": rule.generated_until_at.isoformat() if rule.generated_until_at else None,
        "advancedRuleJson": rule.advanced_rule_json,
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


def refill_recurring_windows(db: Session, *, now: datetime | None = None) -> int:
    """低频(main.py 每天一次)"視窗續產生":扫 `enabled=True` 且
    `generated_until_at` 距今 < `recurring_schedule.REFILL_THRESHOLD_DAYS`
    天(或从未设置过,理论上不会出现)的规则,往前补
    `recurring_schedule.REFILL_WINDOW_MONTHS` 个月并推进
    `generated_until_at`。有 `end_at` 且已经生成到 `end_at` 的规则视为"完全
    生成",直接跳过(正常情况下这种规则在生成完成的当下就已经被建规则的
    write endpoint 标记 `enabled=False` 了,这里的检查只是兜底防御)。
    返回生成的交易笔数。不 commit —— 调用方决定事务边界。"""
    now = now or datetime.now(timezone.utc)
    threshold = now + timedelta(days=recurring_schedule.REFILL_THRESHOLD_DAYS)
    rules = db.scalars(
        select(ReadRecurringRuleProjection).where(
            ReadRecurringRuleProjection.enabled.is_(True),
            or_(
                ReadRecurringRuleProjection.generated_until_at.is_(None),
                ReadRecurringRuleProjection.generated_until_at <= threshold,
            ),
        )
    ).all()

    generated = 0
    for rule in rules:
        lock_ledger_for_materialize(db, rule.ledger_id)
        rule.next_run_at = _ensure_aware(rule.next_run_at)
        if rule.end_at is not None:
            rule.end_at = _ensure_aware(rule.end_at)
        generated_until = (
            _ensure_aware(rule.generated_until_at) if rule.generated_until_at else rule.next_run_at
        )
        if rule.end_at is not None and generated_until >= rule.end_at:
            continue

        advanced_rule: dict[str, Any] | None = None
        if rule.advanced_rule_json:
            try:
                advanced_rule = json.loads(rule.advanced_rule_json)
            except json.JSONDecodeError:
                advanced_rule = None

        # generated_until 本身是"上一次已生成的最后一笔",找它之后的下一笔
        # 作为这次续产生的起点(max_count=2:第一笔一定是 generated_until
        # 自己,第二笔才是真正要生成的)。
        following = recurring_schedule.enumerate_occurrences(
            start=generated_until, end=None, frequency=rule.frequency,
            interval=rule.interval, advanced_rule=advanced_rule, max_count=2,
        )
        next_start = following[1] if len(following) > 1 else None
        if next_start is None:
            continue

        if rule.end_at is not None and next_start > rule.end_at:
            rule.generated_until_at = rule.end_at
            rule.enabled = False
            _emit_recurring_rule_update(db, rule=rule, now=now)
            continue

        window_end = add_months(next_start, recurring_schedule.REFILL_WINDOW_MONTHS)
        if rule.end_at is not None and window_end > rule.end_at:
            window_end = rule.end_at
        occurrences = recurring_schedule.enumerate_occurrences(
            start=next_start, end=window_end, frequency=rule.frequency,
            interval=rule.interval, advanced_rule=advanced_rule,
            max_count=recurring_schedule.MAX_OCCURRENCES_PER_GENERATION,
        )
        for occurrence_at in occurrences:
            tx_sync_id = _new_sync_id("tx")
            item: dict[str, Any] = {
                "syncId": tx_sync_id,
                "type": rule.tx_type,
                "amount": rule.amount,
                "happenedAt": occurrence_at.isoformat(),
                "recurringRuleId": rule.sync_id,
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

        if occurrences:
            rule.generated_until_at = occurrences[-1]
        if rule.end_at is not None and rule.generated_until_at is not None and rule.generated_until_at >= rule.end_at:
            rule.enabled = False

        if occurrences:
            notification_service.create_notification(
                db,
                user_id=rule.user_id,
                category="reminder",
                title="週期性收支已续期",
                body=(rule.note or "") or f"一条週期性收支规则已自动续产生 {len(occurrences)} 笔未来交易",
                payload={"ledgerId": rule.ledger_id, "recurringRuleId": rule.sync_id},
            )
        _emit_recurring_rule_update(db, rule=rule, now=now)

    if generated:
        logger.info("recurring_materializer: refilled %d transactions", generated)
    return generated


def materialize_all_due(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """一次性跑完週期性收支的視窗續產生并 commit。给 internal endpoint /
    周期性 asyncio loop 用 —— 自成一个事务,失败整体回滚不留半截数据。
    分期付款不再需要排程(建计画/rebalance/payoff 等写入口当下就算完全部
    期数),这里不再调用任何 installment 相关函式。"""
    now = now or datetime.now(timezone.utc)
    recurring_count = refill_recurring_windows(db, now=now)
    db.commit()
    return {"recurring_transactions": recurring_count}
