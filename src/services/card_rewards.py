"""信用卡紅利回饋計算(§2.9.5 Phase 4.5 MOZE_FEATURE_GAP_SD.md)。

規則本身(`read_card_reward_rule_projection`)是使用者手動維護的 sync
entity;這裡只做「規則 + 交易 → 當期回饋金額」的純計算,不落庫 —— 跟
§2.5 借還款 `remaining_amount`/§2.3 分期 `paid_periods` 同一個「不落表、
讀路徑即時加總」取捨,理由同樣是避免交易增刪改要連動重算回饋金額這條
額外寫路徑,省掉一整類潛在的漂移 bug。

**2026-08-06 改版**:哪筆交易算哪條規則,改成使用者記交易當下手動勾選
(`read_tx_projection.reward_rule_sync_ids_json`,可複選),系統不再用
`category_ids` 自動比對交易 —— 一來現實中一筆消費通常只適用一種回饋,
自動比對多條規則各自獨立加總會有「同一筆錢被算進兩條規則」的重複計算
問題;二來使用者比系統更清楚這筆消費實際上是用哪個回饋方案。`min_tx_amount`
(單筆門檻)/`min_spend_threshold`(當期累積門檻)這兩個「金額」判斷維持
由系統在這裡計算(使用者只負責選規則,不負責算門檻),`category_ids`
欄位(`read_card_reward_rule_projection.category_sync_ids_json`)仍保留在
規則資料表裡但不再參與這裡的比對(見 `docs/MOZE_FEATURE_GAP_SD.md` §2.9.5)。

`calc_basis == "settlement_date"` 目前行為等同 `"transaction_date"` ——
§2.10 延後入帳(`deferred_posting_at`)還沒實作,`_attribution_date` 是
唯一呼叫點,之後 §2.10 落地時只需要改這一個函式(對齊
MOZE_FEATURE_GAP_SD.md 的「三處 COALESCE 呼叫變四處,仍共用同一函式」)。

規則的 `rate_type`:
- `percentage`:每筆符合條件的消費各自算 `amount * rate_value / 100`,
  依 `rounding` 對單筆取整後加總(對齊 Moze 文件「單筆計算取整方式」)。
- `fixed_amount`:每筆符合條件的消費固定拿 `rate_value`(比如「單筆滿
  NT$100 送 15 點」這種門檻式固定回饋,門檻本身由 `min_tx_amount` 表達)。
"""
from __future__ import annotations

import calendar
import json
import math
import uuid
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ReadCardRewardRuleProjection, ReadTxProjection, SyncChange, UserAccountProjection, UserCategoryProjection
from ..sync_applier import apply_user_change_to_projection
from . import credit_card

REWARD_CATEGORY_NAME = "回饋金"
_REWARD_CATEGORY_DEVICE_ID = "server-card-reward-payout"


def ensure_reward_category(db: Session, *, user_id: str) -> str:
    """信用卡回饋自動入帳(§2.9.5.4 補強)的交易需要一個分類,不然交易列表
    顯示空白很奇怪(使用者反饋)。找不到就用跟 `exchange_rate_overrides.py`
    同款「sync push 等价」旁路建一個 user-global 的 income 分類,名稱固定
    `"回饋金"`——`category` 已經在 `USER_GLOBAL_ENTITY_TYPES`,直接複用
    `apply_user_change_to_projection` 的既有 dispatch,不用另外走
    `snapshot_mutator.create_category` + `_emit_entity_diffs` 那整套需要
    完整 ledger snapshot 的 HTTP write 引擎。同名同 kind 只會建一次(之後
    每次呼叫都直接命中既有規則),多個規則/多次入帳共用同一個分類。"""
    existing = db.scalar(
        select(UserCategoryProjection.sync_id).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.name == REWARD_CATEGORY_NAME,
            UserCategoryProjection.kind == "income",
        )
    )
    if existing is not None:
        return existing

    sync_id = f"cat_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    payload = {
        "syncId": sync_id,
        "name": REWARD_CATEGORY_NAME,
        "kind": "income",
        "createdByUserId": user_id,
        "updatedByUserId": user_id,
    }
    change = SyncChange(
        user_id=user_id,
        ledger_id=None,
        scope="user",
        entity_type="category",
        entity_sync_id=sync_id,
        action="upsert",
        payload_json=payload,
        updated_at=now,
        updated_by_device_id=_REWARD_CATEGORY_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change)
    db.flush()
    apply_user_change_to_projection(db, user_id=user_id, change=change)
    return sync_id


