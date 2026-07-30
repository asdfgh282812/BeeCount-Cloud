"""从 projection 表按需拼装 snapshot dict。

方案 B 里 projection 是权威源,snapshot 不再 runtime 写入。但 mobile 协议
(`/sync/full`)、snapshot_mutator(web write 路径)还吃 snapshot dict 作输入,所以
提供一个按 (ledger_id, max_change_id) 缓存的 builder。

字段 shape 跟原先 mobile push 来的 snapshot 完全对齐 —— mobile 客户端零改动。
"""
from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Ledger,
    ReadBudgetProjection,
    ReadInstallmentPlanProjection,
    ReadRecurringRuleProjection,
    ReadTxProjection,
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
         refund_of_id, installment_plan_id) = row
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
        UserAccountProjection.hidden,
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
        hidden,
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
        # 账户隐藏(issue #240):无条件输出(不像其它扩展字段那样"有值才带
        # key"),与 App serializeAccount 无条件发 hidden 对齐,保 /sync/full
        # 重装 / 新设备首次同步时隐藏标记不丢(03-tech-design-cloud.md §二 (B))。
        acc["hidden"] = bool(hidden)
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
    ).where(ReadRecurringRuleProjection.ledger_id == ledger_id)
    for (sid, tx_type, amount, note, cat_sid, acc_sid, from_sid, to_sid,
         frequency, interval, next_run_at, end_at, enabled) in db.execute(rec_stmt).all():
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
        recurring_rules.append(r)

    # Installment plans(§2.3)
    installment_plans: list[dict[str, Any]] = []
    ins_stmt = select(
        ReadInstallmentPlanProjection.sync_id,
        ReadInstallmentPlanProjection.total_amount,
        ReadInstallmentPlanProjection.periods,
        ReadInstallmentPlanProjection.period_amount,
        ReadInstallmentPlanProjection.first_period_at,
        ReadInstallmentPlanProjection.next_period_at,
        ReadInstallmentPlanProjection.paid_periods,
        ReadInstallmentPlanProjection.account_sync_id,
        ReadInstallmentPlanProjection.category_sync_id,
        ReadInstallmentPlanProjection.note,
        ReadInstallmentPlanProjection.status,
    ).where(ReadInstallmentPlanProjection.ledger_id == ledger_id)
    for (sid, total_amount, periods, period_amount, first_period_at, next_period_at,
         paid_periods, acc_sid, cat_sid, note, status) in db.execute(ins_stmt).all():
        p: dict[str, Any] = {
            "syncId": sid,
            "totalAmount": total_amount,
            "periods": periods,
            "periodAmount": period_amount,
            "firstPeriodAt": _to_iso_utc(first_period_at),
            "nextPeriodAt": _to_iso_utc(next_period_at),
            "paidPeriods": paid_periods,
            "status": status,
        }
        if acc_sid:
            p["accountId"] = acc_sid
        if cat_sid:
            p["categoryId"] = cat_sid
        if note is not None:
            p["note"] = note
        installment_plans.append(p)

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
    }


def latest_change_id(db: Session, ledger_id: str) -> int:
    """Ledger 的 latest change_id(任意 entity_type),当作"当前版本号"。"""
    return int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
        )
        or 0
    )
