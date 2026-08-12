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

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import projection
from ..models import CardRewardPayout, ReadCardRewardRuleProjection, ReadTxProjection, SyncChange, UserAccountProjection
from . import card_rewards
from . import notifications as notification_service
from .recurring_materializer import emit_tx, new_sync_id

logger = logging.getLogger(__name__)

_REWARD_PAYOUT_EDIT_DEVICE_ID = "server-card-reward-payout"


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
    db: Session, *, ledger_id: str, user_id: str, now: datetime, happened_at: datetime,
    reward_account_id: str, amount: float, note: str,
    source_tx_id: str | None = None,
) -> str:
    """§2.9.5.4 補強(2026-08-04 使用者反饋):①自動帶入固定的「回饋金」
    income 分類(`card_rewards.ensure_reward_category`,找不到就建一個,
    user-global 只會建一次),不然交易列表顯示空白分類很奇怪;②
    `source_tx_id` 非空時(逐筆結算才有單一對應的原始消費)寫入
    `rewardSourceTxId`,web 交易詳情弹窗會渲染一個可點擊的「關聯消費」連結
    跳去看那筆原始消費(取代舊版把 tx sync_id 原樣寫進備註文字裡的做法)。
    `happened_at` 是這筆回饋交易的業務日期(依規則算出的入帳日/期末日),
    跟 `now`(排程實際跑的時間,只用來寫 SyncChange.updated_at)刻意分開
    ——不然補記/回溯交易時,回饋交易的日期會被排程實際執行的時間點污染,
    而不是反映規則設定的入帳時機(2026-08-04 使用者回報的 bug)。"""
    to_name = _account_name(db, user_id=user_id, sync_id=reward_account_id)
    category_id = card_rewards.ensure_reward_category(db, user_id=user_id)
    item: dict[str, object] = {
        "syncId": new_sync_id("tx"),
        "type": "income",
        "amount": amount,
        "happenedAt": happened_at.isoformat(),
        "note": note,
        "accountId": reward_account_id,
        "accountName": to_name,
        "categoryId": category_id,
        "categoryName": card_rewards.REWARD_CATEGORY_NAME,
        "categoryKind": "income",
        "createdByUserId": user_id,
        "updatedByUserId": user_id,
    }
    if source_tx_id is not None:
        item["rewardSourceTxId"] = source_tx_id
    return emit_tx(db, ledger_id=ledger_id, user_id=user_id, now=now, item=item)


def reverse_card_reward_payouts_for_refund(
    db: Session, *, ledger_id: str, user_id: str, refunded_tx_id: str, now: datetime,
) -> list[str]:
    """退款沖銷已入帳的信用卡回饋(2026-08-04 使用者反饋):`refunded_tx_id`
    這筆消費一旦被退款,它已經逐筆結算(`immediate_after_tx`/`after_posting_
    date`)入帳過的回饋金要跟著沖銷回去——不然使用者退了貨還留著那筆「憑空」
    多出來的回饋收入。呼叫點是 `routers/write/_shared.py` 建立退款交易的
    同一個 DB transaction(`_commit_create_tx_fast`),原子性地跟退款交易
    一起 commit。

    只找得到 `CardRewardPayout.dedup_key == refunded_tx_id` 的紀錄(逐筆
    結算,dedup_key 就是消費本身的 sync_id)——`period_end`(整期結算)的
    dedup_key 是「期末日期」字串,回饋金額是整期彙總算出來的,沒有跟單一
    消費綁定的紀錄可查,無法精準沖銷「這一筆消費佔了多少」,是已知限制
    (v1 不處理,同這個 codebase 其它「已知限制」慣例,不在這裡另外強行
    重算整期分攤)。

    `card_rewards._qualifying_transactions` 已經把「被退款的交易」整個排除
    在未來的結算掃描之外(見該函式 docstring),所以「退款發生在排程跑之前」
    的情況天然不會被排入、不需要這裡處理;這個函式只處理「排程已經跑過,
    回饋已經入帳」的情況——兩條路徑合起來,退款前/退款後結算的淨效果一致
    (這筆消費的回饋淨額最終都是 0)。

    沖銷交易本身是一筆 `expense`,金額/入帳帳戶取自當初實際入帳的那筆回饋
    交易(`payout.payout_tx_sync_id`,而不是重新计算——實際落袋的錢是唯一
    權威來源),分類用自建的「退款」分類(`ensure_refund_category`,expense
    kind)。回傳新建的沖銷交易 sync_id 列表(通常 0~規則勾選數量那麼多筆)。
    """
    refunded_tx = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == refunded_tx_id,
        )
    )
    if refunded_tx is None or not refunded_tx.reward_rule_sync_ids_json:
        return []
    try:
        rule_ids = json.loads(refunded_tx.reward_rule_sync_ids_json)
    except (TypeError, ValueError):
        rule_ids = []
    if not isinstance(rule_ids, list) or not rule_ids:
        return []

    created: list[str] = []
    for rule_id in rule_ids:
        rule_sync_id = str(rule_id)
        payout = db.scalar(
            select(CardRewardPayout).where(
                CardRewardPayout.user_id == user_id,
                CardRewardPayout.rule_sync_id == rule_sync_id,
                CardRewardPayout.dedup_key == refunded_tx_id,
            )
        )
        if payout is None or payout.payout_tx_sync_id is None or payout.amount <= 0:
            continue  # 還沒結算 / 結算成 0 元,沒有錢要沖銷

        reward_tx = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.sync_id == payout.payout_tx_sync_id,
            )
        )
        if reward_tx is None:
            continue  # 回饋交易本身被删了(理论上不该发生),没有帐户/金额可沖銷

        rule = db.scalar(
            select(ReadCardRewardRuleProjection).where(
                ReadCardRewardRuleProjection.user_id == user_id,
                ReadCardRewardRuleProjection.sync_id == rule_sync_id,
            )
        )
        rule_label = rule.label if rule is not None else rule_sync_id

        category_id = card_rewards.ensure_refund_category(db, user_id=user_id, kind="expense")
        item: dict[str, object] = {
            "syncId": new_sync_id("tx"),
            "type": "expense",
            "amount": reward_tx.amount,
            "happenedAt": now.isoformat(),
            "note": f"退款沖銷回饋金：{rule_label}",
            "accountId": reward_tx.account_sync_id,
            "accountName": reward_tx.account_name,
            "categoryId": category_id,
            "categoryName": card_rewards.REFUND_CATEGORY_NAME,
            "categoryKind": "expense",
            "rewardSourceTxId": refunded_tx_id,
            "createdByUserId": user_id,
            "updatedByUserId": user_id,
        }
        created.append(emit_tx(db, ledger_id=ledger_id, user_id=user_id, now=now, item=item))
    return created