def _attribution_date(tx: ReadTxProjection) -> datetime:
    """交易歸屬到哪一期回饋計算的日期。§2.10 落地後,理論上
    `calc_basis == "settlement_date"` 應該改成
    `COALESCE(deferred_posting_at, happened_at)`,目前两种 calc_basis
    行為相同,因為 `deferred_posting_at` 還不存在。SQLite 讀回來的
    DateTime 可能是 naive 的,補 UTC 才能跟本模块其它 aware datetime 比較。"""
    happened_at = tx.happened_at
    if happened_at.tzinfo is None:
        happened_at = happened_at.replace(tzinfo=timezone.utc)
    return happened_at


def _date_to_utc_dt(d: date, *, end_of_day: bool = False) -> datetime:
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _calendar_month_containing(as_of: date) -> tuple[date, date]:
    last_day = calendar.monthrange(as_of.year, as_of.month)[1]
    return date(as_of.year, as_of.month, 1), date(as_of.year, as_of.month, last_day)


def _shift_calendar_month(month_start: date, months: int) -> date:
    total = month_start.month - 1 + months
    year = month_start.year + total // 12
    month = total % 12 + 1
    return date(year, month, 1)


def resolve_billing_schedule(
    db: Session, *, account: UserAccountProjection,
) -> tuple[int, int] | None:
    """`interval == "billing_cycle"` 的規則要用哪個結帳日 —— 優先用帳戶
    自己的 `billing_day`/`payment_due_day`(獨立信用卡,或帳戶本身就是
    account_group);沒有的話(掛靠群組的子卡,§2.9 群組模型下 billing_day
    只設在群組身上,見 `credit_card_billing`)回頭查它掛靠的群組。兩者都
    沒有就回傳 None,呼叫端據此把該規則標成 `no_billing_schedule`,不拋
    例外中斷其它規則的計算。"""
    if account.billing_day is not None and account.payment_due_day is not None:
        return account.billing_day, account.payment_due_day
    if account.parent_account_id:
        parent = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == account.user_id,
                UserAccountProjection.sync_id == account.parent_account_id,
            )
        )
        if parent is not None and parent.billing_day is not None and parent.payment_due_day is not None:
            return parent.billing_day, parent.payment_due_day
    return None


def _resolve_period(
    db: Session,
    *,
    account: UserAccountProjection,
    rule: ReadCardRewardRuleProjection,
    now: date,
    period_offset: int,
) -> tuple[date, date] | None:
    if rule.interval == "billing_cycle":
        schedule = resolve_billing_schedule(db, account=account)
        if schedule is None:
            return None
        billing_day, _payment_due_day = schedule
        start, end = credit_card.billing_cycle_containing(now, billing_day)
        if period_offset:
            start, end = credit_card.shift_cycle(start, end, billing_day, period_offset)
        return start, end
    # calendar_month —— 不需要 billing_day,永遠可算。
    month_start, month_end = _calendar_month_containing(now)
    if period_offset:
        shifted_start = _shift_calendar_month(month_start, period_offset)
        month_start, month_end = _calendar_month_containing(shifted_start)
    return month_start, month_end


def _round_amount(value: float, rounding: str) -> float:
    if rounding == "floor":
        return math.floor(value * 100) / 100
    if rounding == "ceil":
        return math.ceil(value * 100) / 100
    return round(value, 2)


def compute_tx_reward_amount(rule: ReadCardRewardRuleProjection, tx_amount: float) -> float:
    """單筆交易在這條規則下的回饋金額(percentage 逐筆取整 / fixed_amount
    固定值)。`compute_account_card_rewards` 的聚合迴圈跟 §2.9.5.4 payout
    引擎(`services.card_reward_payout`)共用同一份公式。"""
    if rule.rate_type == "fixed_amount":
        return rule.rate_value
    return _round_amount(tx_amount * rule.rate_value / 100, rule.rounding)


class QualifyingTx(TypedDict):
    tx: ReadTxProjection
    reward_amount: float


