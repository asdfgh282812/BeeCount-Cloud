"""SwipeSmart 使用額度回填(Phase 14 §3.3.4;Phase 16 改版)。

已對照 `swipesmart_card_id` 的信用卡帳戶,直接把 BeeCount 自己算好的「這期
各上限群組已經用了多少回饋金額」推給 SwipeSmart(`POST /api/user/usages/
direct`,見 `services/swipesmart_client.py::set_usages_direct`)——不再像
Phase 14 那樣把整批消費明細(金額+商家)丟給 SwipeSmart,靠它自己用商家
名稱字串去猜類別。

刻意偏離的原因(2026-08-30 使用者反饋踩到的真實 bug):BeeCount 的交易商家
欄位大多是空的,SwipeSmart 只要那張卡沒有 `General`/`GENERAL` 這條 catch-all
規則接住,商家比對就完全落空,回填永遠停在 0——而 BeeCount 自己
(`services/card_rewards.py`)本來就用使用者自己指定的分類/規則正確算出
「這期用了多少」,沒有理由讓 SwipeSmart 再用一次準確度更低的方式重新猜一遍。

跟 `card_reward_payout.materialize_due_card_reward_payouts` 同一個「不
commit,呼叫方決定事務邊界」的既有慣例 —— 這裡完全是唯讀查詢 + 對外部服務
的呼叫,本來就不需要寫 DB,不commit 也成立。

刻意偏離(見 docs/PH14 plan):`UserAccountProjection` 是 user-global,但
交易是 ledger-scoped。這裡把範圍收斂到使用者**自己擁有**的帳本
(`Ledger.user_id == user.id`),不含分享/協作的帳本 —— 合理的 v1 邊界
(「自己的信用卡」通常只會在自己的帳本記帳)。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Ledger,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    User,
    UserAccountProjection,
    UserProfile,
)
from . import card_rewards, secret_crypto, swipesmart_client

logger = logging.getLogger(__name__)


def _format_cap_amount_key(cap_amount: float) -> str:
    """比照 SwipeSmart `RewardRule.CapGroupId` 的
    `CapAmount.Value.ToString("0.####", CultureInfo.InvariantCulture)` 格式
    ——四捨五入到 4 位小數後去掉多餘的尾端 0。兩邊格式不一致會導致組出來的
    `CapGroupId` 對不上,SwipeSmart 永遠找不到這條使用量歸屬哪條規則。"""
    text = f"{round(cap_amount, 4):.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _resolve_ledger_id_for_account(
    db: Session, *, user_id: str, account_sync_id: str, owned_ledger_ids: list[str],
) -> str | None:
    """卡片規則不掛 ledger_id(user-global 實體),用它名下任一筆交易反查落在
    哪本帳(比照 `services/card_reward_payout.py::_resolve_ledger_id` 同款
    既有做法,這裡另外限制在使用者自己擁有的帳本範圍內)。完全沒有交易的卡
    沒有可掃描的範圍,回傳 None。"""
    if not owned_ledger_ids:
        return None
    return db.scalar(
        select(ReadTxProjection.ledger_id).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.ledger_id.in_(owned_ledger_ids),
            ReadTxProjection.account_sync_id == account_sync_id,
        ).limit(1)
    )


def _collect_cap_group_usages(
    db: Session, *, user_id: str, ledger_id: str, account: UserAccountProjection, now: datetime,
) -> dict[str, float]:
    """算出這個帳戶目前生效的信用卡回饋規則,依 SwipeSmart 的 CapGroupId
    (`CardId|CapAmount`)分組後,各組本期已使用的回饋金額——直接複用
    `services.card_rewards`(跟「紅利回饋」頁面同一套計算,數字保證跟使用者
    在 UI 上看到的一致)。

    先照 BeeCount 自己的 `cap_shared_key` 分組(等同 `apply_caps` 內部分組)
    算出每組已用多少,再依 `cap_amount` 二次合併——SwipeSmart 的 CapGroupId
    只認 CapAmount,不認 `cap_shared_key`。如果同一張卡有兩個不共用
    `cap_shared_key`、但剛好上限金額相同的群組,物理上就是只能送到同一個
    `CapGroupId`,這裡會強制合併並記一筆 warning(可能代表這兩組其實該共用
    同一個真實上限,或只是剛好撞到同一個上限金額,值得回頭確認,而不是預期
    它們會各自準確累加)。

    `card_rewards.compute_account_card_rewards`(Phase 22)對橫跨帳單週期的
    `calendar_month` 規則會回傳它涵蓋到的**每一個**自然月各自一筆 result
    (例如帳單週期 8/12~9/11,會拆成 8 月、9 月兩筆)——這裡只要「本期」
    (`now` 當下落在的那一個週期,不論規則是 `自然月` 還是 `帳單週期`,
    `_resolve_periods` 已經各自算好正確邊界),不能像 SwipeSmart 那樣把
    多個週期的 `capped_reward` 加在一起送出去,否則等於沒有照使用者設定的
    計算週期切開(2026-08-30 使用者反饋踩到的 bug:星展卡帳單週期橫跨
    8/9 月,回填把兩個月的用量加總送出)。"""
    own_rules = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.account_sync_id == account.sync_id,
        )
    ).all()
    if not own_rules:
        return {}

    all_rules = card_rewards.fetch_cap_group_rules(db, user_id=user_id, base_rules=own_rules)
    results = card_rewards.compute_account_card_rewards(
        db, ledger_id=ledger_id, account=account, rules=all_rules, now=now, period_offset=0,
    )
    card_rewards.apply_caps(results)

    as_of_date = now.date()
    own_rule_ids = {r.sync_id for r in own_rules}
    bee_group_totals: dict[str, float] = {}
    bee_group_cap: dict[str, float] = {}
    for r in results:
        rule = r["rule"]
        if rule.sync_id not in own_rule_ids or r["status"] != "ok" or rule.cap_amount is None:
            continue
        if not (r["period_start"] <= as_of_date <= r["period_end"]):
            continue
        key = rule.cap_shared_key or f"__own_{rule.sync_id}"
        bee_group_totals[key] = bee_group_totals.get(key, 0.0) + r["capped_reward"]
        bee_group_cap[key] = rule.cap_amount

    by_cap_amount: dict[float, float] = {}
    contributing_keys: dict[float, list[str]] = {}
    for key, total in bee_group_totals.items():
        cap_amount = bee_group_cap[key]
        by_cap_amount[cap_amount] = by_cap_amount.get(cap_amount, 0.0) + total
        contributing_keys.setdefault(cap_amount, []).append(key)

    for cap_amount, keys in contributing_keys.items():
        if len(keys) > 1:
            logger.warning(
                "swipesmart_backfill: account=%s cap_amount=%s 由 %d 個不共用 cap_shared_key "
                "的規則群組合併推送(%s)——SwipeSmart 的 CapGroupId 只認 CapAmount,無法分開送,"
                "請確認這些是不是該共用同一個真實上限。",
                account.sync_id, cap_amount, len(keys), keys,
            )

    return {_format_cap_amount_key(cap): amount for cap, amount in by_cap_amount.items()}


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

        ledger_id = _resolve_ledger_id_for_account(
            db, user_id=user.id, account_sync_id=account.sync_id, owned_ledger_ids=owned_ledger_ids,
        )
        usages = (
            _collect_cap_group_usages(db, user_id=user.id, ledger_id=ledger_id, account=account, now=now)
            if ledger_id is not None else {}
        )

        attempted += 1
        ok = await swipesmart_client.set_usages_direct(api_key, card_id=card_id, usages=usages)
        if ok:
            succeeded += 1
    return attempted, succeeded


def run_swipesmart_usage_backfill(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """`services.scheduled_jobs` JOB_REGISTRY 入口,同步簽章(跟其它 job 一
    致),內部用 `asyncio.run` 驅動 async 的 swipesmart_client 呼叫 —— 呼叫時
    這裡不在任何既有 event loop 裡(排程 loop 本身用
    `asyncio.to_thread` 跑同步的 `run_job`),不會有巢狀 loop 衝突。

    不 commit(純唯讀查詢 + 外部呼叫,無需寫 DB)。回傳
    `{"users": N, "accounts_attempted": M, "accounts_succeeded": K}`。"""
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
