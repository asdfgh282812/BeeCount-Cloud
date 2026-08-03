"""信用卡紅利回饋自動入帳(§2.9.5.4 MOZE_FEATURE_GAP_SD.md)。

規則設定結算時機(`settlement_type`)+ 目的帳戶(`reward_account_id`)後,
到期自動生成一筆 `tx_type="income"` 的交易把回饋金額存進去。已確認
`credit_card_billing.py` 的應繳/餘額計算本來就把 `income` 當負的消費處理
(`sa_case` 那幾處),所以 `reward_account_id` 選同一張卡自己時這筆 income
會正確沖抵應繳金額;選別的錢包帳戶時 `recurring_materializer.
compute_account_balance` 也會正確算進餘額。回饋是系統依規則算出來「憑空」
記給使用者的錢(不是從哪個帳戶扣出來的),所以跟 `credit_card_autopay`
不同,這裡不需要查任何「來源帳戶餘額夠不夠」。

四種 `settlement_type`:
- `immediate_after_tx`/`after_posting_date`:逐筆結算,每筆符合資格的
  交易各自在 `happened_at + settlement_days` 天後入帳一次
  (`after_posting_date` 目前算法跟 `immediate_after_tx` 相同,見
  `card_rewards.compute_settlement_date` docstring 的誠實文檔化限制)。
  `min_spend_threshold`(本期累積門檻)不適用——逐筆結算沒辦法等到「這期
  結束」才知道有沒有達標,見 `card_rewards._qualifying_transactions` 只
  受 `min_tx_amount` 過濾。
- `period_end`:整期結束後一次性入帳這條規則的 `capped_reward`(套用
  `min_spend_threshold` 跟跨卡共用上限)。
- `manual`:不自動化,完全不進這個引擎的掃描範圍。

去重靠專用表 `CardRewardPayout`(不是 `Notification`)——理由見
`models.CardRewardPayout` docstring:逐筆結算量級可能累積到上百筆,沿用
`Notification` 的「查歷史 payload 比對」去重法會讓查詢隨時間無界成長,
也會把使用者的通知中心灌爆。決策(已跟使用者確認):逐筆結算**不發任何
通知**(使用者在交易列表就看得到這筆收入),`period_end` 整批結算才發
一則通知(比照 `credit_card_autopay` 的通知慣例)。

已知限制:共用上限群組如果同時包含逐筆結算跟區間結束兩種規則混用,逐筆
結算那條在當下沒辦法即時知道區間結算那條「已經吃掉多少額度」(區間結算
是整期結束才算一次),極端情況下總額可能略微超出共用上限(最多超出一筆
區間結算的量)。v1 已知限制,不在這裡額外處理。

呼叫入口跟 `credit_card_autopay`/`debt_reminders`/`credit_card_reminders`
同一個 15 分鐘 loop(`main.py::_run_debt_reminders_once`),也可以手動
`POST /internal/tasks/materialize-recurring`(admin scope)立即觸發。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CardRewardPayout, ReadCardRewardRuleProjection, ReadTxProjection, UserAccountProjection
from . import card_rewards
from . import notifications as notification_service
from .recurring_materializer import emit_tx, new_sync_id

logger = logging.getLogger(__name__)


def _resolve_ledger_id(db: Session, *, user_id: str, account_sync_id: str) -> str | None:
    """卡片規則不掛 ledger_id(user-global 實體),用它名下任一筆交易反查
    落在哪本帳(同 `credit_card_autopay._account_name` 一帶的既有做法)。
    完全沒有交易的卡沒有可掃描的範圍,直接跳過。"""
    return db.scalar(
        select(ReadTxProjection.ledger_id).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.account_sync_id == account_sync_id,
        ).limit(1)
    )


def _already_paid_keys(db: Session, *, user_id: str, rule_sync_id: str) -> set[str]:
    rows = db.scalars(
        select(CardRewardPayout.dedup_key).where(
            CardRewardPayout.user_id == user_id,
            CardRewardPayout.rule_sync_id == rule_sync_id,
        )
    ).all()
    return set(rows)


def _account_name(db: Session, *, user_id: str, sync_id: str | None) -> str | None:
    if not sync_id:
        return None
    return db.scalar(
        select(UserAccountProjection.name).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == sync_id,
        )
    )


def _record_payout(
    db: Session, *, user_id: str, rule_sync_id: str, dedup_key: str, amount: float,
    payout_tx_sync_id: str | None, now: datetime,
) -> None:
    db.add(CardRewardPayout(
        user_id=user_id, rule_sync_id=rule_sync_id, dedup_key=dedup_key,
        amount=amount, payout_tx_sync_id=payout_tx_sync_id, created_at=now,
    ))


def _emit_reward_tx(
    db: Session, *, ledger_id: str, user_id: str, now: datetime,
    reward_account_id: str, amount: float, note: str,
) -> str:
    to_name = _account_name(db, user_id=user_id, sync_id=reward_account_id)
    item = {
        "syncId": new_sync_id("tx"),
        "type": "income",
        "amount": amount,
        "happenedAt": now.isoformat(),
        "note": note,
        "accountId": reward_account_id,
        "accountName": to_name,
        "createdByUserId": user_id,
        "updatedByUserId": user_id,
    }
    return emit_tx(db, ledger_id=ledger_id, user_id=user_id, now=now, item=item)


def _paid_in_period(
    db: Session, *, user_id: str, rule_sync_id: str, ledger_id: str, account_sync_id: str,
    period_start: date, period_end: date,
) -> float:
    """這條規則在 [period_start, period_end] 這個帳單週期/自然月裡,已經
    透過逐筆結算入帳過的金額加總(不含這次呼叫還沒 commit 的——呼叫端用
    `period_cache` 在記憶體裡疊加,不依賴這裡重複查詢)。"""
    start_dt = card_rewards._date_to_utc_dt(period_start)
    end_dt = card_rewards._date_to_utc_dt(period_end, end_of_day=True)
    rows = db.execute(
        select(CardRewardPayout.amount)
        .join(ReadTxProjection, ReadTxProjection.sync_id == CardRewardPayout.dedup_key)
        .where(
            CardRewardPayout.user_id == user_id,
            CardRewardPayout.rule_sync_id == rule_sync_id,
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.account_sync_id == account_sync_id,
            ReadTxProjection.happened_at > start_dt,
            ReadTxProjection.happened_at <= end_dt,
        )
    ).scalars().all()
    return sum(rows)


def _materialize_per_tx(
    db: Session, *, rule: ReadCardRewardRuleProjection, account: UserAccountProjection,
    ledger_id: str, now: datetime,
) -> int:
    qualifying = card_rewards._qualifying_transactions(
        db, ledger_id=ledger_id, rule=rule, period_start=None, period_end=None,
    )
    if not qualifying:
        return 0
    already_paid = _already_paid_keys(db, user_id=rule.user_id, rule_sync_id=rule.sync_id)
    pending = [item for item in qualifying if item["tx"].sync_id not in already_paid]
    if not pending:
        return 0

    count = 0
    period_cache: dict[tuple[date, date], float] = {}
    for item in pending:
        tx = item["tx"]
        settlement_date = card_rewards.compute_settlement_date(rule, tx_happened_at=tx.happened_at)
        if settlement_date is None or now.date() < settlement_date:
            continue  # 還沒到入帳日,留到下次 tick 重試,不記去重

        reward_amount = item["reward_amount"]
        if rule.cap_amount is not None:
            period = card_rewards._resolve_period(
                db, account=account, rule=rule, now=tx.happened_at.date(), period_offset=0,
            )
            if period is not None:
                if period not in period_cache:
                    period_cache[period] = _paid_in_period(
                        db, user_id=rule.user_id, rule_sync_id=rule.sync_id,
                        ledger_id=ledger_id, account_sync_id=rule.account_sync_id,
                        period_start=period[0], period_end=period[1],
                    )
                already_in_period = period_cache[period]
                remaining = max(rule.cap_amount - already_in_period, 0.0)
                reward_amount = max(min(reward_amount, remaining), 0.0)
                period_cache[period] = already_in_period + reward_amount

        payout_tx_sync_id = None
        if reward_amount > 0:
            assert rule.reward_account_id is not None  # 上層 WHERE 已过滤
            payout_tx_sync_id = _emit_reward_tx(
                db, ledger_id=ledger_id, user_id=rule.user_id, now=now,
                reward_account_id=rule.reward_account_id, amount=reward_amount,
                note=f"信用卡回饋入帳：{rule.label}（原交易 {tx.sync_id}）",
            )
        # 不管金額是否被 cap 夾到 0,都要記一筆去重,這筆交易才不會被重複評估。
        _record_payout(
            db, user_id=rule.user_id, rule_sync_id=rule.sync_id, dedup_key=tx.sync_id,
            amount=reward_amount, payout_tx_sync_id=payout_tx_sync_id, now=now,
        )
        count += 1
    return count


def _materialize_period_end(
    db: Session, *, rule: ReadCardRewardRuleProjection, account: UserAccountProjection,
    ledger_id: str, now: datetime,
) -> bool:
    """只結算「最近一次已結束的那期」(`period_offset=-1`),比照
    `credit_card_autopay`/`credit_card_reminders` 既有的「只看最近一期,
    長時間離線=錯過一次自動化」的既有限制,不另外做補窗機制。"""
    group_rules = card_rewards.fetch_cap_group_rules(db, user_id=rule.user_id, base_rules=[rule])
    results = card_rewards.compute_account_card_rewards(
        db, ledger_id=ledger_id, account=account, rules=group_rules, now=now, period_offset=-1,
    )
    card_rewards.apply_caps(results)
    this_result = next((r for r in results if r["rule"].sync_id == rule.sync_id), None)
    if this_result is None or this_result["status"] != "ok":
        return False  # 排程/資料還沒修好,留到下次重試,不記去重

    period_end = this_result["period_end"]
    dedup_key = period_end.isoformat()
    already_paid = _already_paid_keys(db, user_id=rule.user_id, rule_sync_id=rule.sync_id)
    if dedup_key in already_paid:
        return False

    reward_amount = this_result["capped_reward"]
    payout_tx_sync_id = None
    if reward_amount > 0:
        assert rule.reward_account_id is not None
        payout_tx_sync_id = _emit_reward_tx(
            db, ledger_id=ledger_id, user_id=rule.user_id, now=now,
            reward_account_id=rule.reward_account_id, amount=reward_amount,
            note=(
                f"信用卡回饋入帳：{rule.label}"
                f"（{this_result['period_start'].isoformat()}~{period_end.isoformat()}）"
            ),
        )
    _record_payout(
        db, user_id=rule.user_id, rule_sync_id=rule.sync_id, dedup_key=dedup_key,
        amount=reward_amount, payout_tx_sync_id=payout_tx_sync_id, now=now,
    )

    ledger_external_id = notification_service.resolve_ledger_external_id(db, ledger_id)
    notification_service.create_notification(
        db,
        user_id=rule.user_id,
        category="card_reward",
        title=f"信用卡回饋入帳：{rule.label}",
        body=(
            f"本期回饋 {reward_amount:.2f} 已存入"
            f"{_account_name(db, user_id=rule.user_id, sync_id=rule.reward_account_id) or '指定帳戶'}。"
        ),
        payload={
            "ruleId": rule.sync_id,
            "accountId": rule.account_sync_id,
            "periodEnd": dedup_key,
            "ledgerId": ledger_external_id,
        },
    )
    return True


def materialize_due_card_reward_payouts(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """main.py 15 分鐘 loop + `POST /internal/tasks/materialize-recurring`
    共用入口,不 commit(呼叫方決定事務邊界,同 `credit_card_autopay` 慣例)。
    掃所有 `settlement_type != "manual"` 且 `reward_account_id` 不為空、
    `enabled` 的規則,依 `settlement_type` 分派到逐筆/整期結算。回傳
    `{"tx_payouts": N, "period_payouts": M}`。"""
    now = now or datetime.now(timezone.utc)

    rules = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.settlement_type != "manual",
            ReadCardRewardRuleProjection.reward_account_id.is_not(None),
            ReadCardRewardRuleProjection.enabled.is_(True),
        )
    ).all()

    tx_payouts = 0
    period_payouts = 0
    for rule in rules:
        account = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == rule.user_id,
                UserAccountProjection.sync_id == rule.account_sync_id,
            )
        )
        if account is None:
            continue
        ledger_id = _resolve_ledger_id(db, user_id=rule.user_id, account_sync_id=rule.account_sync_id)
        if ledger_id is None:
            continue

        if rule.settlement_type in ("immediate_after_tx", "after_posting_date"):
            tx_payouts += _materialize_per_tx(db, rule=rule, account=account, ledger_id=ledger_id, now=now)
        else:  # period_end
            if _materialize_period_end(db, rule=rule, account=account, ledger_id=ledger_id, now=now):
                period_payouts += 1

    if tx_payouts or period_payouts:
        logger.info("card reward payouts: tx=%d period=%d", tx_payouts, period_payouts)
    return {"tx_payouts": tx_payouts, "period_payouts": period_payouts}