def _qualifying_transactions(
    db: Session,
    *,
    ledger_id: str,
    rule: ReadCardRewardRuleProjection,
    period_start: date | None,
    period_end: date | None,
) -> list[QualifyingTx]:
    """規則 + (可選)期間 → 符合條件交易 + 各自回饋金額,依 happened_at
    升序排列。`period_start`/`period_end` 皆為 `None` 時不限期間(§2.9.5.4
    payout 引擎逐筆結算用,靠 account_sync_id + LIKE 前綴天然限縮範圍)。
    `compute_account_card_rewards`(聚合顯示)、`list_rule_qualifying_
    transactions`(§2.9.5.3 明細彈窗)、payout 引擎三處共用,避免第三次
    複製同一段 LIKE 粗篩 + JSON membership + min_tx_amount 過濾邏輯。"""
    conditions = [
        ReadTxProjection.ledger_id == ledger_id,
        ReadTxProjection.account_sync_id == rule.account_sync_id,
        ReadTxProjection.tx_type == "expense",
        ReadTxProjection.reward_rule_sync_ids_json.like(f'%"{rule.sync_id}"%'),
    ]
    start_dt = end_dt = None
    if period_start is not None:
        start_dt = _date_to_utc_dt(period_start)
        conditions.append(ReadTxProjection.happened_at > start_dt)
    if period_end is not None:
        end_dt = _date_to_utc_dt(period_end, end_of_day=True)
        conditions.append(ReadTxProjection.happened_at <= end_dt)

    tx_rows = db.scalars(
        select(ReadTxProjection).where(*conditions).order_by(ReadTxProjection.happened_at.asc())
    ).all()

    items: list[QualifyingTx] = []
    for tx in tx_rows:
        attributed = _attribution_date(tx)
        if start_dt is not None and attributed <= start_dt:
            continue
        if end_dt is not None and attributed > end_dt:
            continue
        try:
            tagged_rule_ids = json.loads(tx.reward_rule_sync_ids_json or "[]")
        except (TypeError, ValueError):
            tagged_rule_ids = []
        if not isinstance(tagged_rule_ids, list) or rule.sync_id not in tagged_rule_ids:
            continue
        if rule.min_tx_amount is not None and tx.amount < rule.min_tx_amount:
            continue
        items.append({"tx": tx, "reward_amount": compute_tx_reward_amount(rule, tx.amount)})
    return items


def fetch_cap_group_rules(
    db: Session, *, user_id: str, base_rules: Sequence[ReadCardRewardRuleProjection],
) -> list[ReadCardRewardRuleProjection]:
    """`base_rules` 通常是目前這張卡的規則。把其中任何一條有
    `cap_shared_key` 的,去撈同一個 user 底下(**不限帳戶**)所有共用同一個
    key 的規則,聯集後回傳(去重)——共用上限群組本來就設計成跨卡(§2.9.5.4,
    同一家銀行的正副卡共用一個回饋額度),故意不加 account_sync_id 過濾。
    沒有任何規則帶 `cap_shared_key` 時原樣回傳 `base_rules`,不多查一次。"""
    keys = {r.cap_shared_key for r in base_rules if r.cap_shared_key}
    if not keys:
        return list(base_rules)
    extra = db.scalars(
        select(ReadCardRewardRuleProjection).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.cap_shared_key.in_(keys),
        )
    ).all()
    by_id = {r.sync_id: r for r in base_rules}
    for r in extra:
        by_id[r.sync_id] = r
    return list(by_id.values())


def compute_settlement_date(
    rule: ReadCardRewardRuleProjection,
    *,
    tx_happened_at: datetime | None = None,
    period_end: date | None = None,
) -> date | None:
    """規則的「這筆錢什麼時候入帳」。`immediate_after_tx`/`after_posting_date`
    是 `tx_happened_at.date() + settlement_days` 天(`after_posting_date`
    目前跟 `immediate_after_tx` 算法相同——沿用 `calc_basis`/
    `_attribution_date` 同款「§2.10 `deferred_posting_at` 落地前兩者行為
    一致」的誠實文檔化限制,之後只需要改這一個函式)。`period_end` 直接
    回傳 `period_end`。`manual` 永遠回傳 `None`(不自動化)。放在
    `card_rewards` 而不是 `services.card_reward_payout`,是因為
    `list_rule_qualifying_transactions`(§2.9.5.3 明細彈窗)也要用,放
    payout 模組會形成循環 import(payout 模組本來就要 import
    `card_rewards`)。"""
    if rule.settlement_type in ("immediate_after_tx", "after_posting_date"):
        if tx_happened_at is None:
            return None
        happened = tx_happened_at
        if happened.tzinfo is None:
            happened = happened.replace(tzinfo=timezone.utc)
        return happened.date() + timedelta(days=rule.settlement_days or 0)
    if rule.settlement_type == "period_end":
        return period_end
    return None


def _rule_active_in_period(
    rule: ReadCardRewardRuleProjection, period_start: date, period_end: date,
) -> bool:
    if not rule.enabled:
        return False
    if rule.starts_at is not None and rule.starts_at.date() > period_end:
        return False
    if rule.ends_at is not None and rule.ends_at.date() < period_start:
        return False
    return True