def reverse_card_reward_payouts_for_edit(
    db: Session, *, ledger_id: str, user_id: str, edited_tx_id: str, now: datetime,
) -> list[str]:
    """交易編輯後沖銷已入帳回饋(§2.9.5.4 補強,Phase 8 #5,2026-08 使用者
    反饋):`CardRewardPayout.dedup_key` 是交易的 sync_id 且不會變,一旦這筆
    交易的回饋已經逐筆結算入帳(`immediate_after_tx`/`after_posting_date`),
    事後修改 `happened_at`/`amount`/`category_id`/`account_id` 這幾個會影響
    回饋計算的欄位,原本已入帳的回饋金就跟修改後的交易脫鉤了(拿舊欄位值
    算出來的金額/日期)。

    比照使用者確認的方案:直接刪除舊的回饋交易 + 去重記錄(而不是新增一筆
    反向沖正交易——歷史包袱最小),下一輪排程(`materialize_due_card_reward_
    payouts`)掃到這筆交易時,`_already_paid_keys` 找不到它的 dedup_key,會
    用新欄位值重新算一次補發正確金額。

    呼叫點是 `routers/write/_shared.py` 更新交易的路徑,只有在合併後
    happened_at/amount/category_id/account_id 任一實際變動時才呼叫(note/
    商店等欄位編輯不觸發),原子性地跟這次交易更新一起 commit。

    只處理逐筆結算(dedup_key == tx sync_id)——`period_end`(整期結算)沒有
    單一對應紀錄可查,是已知限制(同 `reverse_card_reward_payouts_for_refund`
    docstring 的既有限制,不在這裡額外處理)。

    不會遞迴觸發沖銷:這裡刪除回饋交易走的是 `projection.delete_tx` +
    `SyncChange` 直寫,不經過 `routers/write/_shared.py` 的更新分支;而且
    `dedup_key` 存的是「來源消費交易」的 sync_id,不是回饋交易自己的
    sync_id,所以就算使用者之後去編輯這筆回饋交易本身,也不會命中這裡的
    查詢再次觸發沖銷。"""
    payouts = db.scalars(
        select(CardRewardPayout).where(
            CardRewardPayout.user_id == user_id,
            CardRewardPayout.dedup_key == edited_tx_id,
        )
    ).all()
    if not payouts:
        return []

    deleted: list[str] = []
    for payout in payouts:
        if payout.payout_tx_sync_id is not None:
            reward_tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_id,
                    ReadTxProjection.sync_id == payout.payout_tx_sync_id,
                )
            )
            if reward_tx is not None:
                change_row = SyncChange(
                    user_id=user_id,
                    ledger_id=ledger_id,
                    scope="ledger",
                    entity_type="transaction",
                    entity_sync_id=reward_tx.sync_id,
                    action="delete",
                    payload_json={},
                    updated_at=now,
                    updated_by_device_id=_REWARD_PAYOUT_EDIT_DEVICE_ID,
                    updated_by_user_id=user_id,
                )
                db.add(change_row)
                db.flush()
                projection.delete_tx(db, ledger_id=ledger_id, sync_id=reward_tx.sync_id)
                deleted.append(reward_tx.sync_id)
        db.delete(payout)
    return deleted


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

        # Phase 8 #4 補漏(2026-08 使用者反饋:選了「總額四捨五入」實際入帳
        # 還是有小數):逐筆結算沒有「多筆加總」的階段,`compute_account_
        # card_rewards` 的 total_rounding 只套用在預覽/彙總畫面,實際入帳
        # 這裡完全沒有套用過。逐筆結算下每一筆payout本身就是它自己的
        # 「最終總額」,所以在真正落袋(emit)前一樣要依 rule.total_rounding
        # 取整一次,讓預覽跟實際入帳金額對得上。
        reward_amount = card_rewards._round_amount(
            item["reward_amount"], rule.total_rounding, to_integer=True,
        )
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
                happened_at=card_rewards.combine_settlement_date_with_source_time(
                    settlement_date, tx.happened_at,
                ),
                reward_account_id=rule.reward_account_id, amount=reward_amount,
                note=f"信用卡回饋入帳：{rule.label}",
                source_tx_id=tx.sync_id,
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
    """結算「已結束但還沒入帳」的週期。原本只看`period_offset=-1`(剛結束
    的那一期),比照 `credit_card_autopay`/`credit_card_reminders` 既有的
    「只看最近一期,長時間離線=錯過一次自動化」限制。但這個假設在加入
    `settlement_month_offset`(延後 N 個月才入帳,見 `card_rewards.
    compute_settlement_date`)之後不成立了:`period_offset=-1` 是相對『當下
    』算的,設定「次二月28日」入帳時,等真的到了入帳日,`period_offset=-1`
    早就滑到更新的週期,原本那期永遠對不上、永遠不會被結算
    (2026-08-12 使用者反饋:規則設定次二月28日入帳,7月消費卻在7/31當天
    ——也就是週期一結束——就直接入帳了)。修法:往回多看
    `settlement_month_offset` 期,逐一檢查有沒有「已到入帳日但還沒記去重」
    的舊週期,一次 tick 內可以連續補齊多期(離線很久時不用等好幾個 tick 才
    追上)。`settlement_month_offset` 為 `None`(維持現況『期間結束當天入帳
    』)時只看 -1,跟修正前行為完全一致。"""
    already_paid = _already_paid_keys(db, user_id=rule.user_id, rule_sync_id=rule.sync_id)
    group_rules = card_rewards.fetch_cap_group_rules(db, user_id=rule.user_id, base_rules=[rule])
    lookback = (rule.settlement_month_offset or 0) + 1
    paid_any = False

    for period_offset in range(-1, -lookback - 1, -1):
        results = card_rewards.compute_account_card_rewards(
            db, ledger_id=ledger_id, account=account, rules=group_rules, now=now,
            period_offset=period_offset,
        )
        card_rewards.apply_caps(results)
        this_result = next((r for r in results if r["rule"].sync_id == rule.sync_id), None)
        if this_result is None or this_result["status"] != "ok":
            continue  # 排程/資料還沒修好,留到下次重試,不記去重

        period_end = this_result["period_end"]
        dedup_key = period_end.isoformat()
        if dedup_key in already_paid:
            continue  # 這期已經結算過,看更早一期有沒有還沒入帳的

        settlement_date = card_rewards.compute_settlement_date(rule, period_end=period_end)
        if settlement_date is None or now.date() < settlement_date:
            continue  # 這期還沒到規則設定的入帳日,留到下次 tick 重試

        reward_amount = this_result["capped_reward"]
        if reward_amount <= 0:
            # 不記去重:跟逐筆結算(per-tx dedup_key = 交易自己的 sync_id)不同,
            # 這裡的 dedup_key 是整期共用的日期字串——如果在使用者於這期結束後
            # 才補記/回溯一筆合格交易之前,剛好有一次 tick 先以 0 元跑過這期,
            # 提前記下去重會讓這期永遠卡在 0,之後補的交易再也不會被結算。留到
            # 下次 tick 重新算。
            continue
        assert rule.reward_account_id is not None
        payout_tx_sync_id = _emit_reward_tx(
            db, ledger_id=ledger_id, user_id=rule.user_id, now=now,
            happened_at=card_rewards._date_to_utc_dt(settlement_date),
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
        paid_any = True

    return paid_any


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
