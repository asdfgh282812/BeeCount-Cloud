"""信用卡回饋「即時連動的專屬回饋交易」(2026-10-16)。

設計全文見 App repo `docs/changes/2026-10-16-live-card-reward-transactions.md`。摘要:

- 消費一存檔(App push / Web 寫入 / 排程產生),同一個 DB 交易裡就把它影響到的
  回饋重算一次,回饋交易立刻出現在入帳日。觸發點是 `projection.upsert_tx` /
  `delete_tx` / 回饋規則 upsert/delete 留下的標記(`src/card_reward_dirty.py`),
  在 `Session.before_commit` 統一處理——所以所有寫入路徑都涵蓋,不需要每個 router
  各自呼叫。
- **整期模式**(`settlement_type == "period_end"`):同一條規則、同一期(同一本帳)
  只有一筆回饋交易,`dedup_key = live:{start}~{end}:{ledger_id}`,金額 =
  `Reward(全部消費) − Reward(只算舊套用消費)`(舊套用那部分仍由舊排程到期發)。
- **逐筆模式**(`immediate_after_tx` / `after_posting_date`):每筆消費 × 每條勾選的
  規則 = 一筆獨立回饋交易,`dedup_key` = 來源消費 sync_id。有同期上限時依日期順序
  分配(先扣舊排程已發的部分)。
- **新舊切點是「消費 × 規則」配對**:`card_reward_legacy_links` 裡的配對(alembic
  0076 部署當下的快照)= 舊套用,只由 `card_reward_payout` 舊排程處理;其餘 = 新套用。
- 金額一律由系統算;日期、帳戶、專案比對 `proposed_*`(上次系統寫入的值),一樣才
  跟著新規則移動,不一樣代表使用者改過、保留不動;分類、備註、標籤只在新建時給預設值。
- **已入帳(凍結)**:回饋交易日期 ≤ 今天(業務時區)。整期模式凍結後一律不再動;
  逐筆模式凍結後只有「它自己那筆消費」改了才重算(規則修改不動它)。
- 使用者刪掉即時回饋交易(App/Web 會擋,舊版 App 可能送來)→ 該列 `dismissed`,
  之後不再重建。
- 規則修改只重算還沒入帳的回饋(不會替已過入帳日的期別補建),所以規則不再上鎖。

`card_rewards.live_enabled()` 為 False 時只維護 legacy 配對表(新勾的配對也算舊套用),
不產生任何即時交易。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from .. import card_reward_dirty, projection
from ..models import (
    CardRewardLegacyLink,
    CardRewardPayout,
    ExchangeRateCache,
    Ledger,
    ReadCardRewardRuleProjection,
    ReadTxProjection,
    SyncChange,
    UserAccountProjection,
    UserExchangeRateProjection,
)
from . import card_rewards
from . import notifications as notification_service
from .business_time import business_date_start_utc, business_today, to_business_date

logger = logging.getLogger(__name__)

LIVE_DEVICE_ID = "server-card-reward-live"
PER_TX_TYPES = ("immediate_after_tx", "after_posting_date")
_TOUCHED_LEDGERS_KEY = "card_reward_live_touched_ledgers"
_UUID_NAMESPACE = uuid.UUID("6b1d9e0a-5f3c-4f61-9a55-6c1f0b7e2d41")


# --------------------------------------------------------------------------- #
# 識別                                                                          #
# --------------------------------------------------------------------------- #

def period_dedup_key(period_start: date, period_end: date, ledger_id: str) -> str:
    """整期模式的去重鍵。`live:` 前綴避免跟舊排程同一期的 key(期末日 iso)撞唯一索引;
    帶 ledger_id 是因為同一張卡的消費可能分散在不同帳本,各本帳各自一筆。App/Web
    只解析前面的 `live:{start}~{end}` 來顯示期別。"""
    return f"live:{period_start.isoformat()}~{period_end.isoformat()}:{ledger_id}"


def live_tx_sync_id(user_id: str, rule_sync_id: str, dedup_key: str) -> str:
    """回饋交易的 sync_id 由 (user, rule, dedup_key) 決定:重算一定命中同一筆,
    刪掉再重建(例如門檻先達標、退款後未達、又補了消費)也是同一個 id。"""
    digest = uuid.uuid5(_UUID_NAMESPACE, f"beecount:card_reward_live:{user_id}:{rule_sync_id}:{dedup_key}")
    return f"tx_rw{digest.hex[:20]}"


# --------------------------------------------------------------------------- #
# 小工具                                                                        #
# --------------------------------------------------------------------------- #

def _rule_ids(raw: str | None) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _same_instant(a: datetime | None, b: datetime | None) -> bool:
    a, b = _aware(a), _aware(b)
    if a is None or b is None:
        return a is b
    return abs((a - b).total_seconds()) < 1


def _attribution_business_date(snap: dict[str, Any]) -> date:
    value = snap.get("deferred_posting_at") or snap["happened_at"]
    return to_business_date(value)


def _rule_can_pay(rule: ReadCardRewardRuleProjection | None) -> bool:
    return (
        rule is not None
        and bool(rule.enabled)
        and rule.settlement_type != "manual"
        and bool(rule.reward_account_id)
    )


@dataclass(frozen=True)
class _PeriodTarget:
    user_id: str
    rule_sync_id: str
    ledger_id: str
    period_start: date
    period_end: date


@dataclass(frozen=True)
class _SourceTarget:
    user_id: str
    rule_sync_id: str
    ledger_id: str
    source_sync_id: str


@dataclass(frozen=True)
class _PerTxPeriodTarget:
    user_id: str
    rule_sync_id: str
    ledger_id: str
    period_start: date
    period_end: date


class _Ctx:
    def __init__(self, db: Session, now: datetime) -> None:
        self.db = db
        self.now = now
        self.today = business_today(now)
        self._rules: dict[tuple[str, str], ReadCardRewardRuleProjection | None] = {}
        self._accounts: dict[tuple[str, str], UserAccountProjection | None] = {}
        self.touched_ledgers: set[str] = set()
        # target → "tx"(消費觸發)或 "rule"(規則/排程觸發,只動未入帳的)
        self.targets: dict[object, str] = {}

    def rule(self, user_id: str, rule_sync_id: str) -> ReadCardRewardRuleProjection | None:
        key = (user_id, rule_sync_id)
        if key not in self._rules:
            self._rules[key] = self.db.scalar(
                select(ReadCardRewardRuleProjection).where(
                    ReadCardRewardRuleProjection.user_id == user_id,
                    ReadCardRewardRuleProjection.sync_id == rule_sync_id,
                )
            )
        return self._rules[key]

    def account(self, user_id: str, sync_id: str | None) -> UserAccountProjection | None:
        if not sync_id:
            return None
        key = (user_id, sync_id)
        if key not in self._accounts:
            self._accounts[key] = self.db.scalar(
                select(UserAccountProjection).where(
                    UserAccountProjection.user_id == user_id,
                    UserAccountProjection.sync_id == sync_id,
                )
            )
        return self._accounts[key]

    def period_for(self, rule: ReadCardRewardRuleProjection, d: date) -> tuple[date, date] | None:
        account = self.account(rule.user_id, rule.account_sync_id)
        if account is None:
            return None
        return card_rewards._resolve_period(self.db, account=account, rule=rule, now=d, period_offset=0)

    def add(self, target: object, mode: str) -> None:
        if self.targets.get(target) != "tx":
            self.targets[target] = mode


def _find_payout(db: Session, *, user_id: str, rule_sync_id: str, dedup_key: str) -> CardRewardPayout | None:
    return db.scalar(
        select(CardRewardPayout).where(
            CardRewardPayout.user_id == user_id,
            CardRewardPayout.rule_sync_id == rule_sync_id,
            CardRewardPayout.dedup_key == dedup_key,
            CardRewardPayout.live.is_(True),
        )
    )


def _tx_row(db: Session, *, ledger_id: str, sync_id: str | None) -> ReadTxProjection | None:
    if not sync_id:
        return None
    return db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
    )


def _reward_row(db: Session, payout: CardRewardPayout | None) -> ReadTxProjection | None:
    if payout is None or not payout.ledger_id:
        return None
    return _tx_row(db, ledger_id=payout.ledger_id, sync_id=payout.payout_tx_sync_id)


def _is_frozen(ctx: _Ctx, row: ReadTxProjection | None) -> bool:
    return row is not None and to_business_date(row.happened_at) <= ctx.today


# --------------------------------------------------------------------------- #
# 寫回饋交易                                                                    #
# --------------------------------------------------------------------------- #

def _currency_fields(
    db: Session, *, user_id: str, ledger_id: str, account_sync_id: str, amount: float,
) -> dict[str, object]:
    """同 `card_reward_payout._currency_fields_for_reward`,但不打外部匯率 API:
    這裡跑在使用者寫入的 commit 裡(可能就在 event loop 執行緒),只用手動匯率或
    伺服器已快取的匯率,都沒有才 1:1(App 端「重新計算外幣折算」可事後補)。"""
    base = (db.scalar(select(Ledger.currency).where(Ledger.id == ledger_id)) or "CNY").strip().upper()
    account_currency = db.scalar(
        select(UserAccountProjection.currency).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_sync_id,
        )
    )
    cc = (account_currency or base).strip().upper()
    if cc == base:
        return {}
    native: float | None = None
    override = db.scalar(
        select(UserExchangeRateProjection.rate).where(
            UserExchangeRateProjection.user_id == user_id,
            UserExchangeRateProjection.base_currency == base,
            UserExchangeRateProjection.quote_currency == cc,
        ).limit(1)
    )
    if override is not None:
        try:
            rate = float(override)
            if rate > 0:
                native = amount * rate
        except (TypeError, ValueError):
            native = None
    if native is None:
        cache = db.get(ExchangeRateCache, base)
        try:
            payload = dict(cache.payload_json) if cache is not None else {}
            raw = payload.get(cc) or payload.get(cc.lower())
            x = float(raw) if raw is not None else 0.0
            native = amount / x if x > 0 else amount
        except (TypeError, ValueError):
            native = amount
    return {"currencyCode": cc, "nativeAmount": round(native, 6)}


def _account_name(db: Session, *, user_id: str, sync_id: str | None) -> str | None:
    if not sync_id:
        return None
    return db.scalar(
        select(UserAccountProjection.name).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == sync_id,
        )
    )


def _emit_change(db: Session, ctx: _Ctx, *, user_id: str, ledger_id: str, sync_id: str,
                 action: str, payload: dict[str, Any]) -> None:
    change = SyncChange(
        user_id=user_id,
        ledger_id=ledger_id,
        scope="ledger",
        entity_type="transaction",
        entity_sync_id=sync_id,
        action=action,
        payload_json=payload,
        updated_at=ctx.now,
        updated_by_device_id=LIVE_DEVICE_ID,
        updated_by_user_id=user_id,
    )
    db.add(change)
    db.flush()
    with card_reward_dirty.suppress(db):
        if action == "delete":
            projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)
        else:
            projection.upsert_tx(
                db, ledger_id=ledger_id, user_id=user_id,
                source_change_id=change.change_id, payload=payload,
            )
    ctx.touched_ledgers.add(ledger_id)


def _delete_live(db: Session, ctx: _Ctx, payout: CardRewardPayout, *, keep_row: bool = False) -> None:
    """刪掉即時回饋交易 + payout 列(`keep_row=True` 時保留 dismissed 的列)。"""
    row = _reward_row(db, payout)
    if row is not None:
        _emit_change(db, ctx, user_id=payout.user_id, ledger_id=row.ledger_id,
                     sync_id=row.sync_id, action="delete", payload={})
    if keep_row:
        payout.payout_tx_sync_id = None
    else:
        db.delete(payout)
    db.flush()


def _write_live(
    db: Session,
    ctx: _Ctx,
    *,
    rule: ReadCardRewardRuleProjection,
    ledger_id: str,
    dedup_key: str,
    payout: CardRewardPayout | None,
    amount: float,
    happened_at: datetime,
    project_id: str | None,
    note: str,
    period: tuple[date, date] | None,
    source_tx_id: str | None,
) -> None:
    """新建或更新一筆即時回饋交易。金額一律覆寫;日期/帳戶/專案只在使用者沒改過
    (= 跟 `proposed_*` 相同)時換成新的系統值;沒有任何變化就不寫 SyncChange。"""
    from ..routers.write._shared import _projection_row_to_tx_dict

    user_id = rule.user_id
    account_id = rule.reward_account_id
    assert account_id is not None
    amount = round(amount, 2)
    row = _reward_row(db, payout)

    if row is None:
        sync_id = live_tx_sync_id(user_id, rule.sync_id, dedup_key)
        existing = _tx_row(db, ledger_id=ledger_id, sync_id=sync_id)
        if existing is not None:
            row = existing  # payout 列被清掉但交易還在(理論上不會發生):接回去
    if row is None:
        category_id = card_rewards.ensure_reward_category(db, user_id=user_id)
        item: dict[str, Any] = {
            "syncId": live_tx_sync_id(user_id, rule.sync_id, dedup_key),
            "type": "income",
            "amount": amount,
            "happenedAt": happened_at.isoformat(),
            "note": note,
            "accountId": account_id,
            "accountName": _account_name(db, user_id=user_id, sync_id=account_id),
            "categoryId": category_id,
            "categoryName": card_rewards.REWARD_CATEGORY_NAME,
            "categoryKind": "income",
            "createdByUserId": user_id,
            "updatedByUserId": user_id,
            "rewardPayoutRuleId": rule.sync_id,
            "rewardPayoutKey": dedup_key,
        }
        item.update(_currency_fields(db, user_id=user_id, ledger_id=ledger_id,
                                     account_sync_id=account_id, amount=amount))
        if source_tx_id is not None:
            item["rewardSourceTxId"] = source_tx_id
        if project_id is not None:
            item["projectId"] = project_id
        _emit_change(db, ctx, user_id=user_id, ledger_id=ledger_id, sync_id=str(item["syncId"]),
                     action="upsert", payload=item)
        tx_sync_id = str(item["syncId"])
    else:
        item = _projection_row_to_tx_dict(row)
        before = dict(item)
        # 金額與類型由系統決定(前端金額唯讀;舊版 App 若改成支出也在這裡拉回來)。
        item["type"] = "income"
        item["amount"] = amount
        prev_happened = payout.proposed_happened_at if payout is not None else None
        prev_account = payout.proposed_account_sync_id if payout is not None else None
        prev_project = payout.proposed_project_id if payout is not None else None
        if payout is None or _same_instant(row.happened_at, prev_happened):
            item["happenedAt"] = happened_at.isoformat()
        if payout is None or row.account_sync_id == prev_account:
            item["accountId"] = account_id
            item["accountName"] = _account_name(db, user_id=user_id, sync_id=account_id)
        if payout is None or (row.project_sync_id or None) == (prev_project or None):
            if project_id is not None:
                item["projectId"] = project_id
            else:
                item.pop("projectId", None)
        item.pop("currencyCode", None)
        item.pop("nativeAmount", None)
        item.update(_currency_fields(db, user_id=user_id, ledger_id=ledger_id,
                                     account_sync_id=str(item["accountId"]), amount=amount))
        item["rewardPayoutRuleId"] = rule.sync_id
        item["rewardPayoutKey"] = dedup_key
        if source_tx_id is not None:
            item["rewardSourceTxId"] = source_tx_id
        tx_sync_id = row.sync_id
        if _payload_changed(before, item):
            item["updatedByUserId"] = user_id
            _emit_change(db, ctx, user_id=user_id, ledger_id=row.ledger_id, sync_id=row.sync_id,
                         action="upsert", payload=item)

    if payout is None:
        payout = CardRewardPayout(
            user_id=user_id, rule_sync_id=rule.sync_id, dedup_key=dedup_key,
            amount=amount, created_at=ctx.now, live=True, dismissed=False,
        )
        db.add(payout)
    payout.amount = amount
    payout.payout_tx_sync_id = tx_sync_id
    payout.ledger_id = ledger_id
    payout.period_start = period[0] if period else None
    payout.period_end = period[1] if period else None
    payout.proposed_happened_at = happened_at
    payout.proposed_account_sync_id = account_id
    payout.proposed_project_id = project_id
    db.flush()


_COMPARE_KEYS = (
    "type", "amount", "happenedAt", "accountId", "projectId", "currencyCode", "nativeAmount",
    "rewardPayoutRuleId", "rewardPayoutKey", "rewardSourceTxId",
)


def _payload_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    for key in _COMPARE_KEYS:
        a, b = before.get(key), after.get(key)
        if key == "happenedAt":
            if not _same_instant(_parse_iso(a), _parse_iso(b)):
                return True
        elif isinstance(a, (int, float)) or isinstance(b, (int, float)):
            if a is None or b is None or abs(float(a) - float(b)) > 1e-6:
                return True
        elif a != b:
            return True
    return False


def _parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 重算:整期模式                                                                #
# --------------------------------------------------------------------------- #

def _group_capped(
    db: Session, ctx: _Ctx, *, rule: ReadCardRewardRuleProjection, ledger_id: str,
    period: tuple[date, date], pair_filter: str,
) -> float:
    """這條規則這一期套完共用上限後的回饋。共用上限群組裡其它規則只在「它自己
    的期別剛好是同一期」時才一起分攤(對齊 `apply_caps` 以 (key, 期別) 分組)。"""
    group = card_rewards.fetch_cap_group_rules(db, user_id=rule.user_id, base_rules=[rule])
    results = []
    for member in group:
        if member.sync_id != rule.sync_id and ctx.period_for(member, period[1]) != period:
            continue
        results.append(card_rewards.compute_rule_period_result(
            db, ledger_id=ledger_id, rule=member, period_start=period[0], period_end=period[1],
            pair_filter=pair_filter,
        ))
    card_rewards.apply_caps(results)
    for r in results:
        if r["rule"].sync_id == rule.sync_id and r["status"] == "ok":
            return float(r["capped_reward"])
    return 0.0


def _recompute_period(db: Session, ctx: _Ctx, target: _PeriodTarget, mode: str) -> None:
    period = (target.period_start, target.period_end)
    dedup_key = period_dedup_key(target.period_start, target.period_end, target.ledger_id)
    payout = _find_payout(db, user_id=target.user_id, rule_sync_id=target.rule_sync_id, dedup_key=dedup_key)
    if payout is not None and payout.dismissed:
        return
    row = _reward_row(db, payout)
    if _is_frozen(ctx, row):
        return  # 已入帳:補勾/刪除消費、改規則都不再動它(避免改到已對過帳的歷史)

    rule = ctx.rule(target.user_id, target.rule_sync_id)
    amount = 0.0
    settlement_date: date | None = None
    if (
        _rule_can_pay(rule)
        and rule is not None
        and rule.settlement_type == "period_end"
        and ctx.period_for(rule, target.period_end) == period
    ):
        settlement_date = card_rewards.compute_settlement_date(rule, period_end=target.period_end)
        if settlement_date is not None:
            if mode == "rule" and row is None and settlement_date <= ctx.today:
                return  # 規則修改/保險重算只動還沒入帳的期別,不替過去的期別補建
            all_reward = _group_capped(db, ctx, rule=rule, ledger_id=target.ledger_id,
                                       period=period, pair_filter="all")
            legacy_reward = _group_capped(db, ctx, rule=rule, ledger_id=target.ledger_id,
                                          period=period, pair_filter="legacy")
            amount = max(round(all_reward - legacy_reward, 2), 0.0)

    if rule is None or settlement_date is None or amount <= 0:
        if payout is not None:
            _delete_live(db, ctx, payout)
        return
    _write_live(
        db, ctx, rule=rule, ledger_id=target.ledger_id, dedup_key=dedup_key, payout=payout,
        amount=amount, happened_at=business_date_start_utc(settlement_date),
        project_id=_resolve_project(db, ledger_id=target.ledger_id, rule=rule, source_tx=None),
        note=f"信用卡回饋：{rule.label}（{target.period_start.isoformat()}~{target.period_end.isoformat()}）",
        period=period, source_tx_id=None,
    )


def _resolve_project(db: Session, *, ledger_id: str, rule: ReadCardRewardRuleProjection,
                     source_tx: ReadTxProjection | None) -> str | None:
    from .card_reward_payout import _resolve_reward_project_id
    return _resolve_reward_project_id(db, ledger_id=ledger_id, rule=rule, source_tx=source_tx)


# --------------------------------------------------------------------------- #
# 重算:逐筆模式                                                                #
# --------------------------------------------------------------------------- #

def _per_tx_amount(rule: ReadCardRewardRuleProjection, item: card_rewards.QualifyingTx) -> float:
    return card_rewards._round_amount(item["reward_amount"], rule.total_rounding, to_integer=True)


def _write_per_tx(db: Session, ctx: _Ctx, *, rule: ReadCardRewardRuleProjection, ledger_id: str,
                  source: ReadTxProjection, payout: CardRewardPayout | None, amount: float,
                  period: tuple[date, date] | None) -> None:
    settlement_date = card_rewards.compute_settlement_date(rule, tx_happened_at=source.happened_at)
    assert settlement_date is not None
    _write_live(
        db, ctx, rule=rule, ledger_id=ledger_id, dedup_key=source.sync_id, payout=payout,
        amount=amount,
        happened_at=card_rewards.combine_settlement_date_with_source_time(settlement_date, source.happened_at),
        project_id=_resolve_project(db, ledger_id=ledger_id, rule=rule, source_tx=source),
        note=f"信用卡回饋：{rule.label}", period=period, source_tx_id=source.sync_id,
    )


def _recompute_source(db: Session, ctx: _Ctx, target: _SourceTarget, mode: str) -> None:
    payout = _find_payout(db, user_id=target.user_id, rule_sync_id=target.rule_sync_id,
                          dedup_key=target.source_sync_id)
    source = _tx_row(db, ledger_id=target.ledger_id, sync_id=target.source_sync_id)
    rule = ctx.rule(target.user_id, target.rule_sync_id)
    item: card_rewards.QualifyingTx | None = None
    if (
        source is not None
        and _rule_can_pay(rule)
        and rule is not None
        and rule.settlement_type in PER_TX_TYPES
        and target.rule_sync_id in _rule_ids(source.reward_rule_sync_ids_json)
    ):
        items = card_rewards._qualifying_transactions(
            db, ledger_id=target.ledger_id, rule=rule, period_start=None, period_end=None,
            pair_filter="live", only_tx_ids=[source.sync_id],
        )
        item = items[0] if items else None

    if payout is not None and payout.dismissed:
        if source is None or rule is None or target.rule_sync_id not in _rule_ids(source.reward_rule_sync_ids_json):
            db.delete(payout)  # 消費刪了 / 取消勾選:dismissed 標記也沒有意義了
            db.flush()
        return
    row = _reward_row(db, payout)
    if mode == "rule" and _is_frozen(ctx, row):
        return
    if item is None or rule is None or source is None:
        if payout is not None:
            _delete_live(db, ctx, payout)
        return
    settlement_date = card_rewards.compute_settlement_date(rule, tx_happened_at=source.happened_at)
    if settlement_date is None:
        if payout is not None:
            _delete_live(db, ctx, payout)
        return
    if mode == "rule" and row is None and settlement_date <= ctx.today:
        return
    if rule.cap_amount is not None:
        period = ctx.period_for(rule, to_business_date(card_rewards._attribution_date(source)))
        if period is not None:
            _allocate_per_tx_period(
                db, ctx, rule=rule, ledger_id=target.ledger_id, period=period,
                focus={source.sync_id} if mode == "tx" else set(), mode=mode,
            )
            return
    _write_per_tx(db, ctx, rule=rule, ledger_id=target.ledger_id, source=source, payout=payout,
                  amount=_per_tx_amount(rule, item), period=None)


def _allocate_per_tx_period(
    db: Session, ctx: _Ctx, *, rule: ReadCardRewardRuleProjection, ledger_id: str,
    period: tuple[date, date], focus: set[str], mode: str,
) -> None:
    """逐筆模式 + 同期上限:同一期所有新套用消費依日期順序分配剩餘上限(先扣掉
    舊排程在這期已發的)。已入帳的維持原金額(只有它自己的消費改了才重算)。"""
    from .card_reward_payout import _paid_in_period

    assert rule.cap_amount is not None
    items = card_rewards._qualifying_transactions(
        db, ledger_id=ledger_id, rule=rule, period_start=period[0], period_end=period[1],
        pair_filter="live",
    )
    remaining = max(rule.cap_amount - _paid_in_period(
        db, rule=rule, ledger_id=ledger_id, period_start=period[0], period_end=period[1],
        include_live=False,
    ), 0.0)
    for item in items:
        source = item["tx"]
        payout = _find_payout(db, user_id=rule.user_id, rule_sync_id=rule.sync_id, dedup_key=source.sync_id)
        if payout is not None and payout.dismissed:
            continue
        row = _reward_row(db, payout)
        if _is_frozen(ctx, row) and (mode == "rule" or source.sync_id not in focus):
            remaining = max(remaining - (payout.amount if payout is not None else 0.0), 0.0)
            continue
        settlement_date = card_rewards.compute_settlement_date(rule, tx_happened_at=source.happened_at)
        if settlement_date is None:
            continue
        if mode == "rule" and row is None and settlement_date <= ctx.today:
            continue
        amount = max(min(_per_tx_amount(rule, item), remaining), 0.0)
        remaining = max(remaining - amount, 0.0)
        if amount <= 0:
            if payout is not None:
                _delete_live(db, ctx, payout)
            continue
        _write_per_tx(db, ctx, rule=rule, ledger_id=ledger_id, source=source, payout=payout,
                      amount=amount, period=period)


def _recompute_per_tx_period(db: Session, ctx: _Ctx, target: _PerTxPeriodTarget, mode: str) -> None:
    rule = ctx.rule(target.user_id, target.rule_sync_id)
    if not _rule_can_pay(rule) or rule is None or rule.settlement_type not in PER_TX_TYPES:
        return
    if rule.cap_amount is None:
        return
    _allocate_per_tx_period(
        db, ctx, rule=rule, ledger_id=target.ledger_id,
        period=(target.period_start, target.period_end), focus=set(), mode=mode,
    )


# --------------------------------------------------------------------------- #
# 收集受影響的目標                                                              #
# --------------------------------------------------------------------------- #

def _legacy_rules_for_tx(db: Session, *, user_id: str, tx_sync_id: str) -> set[str]:
    return set(db.scalars(
        select(CardRewardLegacyLink.rule_sync_id).where(
            CardRewardLegacyLink.user_id == user_id,
            CardRewardLegacyLink.tx_sync_id == tx_sync_id,
        )
    ).all())


def _sync_legacy_links(db: Session, *, user_id: str, tx_sync_id: str,
                       legacy: set[str], current: set[str], add_new: bool) -> None:
    """取消勾選 / 刪除消費 → 刪掉 legacy 列(之後再勾回來就是新套用)。`add_new`
    只有即時機制關閉時為 True:那段期間新勾的配對都由舊排程處理,記成舊套用。"""
    removed = legacy - current
    if removed:
        db.execute(delete(CardRewardLegacyLink).where(
            CardRewardLegacyLink.user_id == user_id,
            CardRewardLegacyLink.tx_sync_id == tx_sync_id,
            CardRewardLegacyLink.rule_sync_id.in_(removed),
        ))
    if add_new:
        for rule_id in sorted(current - legacy):
            db.add(CardRewardLegacyLink(user_id=user_id, tx_sync_id=tx_sync_id, rule_sync_id=rule_id))
        db.flush()


def _add_targets_for_snapshot(ctx: _Ctx, *, ledger_id: str, sync_id: str, snap: dict[str, Any],
                              legacy: set[str], mode: str) -> None:
    if snap.get("tx_type") != "expense":
        return
    user_id = snap["user_id"]
    day = _attribution_business_date(snap)
    for rule_id in _rule_ids(snap.get("reward_rule_sync_ids_json")):
        rule = ctx.rule(user_id, rule_id)
        if rule is None or rule.account_sync_id != snap.get("account_sync_id"):
            continue
        period = ctx.period_for(rule, day)
        if rule.settlement_type == "period_end":
            if period is not None:
                ctx.add(_PeriodTarget(user_id, rule_id, ledger_id, period[0], period[1]), mode)
                _add_cap_siblings(ctx, rule=rule, ledger_id=ledger_id, period=period, mode=mode)
        elif rule.settlement_type in PER_TX_TYPES:
            if rule_id not in legacy:
                ctx.add(_SourceTarget(user_id, rule_id, ledger_id, sync_id), mode)
            if rule.cap_amount is not None and period is not None:
                ctx.add(_PerTxPeriodTarget(user_id, rule_id, ledger_id, period[0], period[1]), mode)


def _add_cap_siblings(ctx: _Ctx, *, rule: ReadCardRewardRuleProjection, ledger_id: str,
                      period: tuple[date, date], mode: str) -> None:
    """共用上限群組整組一起重算同一期。"""
    if not rule.cap_shared_key:
        return
    for member in card_rewards.fetch_cap_group_rules(ctx.db, user_id=rule.user_id, base_rules=[rule]):
        if member.sync_id == rule.sync_id or member.settlement_type != "period_end":
            continue
        if ctx.period_for(member, period[1]) == period:
            ctx.add(_PeriodTarget(rule.user_id, member.sync_id, ledger_id, period[0], period[1]), mode)


def _collect_tx(db: Session, ctx: _Ctx, *, ledger_id: str, sync_id: str,
                before: dict[str, Any] | None, depth: int = 0) -> None:
    after_row = _tx_row(db, ledger_id=ledger_id, sync_id=sync_id)
    after = card_reward_dirty.tx_snapshot(after_row)
    snap = after or before
    if snap is None:
        return
    user_id = snap["user_id"]

    # 使用者刪掉即時回饋交易 → dismissed,之後不再重建。
    if before is not None and after is None and before.get("reward_payout_key"):
        payout = _find_payout(db, user_id=user_id, rule_sync_id=str(before.get("reward_payout_rule_sync_id")),
                              dedup_key=str(before["reward_payout_key"]))
        if payout is not None and payout.payout_tx_sync_id == sync_id:
            payout.dismissed = True
            payout.payout_tx_sync_id = None
            db.flush()

    before_rules = set(_rule_ids(before.get("reward_rule_sync_ids_json"))) if before else set()
    after_rules = set(_rule_ids(after.get("reward_rule_sync_ids_json"))) if after else set()
    if before_rules or after_rules:
        legacy = _legacy_rules_for_tx(db, user_id=user_id, tx_sync_id=sync_id)
        enabled = card_rewards.live_enabled()
        current = after_rules if (after is not None and after.get("tx_type") == "expense") else set()
        _sync_legacy_links(db, user_id=user_id, tx_sync_id=sync_id, legacy=legacy,
                           current=current, add_new=not enabled)
        if enabled:
            for s in (before, after):
                if s is not None:
                    _add_targets_for_snapshot(ctx, ledger_id=ledger_id, sync_id=sync_id, snap=s,
                                              legacy=legacy, mode="tx")

    # 退款:被退款的那筆消費淨額變了,重算它的回饋。
    if depth == 0 and card_rewards.live_enabled():
        refunded = {s.get("refund_of_sync_id") for s in (before, after) if s is not None}
        for refunded_id in refunded - {None}:
            target_row = _tx_row(db, ledger_id=ledger_id, sync_id=str(refunded_id))
            if target_row is not None:
                _collect_tx(db, ctx, ledger_id=ledger_id, sync_id=target_row.sync_id,
                            before=card_reward_dirty.tx_snapshot(target_row), depth=1)


def _rule_cutoff(rule: ReadCardRewardRuleProjection, today: date) -> datetime:
    """規則觸發的重算只看「還可能沒入帳」的消費範圍,不掃整段歷史。"""
    if rule.settlement_type in PER_TX_TYPES:
        days = (rule.settlement_days or 0) + 3
    else:
        days = ((rule.settlement_month_offset or 0) + 3) * 31
        if rule.interval == "custom_range" and rule.starts_at is not None:
            return min(business_date_start_utc(today - timedelta(days=days)),
                       business_date_start_utc(rule.starts_at.date()))
    return business_date_start_utc(today - timedelta(days=days))


def _collect_rule(db: Session, ctx: _Ctx, *, user_id: str, rule_sync_id: str) -> None:
    for payout in db.scalars(
        select(CardRewardPayout).where(
            CardRewardPayout.user_id == user_id,
            CardRewardPayout.rule_sync_id == rule_sync_id,
            CardRewardPayout.live.is_(True),
            CardRewardPayout.dismissed.is_(False),
        )
    ).all():
        if not payout.ledger_id:
            continue
        if payout.dedup_key.startswith("live:") and payout.period_start and payout.period_end:
            ctx.add(_PeriodTarget(user_id, rule_sync_id, payout.ledger_id,
                                  payout.period_start, payout.period_end), "rule")
        else:
            ctx.add(_SourceTarget(user_id, rule_sync_id, payout.ledger_id, payout.dedup_key), "rule")

    rule = ctx.rule(user_id, rule_sync_id)
    if not _rule_can_pay(rule) or rule is None:
        return
    legacy = card_rewards.legacy_tx_ids(db, user_id=user_id, rule_sync_id=rule_sync_id)
    rows = db.scalars(
        select(ReadTxProjection).where(
            ReadTxProjection.user_id == user_id,
            ReadTxProjection.account_sync_id == rule.account_sync_id,
            ReadTxProjection.tx_type == "expense",
            ReadTxProjection.reward_rule_sync_ids_json.like(f'%"{rule_sync_id}"%'),
            ReadTxProjection.happened_at >= _rule_cutoff(rule, ctx.today),
        )
    ).all()
    for row in rows:
        if row.sync_id in legacy and rule.settlement_type in PER_TX_TYPES:
            continue
        snap = card_reward_dirty.tx_snapshot(row)
        assert snap is not None
        snap["reward_rule_sync_ids_json"] = json.dumps([rule_sync_id])
        _add_targets_for_snapshot(ctx, ledger_id=row.ledger_id, sync_id=row.sync_id, snap=snap,
                                  legacy=legacy, mode="rule")


def _run_targets(db: Session, ctx: _Ctx) -> None:
    # 逐筆的來源先跑(會建立/刪除自己的 payout),再跑上限分配,最後整期。
    order = {_SourceTarget: 0, _PerTxPeriodTarget: 1, _PeriodTarget: 2}
    for target, mode in sorted(ctx.targets.items(), key=lambda kv: order[type(kv[0])]):
        if isinstance(target, _SourceTarget):
            _recompute_source(db, ctx, target, mode)
        elif isinstance(target, _PerTxPeriodTarget):
            _recompute_per_tx_period(db, ctx, target, mode)
        else:
            assert isinstance(target, _PeriodTarget)
            _recompute_period(db, ctx, target, mode)


# --------------------------------------------------------------------------- #
# 入口                                                                          #
# --------------------------------------------------------------------------- #

def process_pending(db: Session, *, now: datetime | None = None) -> None:
    """處理這個 session 累積的待重算標記(commit 前由 listener 呼叫)。"""
    now = now or datetime.now(timezone.utc)
    for _ in range(3):  # 重算自己寫的交易不會再留標記;保險起見最多跑三輪
        if not card_reward_dirty.has_pending(db):
            return
        touched, rules = card_reward_dirty.pop_all(db)
        ctx = _Ctx(db, now)
        with card_reward_dirty.suppress(db):
            for (ledger_id, sync_id), before in touched.items():
                _collect_tx(db, ctx, ledger_id=ledger_id, sync_id=sync_id, before=before)
            if card_rewards.live_enabled():
                for user_id, rule_sync_id in rules:
                    _collect_rule(db, ctx, user_id=user_id, rule_sync_id=rule_sync_id)
                _run_targets(db, ctx)
        if ctx.touched_ledgers:
            db.info.setdefault(_TOUCHED_LEDGERS_KEY, set()).update(ctx.touched_ledgers)


def pop_touched_ledgers(db: Session) -> set[str]:
    """這個 session 上次 commit 時即時機制寫過回饋交易的帳本(內部 id),給 router
    在 commit 後補 WS 廣播用。"""
    return db.info.pop(_TOUCHED_LEDGERS_KEY, set())


def recompute_rule_now(db: Session, *, user_id: str, rule_sync_id: str, now: datetime | None = None) -> set[str]:
    """排程/腳本用:立即重算一條規則(規則觸發語意,只動未入帳的)。不 commit。"""
    if not card_rewards.live_enabled():
        return set()
    ctx = _Ctx(db, now or datetime.now(timezone.utc))
    with card_reward_dirty.suppress(db):
        _collect_rule(db, ctx, user_id=user_id, rule_sync_id=rule_sync_id)
        _run_targets(db, ctx)
    return ctx.touched_ledgers


def reconcile_live_card_rewards(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """`card_reward_live_reconcile` 排程(每小時):①所有可自動入帳的規則(以及還掛著
    即時回饋的規則)跑一次規則觸發的重算,補上寫入當下漏掉的(例如帳單日改了導致期別
    位移);②整期回饋交易剛進入「已入帳」時發 `card_reward` 通知(沿用舊排程的通知
    行為;逐筆模式維持不通知)。不 commit。"""
    now = now or datetime.now(timezone.utc)
    if not card_rewards.live_enabled():
        return {"rules": 0, "notified": 0}
    keys = {
        (r.user_id, r.sync_id) for r in db.scalars(
            select(ReadCardRewardRuleProjection).where(
                ReadCardRewardRuleProjection.settlement_type != "manual",
                ReadCardRewardRuleProjection.reward_account_id.is_not(None),
                ReadCardRewardRuleProjection.enabled.is_(True),
            )
        ).all()
    }
    keys |= set(db.execute(
        select(CardRewardPayout.user_id, CardRewardPayout.rule_sync_id).where(
            CardRewardPayout.live.is_(True), CardRewardPayout.dismissed.is_(False),
        ).distinct()
    ).all())
    for user_id, rule_sync_id in sorted(keys):
        recompute_rule_now(db, user_id=user_id, rule_sync_id=rule_sync_id, now=now)
    return {"rules": len(keys), "notified": _notify_posted_periods(db, now=now)}


def _notify_posted_periods(db: Session, *, now: datetime) -> int:
    today = business_today(now)
    count = 0
    for payout in db.scalars(
        select(CardRewardPayout).where(
            CardRewardPayout.live.is_(True),
            CardRewardPayout.dismissed.is_(False),
            CardRewardPayout.notified_at.is_(None),
            CardRewardPayout.period_start.is_not(None),
            CardRewardPayout.payout_tx_sync_id.is_not(None),
        )
    ).all():
        if not payout.dedup_key.startswith("live:"):
            continue
        row = _reward_row(db, payout)
        if row is None or to_business_date(row.happened_at) > today:
            continue
        rule = db.scalar(
            select(ReadCardRewardRuleProjection).where(
                ReadCardRewardRuleProjection.user_id == payout.user_id,
                ReadCardRewardRuleProjection.sync_id == payout.rule_sync_id,
            )
        )
        label = rule.label if rule is not None else payout.rule_sync_id
        notification_service.create_notification(
            db,
            user_id=payout.user_id,
            category="card_reward",
            title=f"信用卡回饋入帳：{label}",
            body=f"本期回饋 {row.amount:.2f} 已存入{row.account_name or '指定帳戶'}。",
            payload={
                "ruleId": payout.rule_sync_id,
                "accountId": rule.account_sync_id if rule is not None else None,
                "periodEnd": payout.period_end.isoformat() if payout.period_end else None,
                "ledgerId": notification_service.resolve_ledger_external_id(db, row.ledger_id),
                "txId": row.sync_id,
            },
        )
        payout.notified_at = now
        count += 1
    db.flush()
    return count


def snapshot_legacy_links(db: Session) -> int:
    """把目前所有勾選配對補進 legacy 表(冪等)。alembic 0076 做過一次;即時機制關閉
    一段時間後要再打開時,關閉期間新勾的配對已經由 `_sync_legacy_links(add_new=True)`
    補進去,通常不需要再跑,留給維運腳本救急。回傳新增幾列。"""
    existing = set(db.execute(
        select(CardRewardLegacyLink.user_id, CardRewardLegacyLink.tx_sync_id, CardRewardLegacyLink.rule_sync_id)
    ).all())
    added = 0
    for user_id, sync_id, raw in db.execute(
        select(ReadTxProjection.user_id, ReadTxProjection.sync_id, ReadTxProjection.reward_rule_sync_ids_json)
        .where(ReadTxProjection.reward_rule_sync_ids_json.is_not(None))
    ).all():
        for rule_id in _rule_ids(raw):
            key = (user_id, sync_id, rule_id)
            if key in existing:
                continue
            existing.add(key)
            db.add(CardRewardLegacyLink(user_id=user_id, tx_sync_id=sync_id, rule_sync_id=rule_id))
            added += 1
    db.flush()
    return added


# --------------------------------------------------------------------------- #
# Session hooks                                                                 #
# --------------------------------------------------------------------------- #

@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    if not card_reward_dirty.has_pending(session):
        return
    try:
        process_pending(session)
    except Exception:  # noqa: BLE001
        # 重算失敗不能擋使用者的寫入:記 log,每小時的保險重算會補上。測試環境直接拋,
        # 不讓 bug 被吞掉。
        card_reward_dirty.clear(session)
        logger.exception("card_reward_live: recompute failed; reconcile job will retry")
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    card_reward_dirty.clear(session)
    session.info.pop(_TOUCHED_LEDGERS_KEY, None)


async def broadcast_touched_ledgers(db: Session, *, ws_manager: Any, exclude_ledger_ids: set[str]) -> None:
    """commit 後:即時機制寫進「這次請求本來沒碰到」的帳本(例如改規則、或卡片消費在
    另一本帳)時補一則 `sync_change`,讓其它裝置去拉。"""
    from sqlalchemy import func

    from ..websocket_manager import broadcast_to_ledger

    for ledger_id in sorted(pop_touched_ledgers(db) - exclude_ledger_ids):
        external_id = db.scalar(select(Ledger.external_id).where(Ledger.id == ledger_id))
        if external_id is None:
            continue
        cursor = db.scalar(select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)) or 0
        await broadcast_to_ledger(
            db=db,
            ws_manager=ws_manager,
            ledger_id=ledger_id,
            payload={
                "type": "sync_change",
                "ledgerId": external_id,
                "serverCursor": cursor,
                "serverTimestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
