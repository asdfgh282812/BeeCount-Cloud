"""信用卡群組合併帳單的 DB 聚合計算(§2.9 Phase 4,2026-08-02 群組模型改版)。

`src/services/credit_card.py` 只做無 DB 依賴的帳單週期日期算術;這裡是
"查 `read_tx_projection` 把金額加總起來"的那一半,被三個呼叫點共用:
`routers/read/ledgers.py::get_account_billing_summary`(讀端點)、
`routers/write/accounts.py::card_payment_ep`(繳款分攤要知道每個子帳戶
自己的應繳金額)、`services/credit_card_reminders.py`(到期提醒要知道
總應繳金額才能寫進提醒文案)。三處各自重算一份的話,任何一處修 bug 都要
記得改三次,故意抽成一個函式集中維護。

核心語意見 `compute_group_billing` docstring。
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timezone
from typing import TypedDict

from sqlalchemy import case as sa_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    ReadInstallmentPeriodProjection,
    ReadInstallmentPlanProjection,
    ReadTxProjection,
    UserAccountProjection,
)
from . import credit_card
from .deferred_posting import attribution_date_expr

# 延後入帳(§2.10 Phase 5):信用卡帳單週期窗口按「入帳日」歸屬,不是單純
# 消費日 —— 這是延後入帳這個功能對信用卡場景最主要的使用情境(店家批次
# 請款延遲)。`deferred_posting_at` 對既有資料一律是 NULL,COALESCE 落回
# `happened_at`,對舊資料零行為變化。
_ATTR_DATE = attribution_date_expr()


def compute_offset_totals(
    db: Session, *, ledger_id: str, member_ids: Sequence[str],
) -> dict[str, float]:
    """帳單分期沖銷(§2.3,2026-08-02 第三輪改版)累計金額 —— 純虛擬記帳
    調整,故意**不**落地為任何 `read_tx_projection` 交易(2026-08-02 使用者
    反饋:沖銷款不該出現在交易明細裡),存在
    `read_installment_plan_projection.offset_breakdown_json`(`{child_
    account_sync_id: amount}` 的 JSON,建立分期計畫時算好直接寫入)。刪除
    整個分期計畫時這一行連帶被刪,沖銷自動失效,帳單「變回原本尚未繳費的
    狀態」(2026-08-02 使用者反饋 #3),不需要另外清理任何交易記錄。

    這裡不分時間視窗、對所有 cutoff 一律扣掉同一個總額(呼叫端
    `compute_group_billing`/`compute_cycle_period_billing` 都是"跑動餘額"
    語意,沖銷代表"從此以後這筆帳從卡片上移除",對建立分期計畫之後的任何
    cutoff 都成立;唯一的已知近似是瀏覽**早於**分期計畫建立時間的歷史週期
    時,理論上不該被這筆沖銷影響,但這裡沒有按時間精細判斷 —— 這種回顧
    比分期計畫本身更早的歷史週期是邊緣情境,不值得為此增加複雜度)。"""
    if not member_ids:
        return {}
    member_set = set(member_ids)
    rows = db.scalars(
        select(ReadInstallmentPlanProjection.offset_breakdown_json).where(
            ReadInstallmentPlanProjection.ledger_id == ledger_id,
            ReadInstallmentPlanProjection.offset_breakdown_json.isnot(None),
        )
    ).all()
    totals: dict[str, float] = {}
    for raw in rows:
        if not raw:
            continue
        try:
            breakdown = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(breakdown, dict):
            continue
        for acc_id, amount in breakdown.items():
            if acc_id not in member_set:
                continue
            try:
                totals[acc_id] = totals.get(acc_id, 0.0) + float(amount)
            except (TypeError, ValueError):
                continue
    return totals


def date_to_utc_dt(d: date, *, end_of_day: bool = False) -> datetime:
    """把週期邊界 `date` 轉成查詢用的 UTC datetime 邊界。公開(2026-08-09,
    §2.10 對帳模式改版)給 `read/ledgers.py::get_account_statement`/
    `write/accounts.py::clear_statement_confirmations_ep` 共用同一份邊界
    計算,不重複實作。"""
    if end_of_day:
        return datetime(d.year, d.month, d.day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


class GroupBilling(TypedDict):
    cycle_start: date
    cycle_end: date
    due_date: date
    open_cycle_start: date
    open_cycle_end: date
    open_cycle_due_date: date
    statement_amount: float
    remaining_due: float
    # 2026-08-03 使用者反饋 #2:轉成分期不算「已繳」,信用卡額度不該因此
    # 恢復,要繼續往下扣——`credit_used` 是不扣掉分期沖銷的終身消費淨額,
    # 專門給 available_credit 用;`remaining_due`(當期應繳/不能重複轉分期
    # 的判斷)維持扣掉沖銷後的數字不變。
    credit_used: float
    paid_amount: float
    open_cycle_spend: float
    per_child_cycle_spend: dict[str, float]
    per_child_remaining_due: dict[str, float]


def is_billing_root(account: UserAccountProjection) -> bool:
    """一張帳戶能不能當「合併帳單」的查詢根(billing-summary/card-payment/
    interest-free-suggestion 的 `account_id`):要嘛是 `account_group`(真正
    的群組,有子帳戶才有意義),要嘛是**沒有掛靠任何群組**的獨立信用卡
    ——2026-08-02 使用者反馈:只辦一張卡、沒有建群組的人,不該因此就少了
    繳費/分期/帳單週期這些功能,單卡應該能做群組能做的所有事,把自己當
    成「只有自己一個成員的群組」。已經掛靠某個群組的子卡不算——它的帳單
    要透過群組查,不能繞過去單獨查自己(額度/結帳日這些設定都在群組上,
    子卡自己没有)。"""
    if account.account_type == "account_group":
        return True
    return account.account_type == "credit_card" and not account.parent_account_id


def resolve_billing_children(
    db: Session, *, account: UserAccountProjection,
) -> Sequence[UserAccountProjection]:
    """`account` 必須先通过 `is_billing_root` 检查。`account_group` 回傳掛靠
    在它底下的子帳戶清單;獨立信用卡回傳只包含它自己的單元素清單(自己
    既是「群組」也是唯一「成員」)。"""
    if account.account_type == "account_group":
        return db.scalars(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == account.user_id,
                UserAccountProjection.parent_account_id == account.sync_id,
            )
        ).all()
    return [account]


def billing_member_ids(
    group: UserAccountProjection, children: Sequence[UserAccountProjection],
) -> list[str]:
    """帳單聚合查詢要涵蓋的帳戶 sync_id 集合。§2.3 補強(2026-08-03 使用者
    反饋):帳單分期在主帳戶上建立時,各期分期交易現在直接掛在 group 自己
    身上(不再任選一張子卡,見 `write/installment_plans.py::
    create_installment_plan_ep`),所以 group.sync_id 自己也要算進查詢範圍,
    否則群組自己持有的這些真實交易永遠不會被算進「應繳」。獨立信用卡場景
    下(`group is children[0]`)dedup 後跟原本行為一致。"""
    ids = {c.sync_id for c in children}
    ids.add(group.sync_id)
    return list(ids)


def compute_group_billing(
    db: Session,
    *,
    ledger_id: str,
    group: UserAccountProjection,
    children: Sequence[UserAccountProjection],
    now: datetime,
) -> GroupBilling:
    """`group` 必須是 `account_type == "account_group"`,或是一張沒有掛靠
    任何群組的獨立信用卡(見 `is_billing_root`/`resolve_billing_children`
    —— 後者场景下 `group is children[0]`,自己既是查詢根也是唯一成員)。
    呼叫方必須已確認 `billing_day`/`payment_due_day` 已設定,這裡不重複
    校驗。`children` 是 `resolve_billing_children` 的結果。

    `remaining_due`(整組應繳)用「終身跑動餘額」計算:子帳戶終身消費
    (截至本期結帳日為止)減掉子帳戶+群組自己終身收到的還款轉帳(不分期別
    窗口)—— 任何一期的溢繳都會自動結轉到未來各期,不會在下一期結帳日一過
    就從計算窗口裡消失。`statement_amount`/`per_child_cycle_spend` 是純本期
    窗口的資訊性數字,不影響 `remaining_due`。`per_child_remaining_due`
    是card-payment 分攤要用的每個子帳戶各自應繳金額(下限 0,溢繳不會讓
    某個子帳戶顯示負數,超出的部分算整組層級的溢繳)。"""
    billing_day = group.billing_day
    payment_due_day = group.payment_due_day
    assert billing_day is not None and payment_due_day is not None

    member_ids = billing_member_ids(group, children)

    cycle_start, cycle_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
    due_date = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
    open_start, open_end = credit_card.billing_cycle_containing(now.date(), billing_day)
    open_due = credit_card.due_date_for_cycle_end(open_end, payment_due_day)

    # 下界用結帳日「當天結束」當排除點:結帳日整天都算進「已結束的上一期」,
    # 不能被誤判進這一期。上界同理用 cycle_end 當天結束當 inclusive 上界。
    cycle_start_query_dt = date_to_utc_dt(cycle_start, end_of_day=True)
    cycle_end_query_dt = date_to_utc_dt(cycle_end, end_of_day=True)

    per_child_cycle_spend: dict[str, float] = {}
    per_child_lifetime_charged: dict[str, float] = {}
    per_child_lifetime_paid: dict[str, float] = {}

    if member_ids:
        spend_rows = db.execute(
            select(
                ReadTxProjection.account_sync_id,
                func.coalesce(func.sum(sa_case(
                    (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                    (ReadTxProjection.tx_type == "income", -ReadTxProjection.amount),
                    else_=0.0,
                )), 0.0),
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type.in_(["expense", "income"]),
                _ATTR_DATE > cycle_start_query_dt,
                _ATTR_DATE <= cycle_end_query_dt,
            ).group_by(ReadTxProjection.account_sync_id)
        ).all()
        per_child_cycle_spend = {acc: float(amt) for acc, amt in spend_rows}

        charged_rows = db.execute(
            select(
                ReadTxProjection.account_sync_id,
                func.coalesce(func.sum(sa_case(
                    (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                    (ReadTxProjection.tx_type == "income", -ReadTxProjection.amount),
                    else_=0.0,
                )), 0.0),
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type.in_(["expense", "income"]),
                _ATTR_DATE <= cycle_end_query_dt,
            ).group_by(ReadTxProjection.account_sync_id)
        ).all()
        per_child_lifetime_charged = {acc: float(amt) for acc, amt in charged_rows}
        # credit_used(2026-08-03 使用者反饋 #2)用轉分期前的原始終身消費,
        # 不扣沖銷 —— 轉成分期只是把「怎麼付」拆開,不代表額度立刻恢復。
        lifetime_charged_total_raw = sum(per_child_lifetime_charged.values())
        # 帳單分期沖銷(§2.3,2026-08-02 第三輪):已轉成分期的金額從「終身
        # 消費」裡永久扣掉,見 compute_offset_totals docstring —— 只影響
        # `remaining_due`(當期應繳/防重複轉分期的判斷),不影響上面的
        # credit_used。
        offset_totals = compute_offset_totals(db, ledger_id=ledger_id, member_ids=member_ids)
        for cid, amt in offset_totals.items():
            per_child_lifetime_charged[cid] = per_child_lifetime_charged.get(cid, 0.0) - amt

        # 跨幣別轉帳(2026-08):同 paid_total 下方的注釋,轉入卡片端要用卡片
        # 自身幣別的金額。
        paid_rows = db.execute(
            select(
                ReadTxProjection.to_account_sync_id,
                func.coalesce(
                    func.sum(func.coalesce(ReadTxProjection.to_amount, ReadTxProjection.amount)), 0.0
                ),
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.to_account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type == "transfer",
            ).group_by(ReadTxProjection.to_account_sync_id)
        ).all()
        per_child_lifetime_paid = {acc: float(amt) for acc, amt in paid_rows}
    else:
        lifetime_charged_total_raw = 0.0

    statement_amount = sum(per_child_cycle_spend.values())
    lifetime_charged_total = sum(per_child_lifetime_charged.values())
    lifetime_paid_total = sum(per_child_lifetime_paid.values())
    remaining_due = lifetime_charged_total - lifetime_paid_total
    credit_used = lifetime_charged_total_raw - lifetime_paid_total

    per_child_remaining_due = {
        cid: max(per_child_lifetime_charged.get(cid, 0.0) - per_child_lifetime_paid.get(cid, 0.0), 0.0)
        for cid in member_ids
    }

    if member_ids:
        open_exp, open_inc = db.execute(
            select(
                func.coalesce(func.sum(sa_case(
                    (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                    else_=0.0)), 0.0),
                func.coalesce(func.sum(sa_case(
                    (ReadTxProjection.tx_type == "income", ReadTxProjection.amount),
                    else_=0.0)), 0.0),
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type.in_(["expense", "income"]),
                _ATTR_DATE > cycle_end_query_dt,
                _ATTR_DATE <= now,
            )
        ).one()
        open_cycle_spend = float(open_exp) - float(open_inc)
    else:
        open_cycle_spend = 0.0

    return {
        "cycle_start": cycle_start,
        "cycle_end": cycle_end,
        "due_date": due_date,
        "open_cycle_start": open_start,
        "open_cycle_end": open_end,
        "open_cycle_due_date": open_due,
        "statement_amount": statement_amount,
        "remaining_due": remaining_due,
        "credit_used": credit_used,
        "paid_amount": lifetime_paid_total,
        "open_cycle_spend": open_cycle_spend,
        "per_child_cycle_spend": per_child_cycle_spend,
        "per_child_remaining_due": per_child_remaining_due,
    }


class CyclePeriodBilling(TypedDict):
    cycle_start: date
    cycle_end: date
    due_date: date
    new_spend: float
    carryover_due: float
    total_due: float
    paid_in_cycle: float
    remaining_due: float
    has_older: bool
    has_newer: bool
    per_member_new_spend: dict[str, float]


def compute_cycle_period_billing(
    db: Session,
    *,
    ledger_id: str,
    group: UserAccountProjection,
    children: Sequence[UserAccountProjection],
    now: datetime,
    cycle_offset: int = 0,
) -> CyclePeriodBilling:
    """讓使用者「按帳單週期瀏覽」(§2.9 補強,2026-08-02,對齊 Moze 參考截圖
    `< 2026/07/06–2026/08/05 >` 翻頁互動)——不是序號,是實際日期區間。
    `cycle_offset=0` 是「最近一次已結束」的週期(跟 `compute_group_billing`
    的預設週期是同一期),負數往回看歷史週期,`+1` 是目前還在累積、尚未
    結束的那期(等同 `compute_group_billing` 的 `open_cycle_*`)。

    刻意跟 `compute_group_billing` 是**兩個獨立函式**,不是共用一份改參數
    ——後者的 `remaining_due`(終身跑動餘額,已繳金額不設時間上界)被繳款/
    到期提醒/自動扣繳三處依賴,不能為了這裡新增的「上期欠款/已繳金額」語意
    去動它的既有計算,免得改一處波及三個已測過的既有功能。這裡的
    `carryover_due`/`paid_in_cycle` 改成明確以 `cycle_start`/`cycle_end` 為
    界,是「單獨看這一期帳單」的獨立語意,跟 `compute_group_billing` 回傳的
    "現在當下"欄位(不受 `cycle_offset` 影響)並存,呼叫端(read 端點)兩個
    都會呼叫、一起回傳。

    2026-08-02 使用者實測踩到一個歸屬 bug:6/30~7/30 的帳單在 8/2 才繳,
    當時「本期」(7/30~8/30)已經開始——原本的實作把「已繳金額」按繳款
    交易自己的 `happened_at` 落在哪個週期窗口內認定,於是這筆遲來的繳款被
    算進 7/30~8/30 期,而不是它真正要清償的 6/30~7/30 期,导致回顾旧一期
    時看起来「完全沒繳」。修法:改成跟真實信用卡帳單一致的「先進先出」
    水位模型——`paid_total` 是**不分期別窗口**的終身已繳總額(不看
    `happened_at`,只看金額,跟 `compute_group_billing` 的
    `per_child_lifetime_paid`/`group_leftover_paid` 同款不設時間上界的
    查法),不管繳款當下的真實日期落在哪一期,永遠優先拿去清償「最舊的」
    未繳週期,再依序輪到較新的週期——這正是信用卡遲繳仍算清償原帳單的
    真實行為。`carryover_due`/`remaining_due` 各自用「floor at 0」(欠款
    不能為負,溢繳的部分只在 `compute_group_billing` 的全局終身餘額——即
    彈窗最上方「目前應繳」那個可以顯示負數的欄位——才看得到,不會讓某一期
    的帳單顯示"負的應繳"這種不直覺的數字)。"""
    billing_day = group.billing_day
    payment_due_day = group.payment_due_day
    assert billing_day is not None and payment_due_day is not None
    member_ids = billing_member_ids(group, children)

    base_start, base_end = credit_card.most_recently_closed_cycle(now.date(), billing_day)
    cycle_start, cycle_end = credit_card.shift_cycle(base_start, base_end, billing_day, cycle_offset)
    due_date = credit_card.due_date_for_cycle_end(cycle_end, payment_due_day)
    _open_start, open_end = credit_card.billing_cycle_containing(now.date(), billing_day)

    cycle_start_dt = date_to_utc_dt(cycle_start, end_of_day=True)
    cycle_end_dt = date_to_utc_dt(cycle_end, end_of_day=True)
    # `cycle_offset=+1`(目前還在累積、尚未結束的那期)時 `cycle_end_dt` 會落在
    # 未來——查詢用的上界要 clamp 在 `now`,不然使用者先記一筆未來日期的交易
    # (例如下個月才扣款的訂閱)會被算進「新增花費」/「本期應繳」,顯示金額
    # 比實際已發生的還高(2026-08 使用者反饋 #1)。`cycle_end`/`due_date` 這些
    # 純日期標籤維持不變,只有查詢邊界 clamp。
    query_end_dt = min(cycle_end_dt, now)

    # 2026-08-07 使用者反饋(§2.9.6 Phase 7,子卡詳情不該顯示合併金額):除了
    # 整組加總的 `new_spend`,順便按 `account_sync_id` 分組算出每張子卡自己
    # 在這期貢獻的新增花費(`per_member_new_spend`)——子卡自己的詳情頁改用
    # 這份資料顯示「自己的」本期新增花費,不再借用整組合併數字(見
    # `read/ledgers.py::get_account_billing_summary` 怎麼把這個字典塞進
    # 每個 member 的 `period_new_spend`)。同一個查詢分組一次算完,不額外
    # 多打一次 DB。
    new_spend = 0.0
    per_member_new_spend: dict[str, float] = {}
    if member_ids:
        spend_rows = db.execute(
            select(
                ReadTxProjection.account_sync_id,
                func.coalesce(func.sum(sa_case(
                    (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                    (ReadTxProjection.tx_type == "income", -ReadTxProjection.amount),
                    else_=0.0,
                )), 0.0),
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type.in_(["expense", "income"]),
                _ATTR_DATE > cycle_start_dt,
                _ATTR_DATE <= query_end_dt,
            ).group_by(ReadTxProjection.account_sync_id)
        ).all()
        per_member_new_spend = {acc: float(amt) for acc, amt in spend_rows}
        new_spend = sum(per_member_new_spend.values())

    # 帳單分期沖銷(§2.3,2026-08-02 第三輪):見 compute_offset_totals
    # docstring —— 對所有 cutoff 一律扣掉同一個總額,不分時間視窗。
    offset_total = sum(compute_offset_totals(db, ledger_id=ledger_id, member_ids=member_ids).values())

    def _charged_as_of(cutoff_dt: datetime) -> float:
        if not member_ids:
            return 0.0
        raw = float(db.scalar(
            select(func.coalesce(func.sum(sa_case(
                (ReadTxProjection.tx_type == "expense", ReadTxProjection.amount),
                (ReadTxProjection.tx_type == "income", -ReadTxProjection.amount),
                else_=0.0,
            )), 0.0)).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type.in_(["expense", "income"]),
                _ATTR_DATE <= cutoff_dt,
            )
        ) or 0.0)
        return raw - offset_total

    paid_total = 0.0
    if member_ids:
        # member_ids 已经包含 group.sync_id(见 billing_member_ids),不用再
        # 额外 union 一次。
        # 跨幣別轉帳(2026-08):繳款轉入卡片端要用卡片自身幣別的金額,不是
        # 轉出端的 amount——同幣種繳款 to_amount 是 NULL,COALESCE 回退
        # amount,行為不變。
        paid_total = float(db.scalar(
            select(
                func.coalesce(
                    func.sum(func.coalesce(ReadTxProjection.to_amount, ReadTxProjection.amount)), 0.0
                )
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.to_account_sync_id.in_(member_ids),
                ReadTxProjection.tx_type == "transfer",
            )
        ) or 0.0)

    carryover_due = max(_charged_as_of(cycle_start_dt) - paid_total, 0.0)
    remaining_due = max(_charged_as_of(query_end_dt) - paid_total, 0.0)
    total_due = carryover_due + new_spend
    paid_in_cycle = round(total_due - remaining_due, 2)

    has_newer = cycle_end < open_end
    earliest = db.scalar(
        select(func.min(ReadTxProjection.happened_at)).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.account_sync_id.in_(member_ids),
        )
    ) if member_ids else None
    if earliest is not None and earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    has_older = earliest is not None and earliest <= cycle_start_dt

    return {
        "cycle_start": cycle_start,
        "cycle_end": cycle_end,
        "due_date": due_date,
        "new_spend": round(new_spend, 2),
        "carryover_due": round(carryover_due, 2),
        "total_due": round(total_due, 2),
        "paid_in_cycle": paid_in_cycle,
        "remaining_due": round(remaining_due, 2),
        "has_older": has_older,
        "has_newer": has_newer,
        "per_member_new_spend": {k: round(v, 2) for k, v in per_member_new_spend.items()},
    }


class InstallmentSummary(TypedDict):
    active_count: int
    paid_periods: int | None
    periods: int | None


def compute_installment_summary(
    db: Session, *, ledger_id: str, member_ids: Sequence[str],
) -> InstallmentSummary:
    """帳戶詳情彈窗「帳單分期」欄位(2026-08-04 使用者反饋補上,對齊 Moze
    參考截圖)。只看 `status == "active"` 的分期計畫,`paid_periods` 算法
    對齊 `read/ledgers.py::list_installment_plans`(從 `read_installment_
    period_projection` 的 `due_at <= now` 即時算,不信任 projection 本身
    不被排程更新的歷史相容欄位)。回傳結構化數字(不是預先格式化的文案)
    讓前端依當前語系自己組字串;`active_count == 0` 時前端顯示「---」,
    `active_count == 1` 時額外帶 `paid_periods`/`periods` 顯示進度,
    `active_count > 1` 時只顯示筆數(彈窗空間有限,不逐筆列出每個計畫的
    細節,詳細清單使用者可以自己去 `/app/installment-plans` 頁面看)。"""
    if not member_ids:
        return {"active_count": 0, "paid_periods": None, "periods": None}
    plans = db.scalars(
        select(ReadInstallmentPlanProjection).where(
            ReadInstallmentPlanProjection.ledger_id == ledger_id,
            ReadInstallmentPlanProjection.account_sync_id.in_(member_ids),
            ReadInstallmentPlanProjection.status == "active",
        )
    ).all()
    if not plans:
        return {"active_count": 0, "paid_periods": None, "periods": None}
    if len(plans) > 1:
        return {"active_count": len(plans), "paid_periods": None, "periods": None}

    plan = plans[0]
    now = datetime.now(timezone.utc)
    due_dates = db.scalars(
        select(ReadInstallmentPeriodProjection.due_at).where(
            ReadInstallmentPeriodProjection.ledger_id == ledger_id,
            ReadInstallmentPeriodProjection.plan_sync_id == plan.sync_id,
        )
    ).all()
    paid_periods = sum(1 for d in due_dates if (d if d.tzinfo else d.replace(tzinfo=timezone.utc)) <= now)
    return {"active_count": 1, "paid_periods": paid_periods, "periods": plan.periods}


def compute_card_payment_allocations(
    *, group_sync_id: str, remaining_due_by_child: dict[str, float], amount: float,
) -> dict[str, float]:
    """把一次繳款總額 `amount` 分攤到各子帳戶身上,抽出來給
    `write/accounts.py::card_payment_ep`(使用者手動繳款)跟
    `services.credit_card_autopay`(自動扣繳到期物化)共用同一份分攤規則,
    不然兩處各寫一份容易改一邊漏一邊。規則(不是等比例打折):
    1. `amount` >= 全部子帳戶應繳總和:每個子帳戶各自拿到「完整付清」的
       金額,剩下的溢繳另外記一筆在 `group_sync_id` 自己身上(結轉到未來
       各期的信用額度)。
    2. `amount` < 應繳總和:按各子帳戶應繳金額比例分攤,最後一個子帳戶用
       減法拿餘數,避免四捨五入加總對不上輸入金額,不製造「群組溢繳」假象。
    回傳 `{target_sync_id: amount}`,金額 <= 0 或本來就不欠錱的子帳戶不會
    出現在結果裡。獨立信用卡場景(`group_sync_id` 本身就是唯一子帳戶)下,
    分攤 key 可能跟溢繳結轉 key 相撞,這裡用累加而不是覆蓋,不會把應繳金額
    洗掉。"""
    total_children_due = sum(remaining_due_by_child.values())
    allocations: dict[str, float] = {}
    if total_children_due <= 0:
        if amount > 0:
            allocations[group_sync_id] = amount
        return allocations
    if amount >= total_children_due:
        for child_id, due in remaining_due_by_child.items():
            if due > 0:
                allocations[child_id] = round(due, 2)
        leftover = round(amount - total_children_due, 2)
        if leftover > 0:
            allocations[group_sync_id] = allocations.get(group_sync_id, 0.0) + leftover
        return allocations
    due_children = [(cid, due) for cid, due in remaining_due_by_child.items() if due > 0]
    allocated_so_far = 0.0
    for i, (child_id, due) in enumerate(due_children):
        if i == len(due_children) - 1:
            share = round(amount - allocated_so_far, 2)
        else:
            share = round(amount * (due / total_children_due), 2)
        allocations[child_id] = share
        allocated_so_far += share
    return allocations
