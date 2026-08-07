"""从 projection 表按需拼装 snapshot dict。

方案 B 里 projection 是权威源,snapshot 不再 runtime 写入。但 mobile 协议
(`/sync/full`)、snapshot_mutator(web write 路径)还吃 snapshot dict 作输入,所以
提供一个按 (ledger_id, max_change_id) 缓存的 builder。

字段 shape 跟原先 mobile push 来的 snapshot 完全对齐 —— mobile 客户端零改动。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Ledger,
    ReadBudgetProjection,
    ReadCardRewardRuleProjection,
    ReadDebtProjection,
    ReadInstallmentPeriodProjection,
    ReadInstallmentPlanProjection,
    ReadRecurringRuleProjection,
    ReadTxProjection,
    ReadTxTemplateProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)


def _to_iso_utc(dt) -> str | None:
    """Match snapshot_mutator._to_iso8601 output format ——带 +00:00 后缀。
    SQLite 存 DateTime 可能返回 naive datetime,这里补 UTC 再 isoformat。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def build(db: Session, ledger: Ledger) -> dict[str, Any]:
    """从 projection 5 张表 + Ledger 元数据拼装完整 snapshot dict。

    热路径 —— 用 SQL Core 跳过 ORM hydration(ORM 5000 行 ~65ms,Core ~20ms)。
    调用点:`/sync/full`、`_commit_write` 取 prev 快照做 diff、admin debug。
    """
    ledger_id = ledger.id
    user_id = ledger.user_id

    # Items —— SQL Core,按列顺序取 tuple,比 ORM 快 3 倍
    items: list[dict[str, Any]] = []
    tx_stmt = select(
        ReadTxProjection.sync_id,
        ReadTxProjection.tx_type,
        ReadTxProjection.amount,
        ReadTxProjection.happened_at,
        ReadTxProjection.note,
        ReadTxProjection.category_sync_id,
        ReadTxProjection.category_name,
        ReadTxProjection.category_kind,
        ReadTxProjection.account_sync_id,
        ReadTxProjection.account_name,
        ReadTxProjection.from_account_sync_id,
        ReadTxProjection.from_account_name,
        ReadTxProjection.to_account_sync_id,
        ReadTxProjection.to_account_name,
        ReadTxProjection.tags_csv,
        ReadTxProjection.tag_sync_ids_json,
        ReadTxProjection.attachments_json,
        ReadTxProjection.tx_index,
        ReadTxProjection.created_by_user_id,
        # 交易级多币种(0018):full pull 重建的 item 不带这两列的话,新 App
        # 全量同步后外币折算全部丢失(apply 缺省 nativeAmount=amount 退化 1:1)。
        ReadTxProjection.currency_code,
        ReadTxProjection.native_amount,
        # 退款(§2.6)/ 分期(§2.3)反查字段:同样必须进 full snapshot,否则新
        # 设备首次同步 / 重装后这两个关联全丢。
        ReadTxProjection.refund_of_sync_id,
        ReadTxProjection.installment_plan_sync_id,
        # 週期性收支(§2.12.2 Phase 1.5)反查字段 + 单笔编辑标记,同样必须进
        # full snapshot,否则下一次 _commit_write 的 diff 会把这两个字段撤销。
        ReadTxProjection.recurring_rule_sync_id,
        ReadTxProjection.recurring_occurrence_overridden,
        # 拆帳(§2.4):splits_json 是 LWW merge fallback 的权威值,full snapshot
        # 必须带上,否则新设备首次同步 / _commit_write 的下一次 diff 会把
        # 已有 splits 当成"这笔交易本来就没有 splits"而静默清空。
        ReadTxProjection.splits_json,
        # 借還款追蹤(§2.5 Phase 3)反查字段,同样必须进 full snapshot,原因
        # 同 refund_of_sync_id/installment_plan_sync_id。
        ReadTxProjection.debt_sync_id,
        # 信用卡紅利回饋(§2.9.5,2026-08-06 改版)使用者勾選字段 —— 之前漏
        # 加进这个 SELECT(既有 bug,2026-08-04 补上):没有它,`_commit_write`
        # 下一次拿 snapshot_builder.build() 当 prev_snapshot 时会看不到这笔
        # 交易已勾选的规则,编辑该交易任何其它字段都可能把 rewardRuleIds
        # 静默撤销,原因同 splits_json 那条注释。
        ReadTxProjection.reward_rule_sync_ids_json,
        # 信用卡紅利回饋自動入帳(§2.9.5.4 補強)反查字段,同样必须进 full
        # snapshot,原因同上。
        ReadTxProjection.reward_source_tx_sync_id,
        # 延後入帳(§2.10 Phase 5),同样必须进 full snapshot,原因同上
        # (CLAUDE.md 記過的既有 bug 模式:漏 SELECT 新欄位 → 下一次
        # `_commit_write` 的 diff 把它當「本來就沒有」靜默清空)。
        ReadTxProjection.deferred_posting_at,
        # 對帳模式(§2.10,2026-08-09 改版),同样必须进 full snapshot,原因
        # 同上(漏 SELECT 新欄位的既有 bug 模式)。
        ReadTxProjection.reconciled_at,
    ).where(ReadTxProjection.ledger_id == ledger_id).order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    )
    for row in db.execute(tx_stmt).all():
        (sync_id, tx_type, amount, happened_at, note,
         cat_sid, cat_name, cat_kind,
         acc_sid, acc_name,
         from_sid, from_name,
         to_sid, to_name,
         tags_csv, tag_ids_json, attachments_json,
         tx_index, created_by,
         currency_code, native_amount,
         refund_of_id, installment_plan_id,
         recurring_rule_id, recurring_occurrence_overridden,
         splits_json, debt_id,
         reward_rule_ids_json, reward_source_tx_id,
         deferred_posting_at, reconciled_at) = row
        item: dict[str, Any] = {
            "syncId": sync_id,
            "type": tx_type,
            "amount": amount,
            "happenedAt": _to_iso_utc(happened_at),
        }
        if note is not None:
            item["note"] = note
        if cat_sid:
            item["categoryId"] = cat_sid
        if cat_name:
            item["categoryName"] = cat_name
        if cat_kind:
            item["categoryKind"] = cat_kind
        if acc_sid:
            item["accountId"] = acc_sid
        if acc_name:
            item["accountName"] = acc_name
        if from_sid:
            item["fromAccountId"] = from_sid
        if from_name:
            item["fromAccountName"] = from_name
        if to_sid:
            item["toAccountId"] = to_sid
        if to_name:
            item["toAccountName"] = to_name
        if tags_csv:
            item["tags"] = tags_csv
        if tag_ids_json:
            try:
                tag_ids = json.loads(tag_ids_json)
                if isinstance(tag_ids, list) and tag_ids:
                    item["tagIds"] = tag_ids
            except json.JSONDecodeError:
                pass
        if attachments_json:
            try:
                atts = json.loads(attachments_json)
                if isinstance(atts, list) and atts:
                    item["attachments"] = atts
            except json.JSONDecodeError:
                pass
        if tx_index:
            item["txIndex"] = tx_index
        if created_by:
            item["createdByUserId"] = created_by
        # NULL(旧数据)不产生 key,payload 保持干净;统计端 COALESCE 兜底。
        if currency_code:
            item["currencyCode"] = currency_code
        if native_amount is not None:
            item["nativeAmount"] = native_amount
        if refund_of_id:
            item["refundOfId"] = refund_of_id
        if installment_plan_id:
            item["installmentPlanId"] = installment_plan_id
        if recurring_rule_id:
            item["recurringRuleId"] = recurring_rule_id
        if recurring_occurrence_overridden:
            item["recurringOccurrenceOverridden"] = True
        if splits_json:
            try:
                splits = json.loads(splits_json)
                if isinstance(splits, list) and splits:
                    item["splits"] = splits
            except json.JSONDecodeError:
                pass
        if debt_id:
            item["debtId"] = debt_id
        if reward_rule_ids_json:
            try:
                reward_rule_ids = json.loads(reward_rule_ids_json)
                if isinstance(reward_rule_ids, list) and reward_rule_ids:
                    item["rewardRuleIds"] = reward_rule_ids
            except json.JSONDecodeError:
                pass
        if reward_source_tx_id:
            item["rewardSourceTxId"] = reward_source_tx_id
        if deferred_posting_at is not None:
            item["deferredPostingAt"] = _to_iso_utc(deferred_posting_at)
        if reconciled_at is not None:
            item["reconciledAt"] = _to_iso_utc(reconciled_at)
        items.append(item)

    # Accounts —— user-global per-user 表,按 user_id 取。snapshot 内仍把全用户
     # 的账户都铺出来:mobile 早期版本依赖 snapshot.accounts 完整 — 用户多账本
     # 时数据一致(同一份 accounts 拷贝到每个账本的 snapshot)。
    accounts: list[dict[str, Any]] = []
    acc_stmt = select(
        UserAccountProjection.sync_id,
        UserAccountProjection.name,
        UserAccountProjection.account_type,
        UserAccountProjection.currency,
        UserAccountProjection.initial_balance,
        UserAccountProjection.note,
        UserAccountProjection.credit_limit,
        UserAccountProjection.billing_day,
        UserAccountProjection.payment_due_day,
        UserAccountProjection.bank_name,
        UserAccountProjection.card_last_four,
        UserAccountProjection.parent_account_id,
        UserAccountProjection.hidden,
        UserAccountProjection.auto_pay_enabled,
        UserAccountProjection.auto_pay_from_account_id,
        UserAccountProjection.avatar_cloud_file_id,
        UserAccountProjection.avatar_cloud_sha256,
    ).where(UserAccountProjection.user_id == user_id)
    for (
        sid,
        name,
        acc_type,
        acc_ccy,
        init_bal,
        note,
        credit_limit,
        billing_day,
        payment_due_day,
        bank_name,
        card_last_four,
        parent_account_id,
        hidden,
        auto_pay_enabled,
        auto_pay_from_account_id,
        avatar_cloud_file_id,
        avatar_cloud_sha256,
    ) in db.execute(acc_stmt).all():
        acc: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if acc_type:
            acc["type"] = acc_type
        if acc_ccy:
            acc["currency"] = acc_ccy
        if init_bal is not None:
            acc["initialBalance"] = init_bal
        if note:
            acc["note"] = note
        if credit_limit is not None:
            acc["creditLimit"] = credit_limit
        if billing_day is not None:
            acc["billingDay"] = billing_day
        if payment_due_day is not None:
            acc["paymentDueDay"] = payment_due_day
        if bank_name:
            acc["bankName"] = bank_name
        if card_last_four:
            acc["cardLastFour"] = card_last_four
        if parent_account_id:
            acc["parentAccountId"] = parent_account_id
        # 账户隐藏(issue #240):无条件输出(不像其它扩展字段那样"有值才带
        # key"),与 App serializeAccount 无条件发 hidden 对齐,保 /sync/full
        # 重装 / 新设备首次同步时隐藏标记不丢(03-tech-design-cloud.md §二 (B))。
        acc["hidden"] = bool(hidden)
        # 自動扣繳(§2.9,2026-08-04 改版):NOT NULL 布尔列,同 hidden 无条件
        # 输出。2026-08-02 补强发现这两个字段(连同下面头像字段)原本完全没
        # 进这个 SELECT——写路径的 diff-emit 用这个函数重建"prev"基线,任何
        # 跟 autopay/avatar 无关的账户编辑(改名/备注等)都会因为这里漏选,
        # 导致 diff 出的 entity dict 里这些 key 整个缺失,被 upsert_account
        # 当成"没传"写成 null/False,静默清空用户已设置的自動扣繳。
        acc["autoPayEnabled"] = bool(auto_pay_enabled)
        if auto_pay_from_account_id:
            acc["autoPayFromAccountId"] = auto_pay_from_account_id
        # 帳戶頭像(2026-08-02 補強)。
        if avatar_cloud_file_id:
            acc["avatarCloudFileId"] = avatar_cloud_file_id
        if avatar_cloud_sha256:
            acc["avatarCloudSha256"] = avatar_cloud_sha256
        accounts.append(acc)

    # Categories —— 同 accounts,user-global per-user。
    categories: list[dict[str, Any]] = []
    cat_stmt = select(
        UserCategoryProjection.sync_id,
        UserCategoryProjection.name,
        UserCategoryProjection.kind,
        UserCategoryProjection.level,
        UserCategoryProjection.sort_order,
        UserCategoryProjection.icon,
        UserCategoryProjection.icon_type,
        UserCategoryProjection.custom_icon_path,
        UserCategoryProjection.icon_cloud_file_id,
        UserCategoryProjection.icon_cloud_sha256,
        UserCategoryProjection.parent_name,
    ).where(UserCategoryProjection.user_id == user_id).order_by(
        UserCategoryProjection.sort_order.asc(),
        UserCategoryProjection.name.asc(),
    )
    for (sid, name, kind, level, sort_order, icon, icon_type,
         custom_icon, icon_fid, icon_sha, parent) in db.execute(cat_stmt).all():
        cat: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if kind:
            cat["kind"] = kind
        if level is not None:
            cat["level"] = level
        if sort_order is not None:
            cat["sortOrder"] = sort_order
        if icon:
            cat["icon"] = icon
        if icon_type:
            cat["iconType"] = icon_type
        if custom_icon:
            cat["customIconPath"] = custom_icon
        if icon_fid:
            cat["iconCloudFileId"] = icon_fid
        if icon_sha:
            cat["iconCloudSha256"] = icon_sha
        if parent:
            cat["parentName"] = parent
        categories.append(cat)

    # Tags —— user-global per-user。
    tags: list[dict[str, Any]] = []
    tag_stmt = select(
        UserTagProjection.sync_id,
        UserTagProjection.name,
        UserTagProjection.color,
    ).where(UserTagProjection.user_id == user_id).order_by(UserTagProjection.name.asc())
    for sid, name, color in db.execute(tag_stmt).all():
        t: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if color:
            t["color"] = color
        tags.append(t)

    # Budgets
    # mobile sync_engine._applyBudgetChange 用 payload['ledgerSyncId'] 解析本地
    # ledger int id(不像 tx 用 change.ledger_id 字段),所以 budget snapshot 必
    # 须显式带这个字段;不带则 mobile 收到 change 后会因 localLedgerId==null 直接
    # skip,web 改了 mobile 那边永远刷不出来。
    budgets: list[dict[str, Any]] = []
    bud_stmt = select(
        ReadBudgetProjection.sync_id,
        ReadBudgetProjection.budget_type,
        ReadBudgetProjection.category_sync_id,
        ReadBudgetProjection.amount,
        ReadBudgetProjection.period,
        ReadBudgetProjection.start_day,
        ReadBudgetProjection.enabled,
    ).where(ReadBudgetProjection.ledger_id == ledger_id)
    for sid, btype, cat_sid, amt, period, start_day, enabled in db.execute(bud_stmt).all():
        b: dict[str, Any] = {"syncId": sid, "ledgerSyncId": ledger.external_id}
        if btype:
            b["type"] = btype
        if cat_sid:
            b["categoryId"] = cat_sid
        if amt is not None:
            b["amount"] = amt
        if period:
            b["period"] = period
        if start_day is not None:
            b["startDay"] = start_day
        b["enabled"] = bool(enabled)
        budgets.append(b)

    # Recurring rules(§2.2)
    recurring_rules: list[dict[str, Any]] = []
    rec_stmt = select(
        ReadRecurringRuleProjection.sync_id,
        ReadRecurringRuleProjection.tx_type,
        ReadRecurringRuleProjection.amount,
        ReadRecurringRuleProjection.note,
        ReadRecurringRuleProjection.category_sync_id,
        ReadRecurringRuleProjection.account_sync_id,
        ReadRecurringRuleProjection.from_account_sync_id,
        ReadRecurringRuleProjection.to_account_sync_id,
        ReadRecurringRuleProjection.frequency,
        ReadRecurringRuleProjection.interval,
        ReadRecurringRuleProjection.next_run_at,
        ReadRecurringRuleProjection.end_at,
        ReadRecurringRuleProjection.enabled,
        ReadRecurringRuleProjection.generated_until_at,
        ReadRecurringRuleProjection.advanced_rule_json,
    ).where(ReadRecurringRuleProjection.ledger_id == ledger_id)
    for (sid, tx_type, amount, note, cat_sid, acc_sid, from_sid, to_sid,
         frequency, interval, next_run_at, end_at, enabled,
         generated_until_at, advanced_rule_json) in db.execute(rec_stmt).all():
        r: dict[str, Any] = {
            "syncId": sid,
            "txType": tx_type,
            "amount": amount,
            "frequency": frequency,
            "interval": interval,
            "nextRunAt": _to_iso_utc(next_run_at),
            "enabled": bool(enabled),
        }
        if note is not None:
            r["note"] = note
        if cat_sid:
            r["categoryId"] = cat_sid
        if acc_sid:
            r["accountId"] = acc_sid
        if from_sid:
            r["fromAccountId"] = from_sid
        if to_sid:
            r["toAccountId"] = to_sid
        if end_at is not None:
            r["endAt"] = _to_iso_utc(end_at)
        if generated_until_at is not None:
            r["generatedUntilAt"] = _to_iso_utc(generated_until_at)
        if advanced_rule_json:
            try:
                parsed_rule = json.loads(advanced_rule_json)
                if isinstance(parsed_rule, dict):
                    r["advancedRuleJson"] = parsed_rule
            except json.JSONDecodeError:
                pass
        recurring_rules.append(r)

    # Installment periods(§2.12.1 Phase 1.5)—— 先读,installment plan 的
    # paidPeriods/nextPeriodAt 要从这里 derive(不再信任 projection 里那两个
    # 不再被排程写入的历史相容字段)。
    periods_by_plan: dict[str, list[dict[str, Any]]] = {}
    installment_periods: list[dict[str, Any]] = []
    period_stmt = select(
        ReadInstallmentPeriodProjection.sync_id,
        ReadInstallmentPeriodProjection.plan_sync_id,
        ReadInstallmentPeriodProjection.period_no,
        ReadInstallmentPeriodProjection.due_at,
        ReadInstallmentPeriodProjection.principal_amount,
        ReadInstallmentPeriodProjection.interest_amount,
        ReadInstallmentPeriodProjection.total_amount,
        ReadInstallmentPeriodProjection.status,
        ReadInstallmentPeriodProjection.tx_sync_id,
    ).where(ReadInstallmentPeriodProjection.ledger_id == ledger_id).order_by(
        ReadInstallmentPeriodProjection.plan_sync_id,
        ReadInstallmentPeriodProjection.period_no,
    )
    for (sid, plan_sid, period_no, due_at, principal_amount, interest_amount,
         total_amount, status, tx_sid) in db.execute(period_stmt).all():
        entry: dict[str, Any] = {
            "syncId": sid,
            "planId": plan_sid,
            "periodNo": period_no,
            "dueAt": _to_iso_utc(due_at),
            "principalAmount": principal_amount,
            "interestAmount": interest_amount,
            "totalAmount": total_amount,
            "status": status,
        }
        if tx_sid:
            entry["txId"] = tx_sid
        installment_periods.append(entry)
        periods_by_plan.setdefault(plan_sid, []).append(entry)

    # Installment plans(§2.3 / §2.12.1)
    installment_plans: list[dict[str, Any]] = []
    ins_stmt = select(
        ReadInstallmentPlanProjection.sync_id,
        ReadInstallmentPlanProjection.total_amount,
        ReadInstallmentPlanProjection.periods,
        ReadInstallmentPlanProjection.period_amount,
        ReadInstallmentPlanProjection.first_period_at,
        ReadInstallmentPlanProjection.account_sync_id,
        ReadInstallmentPlanProjection.category_sync_id,
        ReadInstallmentPlanProjection.note,
        ReadInstallmentPlanProjection.status,
        ReadInstallmentPlanProjection.repayment_method,
        ReadInstallmentPlanProjection.interest_period,
        ReadInstallmentPlanProjection.interest_rate,
        ReadInstallmentPlanProjection.round_amounts,
        ReadInstallmentPlanProjection.remainder_position,
        ReadInstallmentPlanProjection.grace_period_months,
        ReadInstallmentPlanProjection.offset_breakdown_json,
    ).where(ReadInstallmentPlanProjection.ledger_id == ledger_id)
    now = datetime.now(timezone.utc)
    for (sid, total_amount, periods, period_amount, first_period_at,
         acc_sid, cat_sid, note, status, repayment_method, interest_period,
         interest_rate, round_amounts, remainder_position,
         grace_period_months, offset_breakdown_json) in db.execute(ins_stmt).all():
        plan_periods = periods_by_plan.get(sid) or []
        # paidPeriods/nextPeriodAt/periodAmount 不再由排程写入,这里从 period
        # 明细即时算出(见 ReadInstallmentPlanProjection docstring)。没有
        # period 明细(理论上不会出现,兜底)时退回旧字段语义:0 期已付,
        # 下一期=首期,periodAmount=创建时算的天真平均值。
        periods_sorted = sorted(
            (datetime.fromisoformat(p["dueAt"]), p["totalAmount"])
            for p in plan_periods if p.get("dueAt")
        )
        due_dates = [d for d, _ in periods_sorted]
        paid_periods = sum(1 for d in due_dates if d <= now)
        future_periods = [(d, amt) for d, amt in periods_sorted if d > now]
        if future_periods:
            next_period_at, current_period_amount = future_periods[0]
        elif periods_sorted:
            next_period_at, current_period_amount = periods_sorted[-1]
        else:
            next_period_at, current_period_amount = first_period_at, period_amount
        p: dict[str, Any] = {
            "syncId": sid,
            "totalAmount": total_amount,
            "periods": periods,
            "periodAmount": current_period_amount,
            "firstPeriodAt": _to_iso_utc(first_period_at),
            "nextPeriodAt": _to_iso_utc(next_period_at),
            "paidPeriods": paid_periods,
            "status": status,
            "repaymentMethod": repayment_method,
            "interestPeriod": interest_period,
            "interestRate": interest_rate,
            "roundAmounts": bool(round_amounts),
            "remainderPosition": remainder_position,
            "gracePeriodMonths": grace_period_months,
        }
        if acc_sid:
            p["accountId"] = acc_sid
        if cat_sid:
            p["categoryId"] = cat_sid
        if note is not None:
            p["note"] = note
        if offset_breakdown_json:
            p["offsetBreakdownJson"] = offset_breakdown_json
        installment_plans.append(p)

    # 借還款追蹤(§2.5 Phase 3)
    debts: list[dict[str, Any]] = []
    debt_stmt = select(
        ReadDebtProjection.sync_id,
        ReadDebtProjection.direction,
        ReadDebtProjection.counterparty_name,
        ReadDebtProjection.principal_amount,
        ReadDebtProjection.due_at,
        ReadDebtProjection.note,
        ReadDebtProjection.closed_at,
    ).where(ReadDebtProjection.ledger_id == ledger_id)
    for (sid, direction, counterparty_name, principal_amount, due_at, note, closed_at) in db.execute(debt_stmt).all():
        d: dict[str, Any] = {
            "syncId": sid,
            "direction": direction,
            "counterpartyName": counterparty_name,
            "principalAmount": principal_amount,
        }
        if due_at is not None:
            d["dueAt"] = _to_iso_utc(due_at)
        if note is not None:
            d["note"] = note
        if closed_at is not None:
            d["closedAt"] = _to_iso_utc(closed_at)
        debts.append(d)

    # 交易範本(§2.7 Phase 3)
    tx_templates: list[dict[str, Any]] = []
    tpl_stmt = select(
        ReadTxTemplateProjection.sync_id,
        ReadTxTemplateProjection.name,
        ReadTxTemplateProjection.tx_type,
        ReadTxTemplateProjection.amount,
        ReadTxTemplateProjection.note,
        ReadTxTemplateProjection.category_sync_id,
        ReadTxTemplateProjection.account_sync_id,
        ReadTxTemplateProjection.from_account_sync_id,
        ReadTxTemplateProjection.to_account_sync_id,
        ReadTxTemplateProjection.tag_sync_ids_json,
        ReadTxTemplateProjection.sort_order,
    ).where(ReadTxTemplateProjection.ledger_id == ledger_id).order_by(
        ReadTxTemplateProjection.sort_order.asc(),
    )
    for (sid, name, tx_type, amount, note, cat_sid, acc_sid, from_sid, to_sid,
         tag_ids_json, sort_order) in db.execute(tpl_stmt).all():
        tpl: dict[str, Any] = {
            "syncId": sid,
            "name": name,
            "txType": tx_type,
            "amount": amount,
            "sortOrder": sort_order,
        }
        if note is not None:
            tpl["note"] = note
        if cat_sid:
            tpl["categoryId"] = cat_sid
        if acc_sid:
            tpl["accountId"] = acc_sid
        if from_sid:
            tpl["fromAccountId"] = from_sid
        if to_sid:
            tpl["toAccountId"] = to_sid
        if tag_ids_json:
            try:
                tag_ids = json.loads(tag_ids_json)
                if isinstance(tag_ids, list) and tag_ids:
                    tpl["tagIds"] = tag_ids
            except json.JSONDecodeError:
                pass
        tx_templates.append(tpl)

    # 信用卡紅利回饋規則(§2.9.5 Phase 4.5)—— user-global,同 accounts 按
    # user_id 取,不按 ledger_id(即使 diff/write 引擎走 per-ledger snapshot,
    # 底层这份表跟账本无关)。
    card_reward_rules: list[dict[str, Any]] = []
    crr_stmt = select(
        ReadCardRewardRuleProjection.sync_id,
        ReadCardRewardRuleProjection.account_sync_id,
        ReadCardRewardRuleProjection.label,
        ReadCardRewardRuleProjection.category_sync_ids_json,
        ReadCardRewardRuleProjection.rate_type,
        ReadCardRewardRuleProjection.rate_value,
        ReadCardRewardRuleProjection.rounding,
        ReadCardRewardRuleProjection.total_rounding,
        ReadCardRewardRuleProjection.calc_basis,
        ReadCardRewardRuleProjection.interval,
        ReadCardRewardRuleProjection.min_spend_threshold,
        ReadCardRewardRuleProjection.min_tx_amount,
        ReadCardRewardRuleProjection.cap_amount,
        ReadCardRewardRuleProjection.cap_shared_key,
        ReadCardRewardRuleProjection.starts_at,
        ReadCardRewardRuleProjection.ends_at,
        ReadCardRewardRuleProjection.settlement_type,
        ReadCardRewardRuleProjection.settlement_days,
        ReadCardRewardRuleProjection.settlement_month_offset,
        ReadCardRewardRuleProjection.settlement_day_of_month,
        ReadCardRewardRuleProjection.reward_account_id,
        ReadCardRewardRuleProjection.note,
        ReadCardRewardRuleProjection.enabled,
    ).where(ReadCardRewardRuleProjection.user_id == user_id)
    for (
        sid, acc_sid, label, category_ids_json, rate_type, rate_value, rounding,
        total_rounding, calc_basis, interval, min_spend_threshold, min_tx_amount, cap_amount,
        cap_shared_key, starts_at, ends_at, settlement_type, settlement_days,
        settlement_month_offset, settlement_day_of_month,
        reward_account_id, note, enabled,
    ) in db.execute(crr_stmt).all():
        rule: dict[str, Any] = {
            "syncId": sid,
            "accountId": acc_sid or "",
            "label": label or "",
            "rateType": rate_type or "percentage",
            "rateValue": rate_value,
            "rounding": rounding or "round",
            "totalRounding": total_rounding or "round",
            "calcBasis": calc_basis or "transaction_date",
            "interval": interval or "billing_cycle",
            "settlementType": settlement_type or "manual",
            "enabled": bool(enabled),
        }
        if settlement_days is not None:
            rule["settlementDays"] = settlement_days
        if settlement_month_offset is not None:
            rule["settlementMonthOffset"] = settlement_month_offset
        if settlement_day_of_month is not None:
            rule["settlementDayOfMonth"] = settlement_day_of_month
        if reward_account_id:
            rule["rewardAccountId"] = reward_account_id
        if category_ids_json:
            try:
                category_ids = json.loads(category_ids_json)
                if isinstance(category_ids, list) and category_ids:
                    rule["categoryIds"] = category_ids
            except json.JSONDecodeError:
                pass
        if min_spend_threshold is not None:
            rule["minSpendThreshold"] = min_spend_threshold
        if min_tx_amount is not None:
            rule["minTxAmount"] = min_tx_amount
        if cap_amount is not None:
            rule["capAmount"] = cap_amount
        if cap_shared_key:
            rule["capSharedKey"] = cap_shared_key
        if starts_at is not None:
            rule["startsAt"] = _to_iso_utc(starts_at)
        if ends_at is not None:
            rule["endsAt"] = _to_iso_utc(ends_at)
        if note is not None:
            rule["note"] = note
        card_reward_rules.append(rule)

    return {
        # ledgerSyncId 给 mutator 用 —— 新建预算时要把它写进 budget payload,
        # 让 mobile sync_engine._applyBudgetChange 能解析本地 ledger int id。
        "ledgerSyncId": ledger.external_id,
        "ledgerName": ledger.name or ledger.external_id,
        "currency": ledger.currency or "CNY",
        "monthStartDay": ledger.month_start_day or 1,
        "count": len(items),
        "items": items,
        "accounts": accounts,
        "categories": categories,
        "tags": tags,
        "budgets": budgets,
        "recurringRules": recurring_rules,
        "installmentPlans": installment_plans,
        "installmentPeriods": installment_periods,
        "debts": debts,
        "txTemplates": tx_templates,
        "cardRewardRules": card_reward_rules,
    }


def latest_change_id(db: Session, ledger_id: str) -> int:
    """Ledger 的 latest change_id(任意 entity_type),当作"当前版本号"。"""
    return int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
        )
        or 0
    )