class RuleRewardResult(TypedDict):
    rule: ReadCardRewardRuleProjection
    period_start: date
    period_end: date
    qualifying_spend: float
    threshold_met: bool
    raw_reward: float
    capped_reward: float
    status: str  # "ok" | "no_billing_schedule" | "expired"


def compute_account_card_rewards(
    db: Session,
    *,
    ledger_id: str,
    account: UserAccountProjection,
    rules: Sequence[ReadCardRewardRuleProjection],
    now: datetime,
    period_offset: int = 0,
) -> list[RuleRewardResult]:
    """回傳每條規則在（`period_offset` 平移後）當期的計算結果,`capped_reward`
    此時尚未套用 `cap_shared_key` 跨規則共享上限 —— 呼叫端接着調
    `apply_caps` 就地套用。

    `account` 是「主要」帳戶(通常是呼叫端目前檢視的那張卡),但 `rules`
    不保證全部屬於它——§2.9.5.4 跨卡共用上限群組(`fetch_cap_group_rules`)
    會把其它卡的規則也一起丟進來算,那些規則的帳單週期/自然月要用**它們
    自己**的帳戶解析,不能全部套用 `account` 的 `billing_day`,否則跨卡
    規則的期間會算錯。"""
    results: list[RuleRewardResult] = []
    as_of_date = now.date()

    foreign_account_ids = {
        r.account_sync_id for r in rules
        if r.account_sync_id and r.account_sync_id != account.sync_id
    }
    foreign_accounts: dict[str, UserAccountProjection] = {}
    if foreign_account_ids:
        rows = db.scalars(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == account.user_id,
                UserAccountProjection.sync_id.in_(foreign_account_ids),
            )
        ).all()
        foreign_accounts = {a.sync_id: a for a in rows}

    for rule in rules:
        rule_account = (
            account if rule.account_sync_id == account.sync_id
            else foreign_accounts.get(rule.account_sync_id)
        )
        if rule_account is None:
            results.append({
                "rule": rule, "period_start": as_of_date, "period_end": as_of_date,
                "qualifying_spend": 0.0, "threshold_met": False,
                "raw_reward": 0.0, "capped_reward": 0.0, "status": "no_billing_schedule",
            })
            continue
        period = _resolve_period(db, account=rule_account, rule=rule, now=as_of_date, period_offset=period_offset)
        if period is None:
            results.append({
                "rule": rule, "period_start": as_of_date, "period_end": as_of_date,
                "qualifying_spend": 0.0, "threshold_met": False,
                "raw_reward": 0.0, "capped_reward": 0.0, "status": "no_billing_schedule",
            })
            continue
        period_start, period_end = period
        if not _rule_active_in_period(rule, period_start, period_end):
            results.append({
                "rule": rule, "period_start": period_start, "period_end": period_end,
                "qualifying_spend": 0.0, "threshold_met": False,
                "raw_reward": 0.0, "capped_reward": 0.0, "status": "expired",
            })
            continue

        items = _qualifying_transactions(
            db, ledger_id=ledger_id, rule=rule, period_start=period_start, period_end=period_end,
        )
        qualifying_spend = sum(item["tx"].amount for item in items)
        threshold_met = rule.min_spend_threshold is None or qualifying_spend >= rule.min_spend_threshold
        raw_reward = sum(item["reward_amount"] for item in items) if threshold_met else 0.0

        results.append({
            "rule": rule, "period_start": period_start, "period_end": period_end,
            "qualifying_spend": round(qualifying_spend, 2),
            "threshold_met": threshold_met,
            "raw_reward": round(raw_reward, 2),
            "capped_reward": round(raw_reward, 2),
            "status": "ok",
        })
    return results


def apply_caps(results: list[RuleRewardResult]) -> None:
    """`cap_shared_key` 相同的規則要先加總再一起套上限(§2.9.5 spec,例如
    玉山 U Bear 卡「網購」「一般消費」兩條規則合併共用一個上限)。沒有
    `cap_shared_key` 的規則自成一組,只受自己的 `cap_amount` 限制。組內
    超過上限時,依各規則 `raw_reward` 佔組內總額的比例分攤,最後一條用
    減法拿餘數(對齊 `credit_card_billing.compute_card_payment_allocations`
    同款做法,避免四捨五入加總對不上上限值)。就地修改每個 result 的
    `capped_reward`,不回傳值。"""
    groups: dict[str, list[int]] = {}
    for idx, r in enumerate(results):
        if r["status"] != "ok":
            continue
        key = r["rule"].cap_shared_key or f"__own_{idx}"
        groups.setdefault(key, []).append(idx)

    for idxs in groups.values():
        cap_candidates: list[float] = [
            cap for i in idxs if (cap := results[i]["rule"].cap_amount) is not None
        ]
        if not cap_candidates:
            continue
        effective_cap: float = min(cap_candidates)
        total_raw = sum(results[i]["raw_reward"] for i in idxs)
        if total_raw <= effective_cap:
            continue
        if total_raw <= 0:
            for i in idxs:
                results[i]["capped_reward"] = 0.0
            continue
        allocated = 0.0
        for pos, i in enumerate(idxs):
            if pos == len(idxs) - 1:
                share = round(effective_cap - allocated, 2)
            else:
                share = round(effective_cap * (results[i]["raw_reward"] / total_raw), 2)
            results[i]["capped_reward"] = max(share, 0.0)
            allocated += share


