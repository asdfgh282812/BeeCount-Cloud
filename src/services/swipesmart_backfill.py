"""SwipeSmart 使用額度回填(Phase 14,§3.3.4)。

已對照 `swipesmart_card_id` 的信用卡帳戶,把「這一期截至目前為止的完整消費
明細(金額+商家)」回填給 SwipeSmart,由 SwipeSmart 自己的規則庫換算成
`usedCapAmount`(整批覆蓋,見 `POST /api/user/usages/recompute` 的幂等設
計,`services/swipesmart_client.py::recompute_usage`)。

跟 `card_reward_payout.materialize_due_card_reward_payouts` 同一個「不
commit,呼叫方決定事務邊界」的既有慣例 —— 這裡完全是唯讀查詢 + 對外部服務
的呼叫,本來就不需要寫 DB,不commit 也成立。

刻意偏離(見 docs/PH14 plan):`UserAccountProjection` 是 user-global,但
交易是 ledger-scoped,`credit_card_billing` 的週期計算又要吃 `ledger_id`。
這裡把範圍收斂到使用者**自己擁有**的帳本(`Ledger.user_id == user.id`),
不含分享/協作的帳本 —— 合理的 v1 邊界(「自己的信用卡」通常只會在自己的
帳本記帳)。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ledger, ReadTxProjection, User, UserAccountProjection, UserProfile
from . import card_rewards, credit_card, credit_card_billing, secret_crypto, swipesmart_client
from .deferred_posting import attribution_date_expr

logger = logging.getLogger(__name__)


def _collect_cycle_transactions(
    db: Session, *, user_id: str, account_sync_id: str, ledger_ids: list[str],
    cycle_start_dt: datetime, cycle_end_dt: datetime,
) -> list[dict]:
    if not ledger_ids:
        return []
    rows = db.execute(
        select(ReadTxProjection.amount, ReadTxProjection.merchant).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.ledger_id.in_(ledger_ids),
            ReadTxProjection.account_sync_id == account_sync_id,
            ReadTxProjection.tx_type == "expense",
            attribution_date_expr() > cycle_start_dt,
            attribution_date_expr() <= cycle_end_dt,
        )
    ).all()
    return [{"amount": float(amount or 0.0), "merchantName": merchant or ""} for amount, merchant in rows]


async def _backfill_one_user(db: Session, *, user: User, now: datetime) -> tuple[int, int]:
    """回傳 (attempted, succeeded) 這個使用者處理了幾張已對照的卡、成功回填
    幾張。單一帳戶失敗只記錄、不中斷其它帳戶(比照 job 錯誤隔離原則)。"""
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    encrypted = profile.swipesmart_api_key_encrypted if profile is not None else None
    if not encrypted:
        return 0, 0
    try:
        api_key = secret_crypto.decrypt(encrypted)
    except ValueError:
        logger.warning("swipesmart_backfill: key decrypt failed user=%s", user.id)
        return 0, 0

    mapped_accounts = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user.id,
            UserAccountProjection.account_type == "credit_card",
            UserAccountProjection.swipesmart_card_id.isnot(None),
        )
    ).all()
    if not mapped_accounts:
        return 0, 0

    owned_ledger_ids = list(
        db.scalars(select(Ledger.id).where(Ledger.user_id == user.id)).all()
    )

    attempted = 0
    succeeded = 0
    for account in mapped_accounts:
        card_id = account.swipesmart_card_id
        if not card_id:
            continue
        schedule = card_rewards.resolve_billing_schedule(db, account=account)
        if schedule is None:
            continue
        billing_day, _payment_due_day = schedule
        cycle_start, cycle_end = credit_card.billing_cycle_containing(now.date(), billing_day)
        cycle_start_dt = credit_card_billing.date_to_utc_dt(cycle_start, end_of_day=True)
        cycle_end_dt = credit_card_billing.date_to_utc_dt(cycle_end, end_of_day=True)

        transactions = _collect_cycle_transactions(
            db, user_id=user.id, account_sync_id=account.sync_id, ledger_ids=owned_ledger_ids,
            cycle_start_dt=cycle_start_dt, cycle_end_dt=cycle_end_dt,
        )
        attempted += 1
        ok = await swipesmart_client.recompute_usage(
            api_key, card_id=card_id, transactions=transactions,
        )
        if ok:
            succeeded += 1
    return attempted, succeeded


def run_swipesmart_usage_backfill(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """`services.scheduled_jobs` JOB_REGISTRY 入口,同步簽章(跟其它 job 一
    致),內部用 `asyncio.run` 驅動 async 的 swipesmart_client 呼叫 —— 呼叫時
    這裡不在任何既有 event loop 裡(排程 loop 本身用
    `asyncio.to_thread` 跑同步的 `run_job`),不會有巢狀 loop 衝突。

    不 commit(純唯讀查詢 + 外部呼叫,無需寫 DB)。回傳
    `{"users": N, "accounts_attempted": M, "accounts_succeeded": K}`。
    """
    now = now or datetime.now(timezone.utc)

    users = db.scalars(
        select(User).join(
            UserProfile, UserProfile.user_id == User.id
        ).where(UserProfile.swipesmart_api_key_encrypted.isnot(None))
    ).all()

    async def _run_all() -> tuple[int, int, int]:
        total_attempted = 0
        total_succeeded = 0
        processed_users = 0
        for user in users:
            attempted, succeeded = await _backfill_one_user(db, user=user, now=now)
            if attempted:
                processed_users += 1
            total_attempted += attempted
            total_succeeded += succeeded
        return processed_users, total_attempted, total_succeeded

    processed_users, attempted, succeeded = asyncio.run(_run_all())
    return {
        "users": processed_users,
        "accounts_attempted": attempted,
        "accounts_succeeded": succeeded,
    }
