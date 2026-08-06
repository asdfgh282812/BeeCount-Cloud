"""背景排程管理後台(§ 排程管理 Phase 5)。

把原本 `main.py` 裡 4 條各自獨立的 asyncio 迴圈(共 7 個邏輯排程動作)收斂
成一張 `ScheduledJobConfig` 設定表 + 這裡的統一輪詢邏輯,讓 admin 可以在
`/admin/scheduled-jobs` 後台調整頻率/停用/立即執行,不需要改代碼重新部署。

`JOB_REGISTRY` 裡每個 job 對應的底層函式都是既有、各自獨立可呼叫的
(`recurring_materializer` / `debt_reminders` / `credit_card_reminders` /
`credit_card_autopay` / `card_reward_payout`),這裡只是包一層統一成
`(Session) -> dict` 的介面 + 正規化摘要文字,不改動函式本身的行為。

`POST /internal/tasks/materialize-recurring`(`routers/internal_tasks.py`)
維持不變、繼續存在——背後呼叫的是同一批底層函式,兩邊互不干擾(各函式本身
有去重/冪等保護)。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MCPCallLog, ScheduledJobConfig

logger = logging.getLogger(__name__)

_MCP_LOG_RETENTION_DAYS = 30

# job_key -> (interval_seconds, cold_start_delay)。跟 seed migration
# (`alembic/versions/0036_scheduled_job_configs.py`)裡的預設值完全對齊——
# `ensure_default_configs` 是給測試 DB(`Base.metadata.create_all` 不會跑
# migration data seed)跟「升級後出現的新 job_key」補預設列用的,兩邊必須
# 保持一致。
_DEFAULT_JOB_CONFIGS: dict[str, tuple[int, bool]] = {
    "mcp_log_retention": (24 * 3600, True),
    "recurring_materializer": (24 * 3600, True),
    "debt_reminders": (15 * 60, False),
    "card_due_reminders": (15 * 60, False),
    "transfer_rule_materialization": (15 * 60, False),
    "card_autopay": (15 * 60, False),
    "card_reward_payout": (5 * 60, False),
}


def ensure_default_configs(db: Session) -> None:
    """幂等補齊缺失的 job_key 預設列。生產環境靠 migration seed;這裡是
    第二道保險——測試 DB 用 `Base.metadata.create_all` 建表不會跑 migration
    data seed,以及未來升級後新增 job_key 時,舊部署的 DB 也需要補列。"""
    existing = set(db.scalars(select(ScheduledJobConfig.job_key)).all())
    now = _now()
    added = False
    for job_key, (interval_seconds, cold_start_delay) in _DEFAULT_JOB_CONFIGS.items():
        if job_key in existing:
            continue
        db.add(
            ScheduledJobConfig(
                job_key=job_key,
                interval_seconds=interval_seconds,
                enabled=True,
                next_run_at=(now + timedelta(seconds=interval_seconds)) if cold_start_delay else None,
                created_at=now,
                updated_at=now,
            )
        )
        added = True
    if added:
        db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_mcp_log_retention(db: Session) -> dict:
    from sqlalchemy import delete

    cutoff = _now() - timedelta(days=_MCP_LOG_RETENTION_DAYS)
    result = db.execute(delete(MCPCallLog).where(MCPCallLog.called_at < cutoff))
    deleted = result.rowcount or 0
    return {"deleted": deleted}


def _run_recurring_materializer(db: Session) -> dict:
    from . import recurring_materializer

    result = recurring_materializer.materialize_all_due(db)
    return {"recurring_transactions": result["recurring_transactions"]}


def _run_transfer_rule_materialization(db: Session) -> dict:
    from . import recurring_materializer

    result = recurring_materializer.materialize_due_transfer_rules(db)
    return {
        "materialized": result["materialized"],
        "skipped_insufficient": result["skipped_insufficient"],
    }


def _run_debt_reminders(db: Session) -> dict:
    from . import debt_reminders

    count = debt_reminders.send_due_debt_reminders(db)
    return {"sent": count}


def _run_card_due_reminders(db: Session) -> dict:
    from . import credit_card_reminders

    count = credit_card_reminders.send_due_card_reminders(db)
    return {"sent": count}


def _run_card_autopay(db: Session) -> dict:
    from . import credit_card_autopay

    result = credit_card_autopay.materialize_due_card_autopay(db)
    return {
        "executed": result["executed"],
        "skipped_insufficient": result["skipped_insufficient"],
    }


def _run_card_reward_payout(db: Session) -> dict:
    from . import card_reward_payout

    result = card_reward_payout.materialize_due_card_reward_payouts(db)
    return {"tx_payouts": result["tx_payouts"], "period_payouts": result["period_payouts"]}


# job_key -> (db) -> dict 摘要。key 必須跟 seed migration
# (`alembic/versions/0036_scheduled_job_configs.py`)裡的 7 筆 job_key 完全對齊。
JOB_REGISTRY: dict[str, Callable[[Session], dict]] = {
    "mcp_log_retention": _run_mcp_log_retention,
    "recurring_materializer": _run_recurring_materializer,
    "debt_reminders": _run_debt_reminders,
    "card_due_reminders": _run_card_due_reminders,
    "transfer_rule_materialization": _run_transfer_rule_materialization,
    "card_autopay": _run_card_autopay,
    "card_reward_payout": _run_card_reward_payout,
}


def run_job(db: Session, job_key: str) -> dict:
    """讀設定 → 呼叫對應函式(失敗記 error 但不拋出,沿用改版前每個 tick
    都有的錯誤隔離模式)→ 更新 last_run_at/last_run_status/last_run_message/
    next_run_at → commit → 回傳摘要。job_key 不存在或設定不存在時拋
    ValueError,由 caller(router / run_due_jobs)決定怎麼處理。"""
    config = db.scalar(select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == job_key))
    if config is None:
        raise ValueError(f"unknown scheduled job: {job_key}")
    handler = JOB_REGISTRY.get(job_key)
    if handler is None:
        raise ValueError(f"no handler registered for job: {job_key}")

    now = _now()
    summary: dict
    try:
        summary = handler(db)
        config.last_run_status = "ok"
        config.last_run_message = ", ".join(f"{k}={v}" for k, v in summary.items()) or "ok"
    except Exception as exc:  # noqa: BLE001 — 排程失敗要記錄，不能讓迴圈掛掉
        db.rollback()
        logger.exception("scheduled job failed: job_key=%s", job_key)
        reloaded = db.scalar(select(ScheduledJobConfig).where(ScheduledJobConfig.job_key == job_key))
        if reloaded is None:
            raise ValueError(f"scheduled job disappeared mid-run: {job_key}") from exc
        config = reloaded
        summary = {"error": str(exc)}
        config.last_run_status = "error"
        config.last_run_message = str(exc)[:500]

    config.last_run_at = now
    config.next_run_at = now + timedelta(seconds=config.interval_seconds)
    config.updated_at = now
    db.commit()

    return {
        "job_key": job_key,
        "status": config.last_run_status,
        "message": config.last_run_message,
        "summary": summary,
        "last_run_at": config.last_run_at,
        "next_run_at": config.next_run_at,
    }


def run_due_jobs(db: Session) -> list[dict]:
    """掃描所有 enabled=True 且到期(next_run_at is None or <= now)的設定,
    逐一呼叫 run_job。主迴圈(`main.py::_start_scheduled_jobs_loop`)每 60
    秒呼叫一次。"""
    now = _now()
    due = db.scalars(
        select(ScheduledJobConfig).where(
            ScheduledJobConfig.enabled.is_(True),
            (ScheduledJobConfig.next_run_at.is_(None)) | (ScheduledJobConfig.next_run_at <= now),
        )
    ).all()
    results = []
    for config in due:
        results.append(run_job(db, config.job_key))
    return results