class RuleTransactionsDetail(TypedDict):
    rule: ReadCardRewardRuleProjection
    period_start: date
    period_end: date
    status: str
    qualifying_spend: float
    raw_reward: float
    capped_reward: float
    remaining_reward_room: float | None
    items: list[QualifyingTx]


def list_rule_qualifying_transactions(
    db: Session,
    *,
    ledger_id: str,
    account: UserAccountProjection,
    rule: ReadCardRewardRuleProjection,
    now: datetime,
    period_offset: int = 0,
) -> RuleTransactionsDetail:
    """單一規則在指定期間的明細(§2.9.5.3 交易明細彈窗):符合條件交易
    清單(各自回饋金額)+ 這條規則所屬共用上限群組(跨卡,見
    `fetch_cap_group_rules`)的剩餘額度。`remaining_reward_room` 為
    `None` 代表這個群組沒有任何上限。Moze 參考截圖裡「消費列表中間出現
    已達上限的分界線」這種逐筆命中點,因為 `apply_caps` 是整期比例分攤
    不是先到先得,沒有明確的「這一筆就是壓線的那筆」,所以這裡只回傳
    整組彙總的剩餘額度,不做逐筆分界線標記(呼叫端在清單頂部/底部顯示
    一行彙總文字即可)。"""
    as_of_date = now.date()
    period = _resolve_period(db, account=account, rule=rule, now=as_of_date, period_offset=period_offset)
    if period is None:
        return {
            "rule": rule, "period_start": as_of_date, "period_end": as_of_date,
            "status": "no_billing_schedule", "qualifying_spend": 0.0,
            "raw_reward": 0.0, "capped_reward": 0.0,
            "remaining_reward_room": rule.cap_amount, "items": [],
        }
    period_start, period_end = period
    if not _rule_active_in_period(rule, period_start, period_end):
        return {
            "rule": rule, "period_start": period_start, "period_end": period_end,
            "status": "expired", "qualifying_spend": 0.0,
            "raw_reward": 0.0, "capped_reward": 0.0,
            "remaining_reward_room": rule.cap_amount, "items": [],
        }

    items = _qualifying_transactions(
        db, ledger_id=ledger_id, rule=rule, period_start=period_start, period_end=period_end,
    )
    qualifying_spend = sum(item["tx"].amount for item in items)
    threshold_met = rule.min_spend_threshold is None or qualifying_spend >= rule.min_spend_threshold
    raw_reward = round(sum(item["reward_amount"] for item in items), 2) if threshold_met else 0.0
    if not threshold_met:
        items = [{**item, "reward_amount": 0.0} for item in items]

    group_rules = fetch_cap_group_rules(db, user_id=rule.user_id, base_rules=[rule])
    group_results = compute_account_card_rewards(
        db, ledger_id=ledger_id, account=account, rules=group_rules, now=now, period_offset=period_offset,
    )
    apply_caps(group_results)
    this_result = next(r for r in group_results if r["rule"].sync_id == rule.sync_id)
    capped_reward = this_result["capped_reward"]

    cap_candidates = [r["rule"].cap_amount for r in group_results if r["rule"].cap_amount is not None]
    if not cap_candidates:
        remaining_reward_room = None
    else:
        effective_cap = min(cap_candidates)
        group_raw_total = sum(r["raw_reward"] for r in group_results)
        remaining_reward_room = max(effective_cap - group_raw_total, 0.0)

    return {
        "rule": rule, "period_start": period_start, "period_end": period_end,
        "status": "ok", "qualifying_spend": round(qualifying_spend, 2),
        "raw_reward": raw_reward, "capped_reward": round(capped_reward, 2),
        "remaining_reward_room": remaining_reward_room, "items": items,
    }
