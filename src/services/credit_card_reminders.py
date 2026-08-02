"""信用卡繳款到期提醒(MOZE_FEATURE_GAP_SD.md §2.9 Phase 4,2026-08-02 補)。

§2.1 通知中心设计时就预留了 `category="card_due"`,但 Phase 4 一開始只做了
帳務計算,沒接上提醒 —— 這裡補上。跟 `debt_reminders.py` 同一个模式(查
projection → 判斷是否該提醒 → `create_notification` 落一行,不 commit,
调用方决定事务边界),但触发时机不一样:債務只提醒一次(到期前 3 天內,
提醒過就不再提醒),信用卡帳單是每月循环的,同一個群組每個帳單週期都要
各自提醒,所以去重 key 除了 accountId 还要带上 cycleEnd(ISO 日期字串)+
kind,不是"這張卡有没有提醒过"而是"這一期的這種提醒发过没有"。

四種提醒時機(對齊使用者需求,2026-08-02 确认;`overdue` 為 2026-08-04
補):
- `statement_closed`:結帳日當天(`now.date() == cycle_end`),帳單金額
  剛结算出來。
- `due_soon`:到期前 7 天(`now.date() == due_date - 7 天`)。
- `due_today`:到期當天(`now.date() == due_date`)。
- `overdue`:已逾期(`now.date() > due_date` 且 `remaining_due > 0`)。原本
  三種時機都是精確比對某一天,如果使用者是在還款日已經過去之後才建立/
  補設定帳單日還款日(或伺服器在那一天精確 tick 之外的空窗完全沒跑),
  就會永遠錯過 `due_today` 這個精確匹配,之後再也不會有任何提醒——這
  是 2026-08-04 使用者實測建了一筆已逾期帳單後回報「完全沒收到提醒」
  挖到的既有 gap,補一個不依賴精確日期匹配的兜底時機,同一期只發一次
  (去重 key 同樣是 accountId+cycleEnd+kind)。

只对通过 `credit_card_billing.is_billing_root` 檢查(account_group,或沒有
掛靠任何群組的獨立信用卡,2026-08-02 第二輪放寬)且已設定
billing_day/payment_due_day 的帳戶跑,且該期 `remaining_due <= 0`
(已繳清/溢繳)時跳過 —— 不用提醒已经不欠钱的帳單。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Notification, ReadTxProjection, UserAccountProjection
from . import credit_card, credit_card_billing
from . import notifications as notification_service

logger = logging.getLogger(__name__)


def _already_sent(db: Session, *, user_id: str, account_id: str, cycle_end_iso: str, kind: str) -> bool:
    rows = db.scalars(
        select(Notification.payload_json).where(
            Notification.user_id == user_id,
            Notification.category == "card_due",
        )
    ).all()
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("accountId") == account_id
            and payload.get("cycleEnd") == cycle_end_iso
            and payload.get("kind") == kind
        ):
            return True
    return False


def send_due_card_reminders(db: Session, *, now: datetime | None = None) -> int:
    """扫所有 account_group 帳戶,依上面三種時機各自判斷是否要提醒。返回
    发出的提醒数。不 commit —— 调用方决定事务边界(跟 debt_reminders 同款
    约定)。"""
    now = now or datetime.now(timezone.utc)
    today = now.date()

    # account_group,或没有掛靠任何群組的獨立信用卡(is_billing_root 的两种
    # 场景),都要各自检查到期提醒;已经掛靠某个群组的子卡跳过——它的帳單
    # 走它的群组那份提醒,不重复提醒两次。
    candidates = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.account_type.in_(["account_group", "credit_card"]),
            UserAccountProjection.billing_day.is_not(None),
            UserAccountProjection.payment_due_day.is_not(None),
        )
    ).all()
    groups = [a for a in candidates if credit_card_billing.is_billing_root(a)]
    if not groups:
        return 0

    sent = 0
    for group in groups:
        # WHERE 已经过滤了 billing_day/payment_due_day IS NOT NULL,mypy 推
        # 不出这层窄化,这里断言一下让类型检查过。
        assert group.billing_day is not None and group.payment_due_day is not None
        cycle_start, cycle_end = credit_card.most_recently_closed_cycle(today, group.billing_day)
        due_date = credit_card.due_date_for_cycle_end(cycle_end, group.payment_due_day)

        kind: str | None = None
        if today == cycle_end:
            kind = "statement_closed"
        elif today == due_date:
            kind = "due_today"
        elif (due_date - today).days == 7:
            kind = "due_soon"
        elif today > due_date:
            kind = "overdue"
        if kind is None:
            continue

        cycle_end_iso = cycle_end.isoformat()
        if _already_sent(db, user_id=group.user_id, account_id=group.sync_id, cycle_end_iso=cycle_end_iso, kind=kind):
            continue

        children = credit_card_billing.resolve_billing_children(db, account=group)
        if not children:
            continue

        # account 是 user-global 实体,没有自己的 ledger_id 字段(billing-summary
        # 读端点靠 URL 路径里的 ledger_external_id 决定查哪本帐)。提醒这里没有
        # "当前打开哪本帐"的概念,退而求其次:找任一笔子帳戶交易所在的 ledger
        # (§2.9 本来就假设一张卡的交易都记在同一本帐,是文档里明写的既有
        # 简化,不是这里新引入的不一致)。查不到交易(还没消费过)就跳过。
        # 2026-08-03:group 自己現在也可能直接持有分期期數交易(§2.3 第四輪),
        # 找 ledger 時併入 group 自己的 sync_id。
        child_ids = credit_card_billing.billing_member_ids(group, children)
        ledger_id = db.scalar(
            select(ReadTxProjection.ledger_id).where(
                ReadTxProjection.user_id == group.user_id,
                ReadTxProjection.account_sync_id.in_(child_ids),
            ).limit(1)
        )
        if ledger_id is None:
            continue
        ledger_external_id = notification_service.resolve_ledger_external_id(db, ledger_id)
        billing = credit_card_billing.compute_group_billing(
            db, ledger_id=ledger_id, group=group, children=children, now=now,
        )
        remaining_due = billing["remaining_due"]
        if remaining_due <= 0.01:
            continue  # 已繳清/溢繳,不提醒

        label = {
            "statement_closed": "帳單已結算",
            "due_soon": "即將到期",
            "due_today": "今天到期",
            "overdue": "已逾期",
        }[kind]
        notification_service.create_notification(
            db,
            user_id=group.user_id,
            category="card_due",
            title=f"{label}：{group.name or '信用卡'}",
            body=(
                f"{group.name or '信用卡'} 帳單({cycle_start.isoformat()}~{cycle_end_iso}）"
                f"應繳 {remaining_due:.2f},繳款截止日 {due_date.isoformat()}"
                + ("(已逾期)" if kind == "overdue" else "")
            ),
            payload={
                "accountId": group.sync_id,
                "cycleEnd": cycle_end_iso,
                "kind": kind,
                "ledgerId": ledger_external_id,
            },
        )
        sent += 1

    if sent:
        logger.info("credit_card_reminders: sent %d reminders", sent)
    return sent
