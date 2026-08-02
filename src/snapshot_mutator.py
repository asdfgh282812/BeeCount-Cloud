from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


def _new_sync_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


_LEGACY_SYNC_ID_PATTERN = re.compile(r"^(tx|acc|cat|tag)_(\d+)_([A-Za-z0-9]+)$")


def _to_iso8601(raw: object) -> str:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc).isoformat()
    if isinstance(raw, str) and raw.strip():
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _date_only_iso8601(raw: object) -> str:
    """借還款到期日(due_at)只存日期,不存時分 —— 先借 `_to_iso8601` 做完整
    的寬鬆時間解析(接受 datetime / 帶時分的字串 / 純日期字串),轉成 UTC
    後 truncate 到當天零點。伺服器端兜底,不完全依賴前端只送日期。"""
    parsed = datetime.fromisoformat(_to_iso8601(raw))
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).isoformat()


def _to_float(raw: object) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _to_optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _to_optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _to_optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


# 扩展字段映射:web/server payload(snake_case) → snapshot(camelCase 跟 mobile
# lib/data/db.dart Account 字段名对齐)。`apply` 只处理 payload 里 explicitly
# 提供的 key —— 这样 update 时不传该字段就不动它,跟旧字段(name/account_type
# /currency/initial_balance)的语义一致。
_ACCOUNT_OPTIONAL_FIELD_MAP: tuple[tuple[str, str, str], ...] = (
    # (payload_key, snapshot_key, kind)
    ("note", "note", "str"),
    ("credit_limit", "creditLimit", "float"),
    ("billing_day", "billingDay", "int"),
    ("payment_due_day", "paymentDueDay", "int"),
    ("bank_name", "bankName", "str"),
    ("card_last_four", "cardLastFour", "str"),
    # 主帳戶(合併帳單,§2.9 Phase 4):子卡指向主卡 syncId;空字串/None=解除掛靠。
    ("parent_account_id", "parentAccountId", "str"),
    # 账户隐藏(issue #240):Web create/update 请求体带 hidden(bool)时才写;
    # 不带 key → 保留原值(不冲掉已有隐藏标记,契约对齐 mobile push 的 merge
    # 缺键保留语义)。
    ("hidden", "hidden", "bool"),
    # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶 syncId。
    ("auto_pay_enabled", "autoPayEnabled", "bool"),
    ("auto_pay_from_account_id", "autoPayFromAccountId", "str"),
    # 帳戶頭像(2026-08-02 補強):空字串/None=移除頭像。
    ("avatar_cloud_file_id", "avatarCloudFileId", "str"),
    ("avatar_cloud_sha256", "avatarCloudSha256", "str"),
)


def _assert_valid_account_parent(
    accounts: list[dict], account_id: str, parent_id: str | None, *, own_type: str | None = None,
) -> None:
    """主帳戶(合併帳單,§2.9 Phase 4,2026-08-02 改版):主帳戶是純管理概念
    (`type == "account_group"`),不是可以自己記交易的獨立帳戶 —— 使用者
    反饋原本設計把「隨便一張信用卡」拿來當主卡不對,銀行/機構本身才是
    「主帳戶」,子帳戶(不限信用卡,銀行帳戶未來也適用)掛靠的目標必須是
    這種群組類型。規則:不能指向自己;目標必須是同一使用者底下真實存在、
    且 `type == "account_group"` 的帳戶;群組帳戶自己不能再掛靠別的群組
    (不支援巢狀);不能形成循環。"""
    if parent_id is None:
        return
    if parent_id == account_id:
        raise ValueError("write validation failed: account cannot be its own parent")
    by_id = {str(row.get("syncId")): row for row in accounts}
    if parent_id not in by_id:
        raise ValueError("write validation failed: parent account not found")
    if by_id[parent_id].get("type") != "account_group":
        raise ValueError("write validation failed: parent account must be an account_group")
    resolved_own_type = own_type if own_type is not None else by_id.get(account_id, {}).get("type")
    if resolved_own_type == "account_group":
        raise ValueError("write validation failed: an account_group cannot itself have a parent (no nested groups)")
    seen = {account_id}
    cursor = parent_id
    for _ in range(len(accounts) + 1):
        if cursor in seen:
            raise ValueError("write validation failed: parent account chain forms a cycle")
        seen.add(cursor)
        next_parent = by_id.get(cursor, {}).get("parentAccountId")
        if not next_parent:
            break
        cursor = str(next_parent)


def _assert_valid_auto_pay_source(
    accounts: list[dict], account_id: str, source_id: str | None,
) -> None:
    """自動扣繳來源帳戶(§2.9,2026-08-04 改版):必須是同一使用者底下真實
    存在的帳戶,不能是這張卡自己(自己扣自己沒有意義),也不能是任何
    account_group(群組沒有自己的資金,不能拿來當扣款來源——跟
    `_assert_account_not_group` 校驗一般交易的 from_account_id 同一個
    道理)。"""
    if not source_id:
        return
    if source_id == account_id:
        raise ValueError(
            "write validation failed: auto_pay_from_account_id must differ from the card itself"
        )
    by_id = {str(row.get("syncId")): row for row in accounts}
    if source_id not in by_id:
        raise ValueError("write validation failed: auto_pay_from_account_id not found")
    if by_id[source_id].get("type") == "account_group":
        raise ValueError(
            "write validation failed: auto_pay_from_account_id cannot be an account_group"
        )


def _apply_account_optional_fields(account: dict, payload: dict) -> None:
    """payload 里如果带这些 key 就写到 snapshot,空字符串 / None 视作 null。

    update 路径调用同一函数:`payload` 不带某 key → 保留原值。带 key 但 value
    是 None / 空串 → 显式清空(对应 mobile 编辑时把 note 清掉的场景)。
    """
    for payload_key, snapshot_key, kind in _ACCOUNT_OPTIONAL_FIELD_MAP:
        if payload_key not in payload:
            continue
        raw = payload.get(payload_key)
        if kind == "float":
            account[snapshot_key] = _to_optional_float(raw)
        elif kind == "int":
            account[snapshot_key] = _to_optional_int(raw)
        elif kind == "bool":
            # hidden 是投影里的 NOT NULL 布尔列,没有"清空"语义 —— 显式带
            # key 但 value 是 None 时按 False(未隐藏)处理,不留 null。
            account[snapshot_key] = bool(raw) if raw is not None else False
        else:
            account[snapshot_key] = _to_optional_str(raw)


def _ensure_list(snapshot: dict, key: str) -> list[dict]:
    raw = snapshot.get(key)
    if not isinstance(raw, list):
        raw = []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    snapshot[key] = out
    return out


def _ensure_sync_id(items: list[dict], prefix: str) -> None:
    for item in items:
        sync_id = item.get("syncId")
        if not isinstance(sync_id, str) or not sync_id.strip():
            item["syncId"] = _new_sync_id(prefix)


def ensure_snapshot_v2(snapshot: dict | None) -> dict:
    target = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    target["ledgerName"] = str(target.get("ledgerName") or "Untitled")
    target["currency"] = str(target.get("currency") or "CNY")

    items = _ensure_list(target, "items")
    accounts = _ensure_list(target, "accounts")
    categories = _ensure_list(target, "categories")
    tags = _ensure_list(target, "tags")

    _ensure_sync_id(items, "tx")
    _ensure_sync_id(accounts, "acc")
    _ensure_sync_id(categories, "cat")
    _ensure_sync_id(tags, "tag")

    for item in items:
        item["type"] = str(item.get("type") or "expense")
        item["amount"] = _to_float(item.get("amount"))
        item["happenedAt"] = _to_iso8601(item.get("happenedAt"))
    for account in accounts:
        account["name"] = str(account.get("name") or "").strip()
        account["type"] = str(account.get("type") or "") or None
        account["currency"] = str(account.get("currency") or "") or None
        if "initialBalance" in account:
            account["initialBalance"] = _to_float(account.get("initialBalance"))
    for category in categories:
        category["name"] = str(category.get("name") or "").strip()
        category["kind"] = str(category.get("kind") or "expense").strip()
    for tag in tags:
        tag["name"] = str(tag.get("name") or "").strip()

    target["count"] = len(items)
    return target


def _legacy_sync_id(sync_id: str) -> tuple[str, int] | None:
    match = _LEGACY_SYNC_ID_PATTERN.fullmatch(sync_id.strip())
    if match is None:
        return None
    prefix, index, _suffix = match.groups()
    return prefix, int(index)


def _find_by_sync_id(
    items: list[dict], sync_id: str, *, expected_prefix: str | None = None
) -> tuple[int, dict]:
    normalized_id = sync_id.strip()
    for idx, item in enumerate(items):
        if str(item.get("syncId")) == normalized_id:
            return idx, item

    legacy = _legacy_sync_id(normalized_id)
    if legacy is not None:
        prefix, legacy_index = legacy
        if (expected_prefix is None or prefix == expected_prefix) and 0 <= legacy_index < len(items):
            fallback_item = items[legacy_index]
            fallback_item["syncId"] = normalized_id
            return legacy_index, fallback_item
    raise KeyError("entity not found")


def _actor_user_id(payload: dict) -> str | None:
    raw = payload.get("__actor_user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _actor_is_admin(payload: dict) -> bool:
    # 单用户隔离:admin 不再拥有"跨用户改别人账本"的权限,这个 helper 保留
    # 只是为了老代码调用点不报错,恒返回 False。
    _ = payload
    return False


def _assert_actor_can_modify(item: dict, payload: dict) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if _actor_is_admin(payload):
        return
    # 共享账本:caller 是该账本的 Owner / Editor(_TRANSACTION_WRITE_ROLES
    # 已在 endpoint 层放行)→ 可以改任何 member 的 tx / category / tag /
    # account / budget。__actor_in_shared_ledger 由 _payload_with_actor
    # 注入,基于 caller 在 LedgerMember 表的存在性 + role。
    if payload.get("__actor_in_shared_ledger") is True:
        return
    created_by = item.get("createdByUserId")
    if isinstance(created_by, str) and created_by.strip() and created_by.strip() != actor_user_id:
        raise PermissionError("write role forbidden: entity owner mismatch")


def _mark_entity_actor(item: dict, payload: dict, *, create: bool) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if create:
        item["createdByUserId"] = actor_user_id
    elif not isinstance(item.get("createdByUserId"), str) or not str(item.get("createdByUserId")).strip():
        item["createdByUserId"] = actor_user_id
    item["updatedByUserId"] = actor_user_id


def _normalize_tx_tags(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        tags = [part.strip() for part in raw.split(",") if part.strip()]
        if not tags:
            return None
        return ",".join(dict.fromkeys(tags))
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            value = str(item).strip()
            if value:
                parts.append(value)
        if not parts:
            return None
        return ",".join(dict.fromkeys(parts))
    return None


def _normalize_tx_splits(raw: object) -> list[dict] | None:
    """拆帳(§2.4):request payload 的 `splits`(snake_case dict 列表,
    `category_id`/`category_name`/`amount`/`note`)→ item 里存的 camelCase
    dict 列表。空/非法输入一律 None(= 不产生 splits key,等同没拆帳)。"""
    if not isinstance(raw, list) or not raw:
        return None
    normalized: list[dict] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        category_id = entry.get("category_id") or entry.get("categoryId")
        if not category_id:
            continue
        split_item: dict[str, object] = {
            "categoryId": str(category_id),
            "amount": _to_float(entry.get("amount")),
            "sortOrder": idx,
        }
        category_name = entry.get("category_name") or entry.get("categoryName")
        if category_name:
            split_item["categoryName"] = str(category_name)
        note = entry.get("note")
        if note:
            split_item["note"] = str(note)
        normalized.append(split_item)
    return normalized or None


def _sort_transactions(snapshot: dict) -> None:
    items = _ensure_list(snapshot, "items")
    items.sort(key=lambda item: _to_iso8601(item.get("happenedAt")), reverse=True)


def create_transaction(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")

    tx_id = _new_sync_id("tx")
    item: dict[str, object] = {
        "syncId": tx_id,
        "type": tx_type,
        "amount": _to_float(payload.get("amount")),
        "happenedAt": _to_iso8601(payload.get("happened_at")),
    }
    # 交易级多币种(0018):Web 币种录入显式传入才写;不传不产生 key
    # (upsert 落 NULL → 统计 COALESCE 回退,旧行为)。
    if payload.get("currency_code") is not None:
        item["currencyCode"] = str(payload.get("currency_code")).upper()
    if payload.get("native_amount") is not None:
        item["nativeAmount"] = _to_float(payload.get("native_amount"))
    if payload.get("note") is not None:
        item["note"] = str(payload.get("note"))
    if payload.get("category_name") is not None:
        item["categoryName"] = str(payload.get("category_name"))
    if payload.get("category_kind") is not None:
        item["categoryKind"] = str(payload.get("category_kind"))
    if payload.get("category_id") is not None:
        item["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_name") is not None:
        item["accountName"] = str(payload.get("account_name"))
    if payload.get("account_id") is not None:
        item["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_name") is not None:
        item["fromAccountName"] = str(payload.get("from_account_name"))
    if payload.get("from_account_id") is not None:
        item["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_name") is not None:
        item["toAccountName"] = str(payload.get("to_account_name"))
    if payload.get("to_account_id") is not None:
        item["toAccountId"] = str(payload.get("to_account_id"))
    tags = _normalize_tx_tags(payload.get("tags"))
    if tags is not None:
        item["tags"] = tags
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids: list[str] = []
        for raw in tag_ids_raw:
            value = str(raw).strip()
            if value and value not in tag_ids:
                tag_ids.append(value)
        if tag_ids:
            item["tagIds"] = tag_ids
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        item["attachments"] = attachments
    # 账单标记(.docs/transaction-flags):snapshot 用 camelCase,跟 mobile
    # serializer + projection.upsert_tx(读 excludeFromStats)对齐。create 默认 False。
    item["excludeFromStats"] = bool(payload.get("exclude_from_stats"))
    item["excludeFromBudget"] = bool(payload.get("exclude_from_budget"))
    if payload.get("refund_of_id") is not None:
        item["refundOfId"] = str(payload.get("refund_of_id"))
    if payload.get("recurring_rule_id") is not None:
        item["recurringRuleId"] = str(payload.get("recurring_rule_id"))
    if payload.get("debt_id") is not None:
        item["debtId"] = str(payload.get("debt_id"))
    splits = _normalize_tx_splits(payload.get("splits"))
    if splits is not None:
        item["splits"] = splits
    _mark_entity_actor(item, payload, create=True)

    _ensure_list(target, "items").append(item)
    # 跳过 _sort_transactions(方案 B):snapshot 不写回,排序徒劳
    target["count"] = len(_ensure_list(target, "items"))
    return target, tx_id


def rescale_native_amount(
    old_amount: float, old_native: float, new_amount: float
) -> float:
    """L14 唯一权威实现:amount 变化时 nativeAmount 的联动规则。

    - 同币种/未折算(old_native == old_amount,隐含汇率 1)→ 跟随新 amount
    - 外币 → 按该笔隐含汇率等比缩放(old_native / old_amount * new_amount),
      保持记账时汇率不漂移
    - old_amount == 0 无法推汇率 → 退化 = 新 amount(1:1,App L11 可捞回)

    调用方:本文件 update_transaction(Web 写路径)与 sync_applier.
    _sync_native_amount_after_merge(旧 App push 路径)。App 端 sync apply 的
    「缺键退化 1:1」是有意的另一规则(旧客户端场景宁可退化让 L11 捞),不共用。
    """
    if old_amount == 0.0 or old_native == old_amount:
        return new_amount
    return old_native / old_amount * new_amount


def update_transaction(snapshot: dict, tx_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    _, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload)

    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        item["type"] = tx_type
    if "amount" in payload:
        new_amount = _to_float(payload.get("amount"))
        # 交易级多币种(L14,.docs/multi-currency-ledger):item 带折算快照
        # (nativeAmount,新 App 记的交易都有)时,改 amount 必须联动,否则
        # 账本统计(读 native_amount)会一直显示旧金额。规则:
        #   同币种/未折算(old_native == old_amount)→ 跟随新 amount;
        #   外币 → 按该笔隐含汇率等比缩放(保持记账时汇率);
        #   old_amount == 0 无法推汇率 → 退化 = 新 amount(1:1)。
        # item 无该 key(旧 App 记的存量交易)→ 不产生,upsert 落 NULL,
        # 统计端 COALESCE 回退新 amount。payload 显式带 native_amount 时
        # 以传入为准(下方统一写入),跳过联动。
        if "nativeAmount" in item and payload.get("native_amount") is None:
            old_amount = _to_float(item.get("amount"))
            old_native = _to_float(item.get("nativeAmount"))
            if new_amount != old_amount:
                item["nativeAmount"] = rescale_native_amount(
                    old_amount, old_native, new_amount)
        item["amount"] = new_amount
    if payload.get("native_amount") is not None:
        # 显式传入优先(Web 折算录入);None = 不变。
        item["nativeAmount"] = _to_float(payload.get("native_amount"))
    if payload.get("currency_code") is not None:
        item["currencyCode"] = str(payload.get("currency_code")).upper()
    if "happened_at" in payload:
        item["happenedAt"] = _to_iso8601(payload.get("happened_at"))

    mapping = {
        "note": "note",
        "category_name": "categoryName",
        "category_kind": "categoryKind",
        "category_id": "categoryId",
        "account_name": "accountName",
        "account_id": "accountId",
        "from_account_name": "fromAccountName",
        "from_account_id": "fromAccountId",
        "to_account_name": "toAccountName",
        "to_account_id": "toAccountId",
    }
    for req_key, snapshot_key in mapping.items():
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                item.pop(snapshot_key, None)
            else:
                item[snapshot_key] = str(value)
    if "tags" in payload:
        raw_tags = payload.get("tags")
        normalized = _normalize_tx_tags(raw_tags)
        logger.info(
            "update_transaction.tags tx_id=%s raw=%r normalized=%r",
            tx_id, raw_tags, normalized,
        )
        if normalized is None:
            item.pop("tags", None)
        else:
            item["tags"] = normalized
    else:
        logger.info("update_transaction.tags tx_id=%s 'tags' key NOT in payload", tx_id)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in tag_ids:
                    tag_ids.append(text)
            if tag_ids:
                item["tagIds"] = tag_ids
            else:
                item.pop("tagIds", None)
        elif raw is None:
            item.pop("tagIds", None)
    if "attachments" in payload:
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            item["attachments"] = attachments
        elif attachments is None:
            item.pop("attachments", None)
    # 账单标记(.docs/transaction-flags):web update 请求里 None = 不变(由
    # exclude_unset 的 payload 控制:不传该 key 就不进 payload)。带显式布尔
    # 才写。snapshot 用 camelCase。
    for req_key, snapshot_key in (
        ("exclude_from_stats", "excludeFromStats"),
        ("exclude_from_budget", "excludeFromBudget"),
    ):
        if req_key in payload and payload.get(req_key) is not None:
            item[snapshot_key] = bool(payload.get(req_key))
    if "refund_of_id" in payload:
        value = payload.get("refund_of_id")
        if value is None or str(value).strip() == "":
            item.pop("refundOfId", None)
        else:
            item["refundOfId"] = str(value)
    # 週期性收支(§2.12.2):單獨編輯/刪除某一期時,呼叫端強制帶
    # recurring_occurrence_overridden=True,之後 update-from / 視窗續產生都要
    # 跳過這筆,不能被批次覆蓋。
    if "recurring_occurrence_overridden" in payload:
        item["recurringOccurrenceOverridden"] = bool(
            payload.get("recurring_occurrence_overridden")
        )
    if "debt_id" in payload:
        value = payload.get("debt_id")
        if value is None or str(value).strip() == "":
            item.pop("debtId", None)
        else:
            item["debtId"] = str(value)
    if "splits" in payload:
        splits = _normalize_tx_splits(payload.get("splits"))
        if splits is None:
            item.pop("splits", None)
        else:
            item["splits"] = splits
    _mark_entity_actor(item, payload, create=False)

    # 方案 B 后 snapshot 不写回 DB,items 排序只对 mutator 内部无意义 → 跳过(原 30ms/5k)。
    # projection 读路径走 SQL ORDER BY,顺序由 index 保证。
    target["count"] = len(items)
    return target


def delete_transaction(snapshot: dict, tx_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    idx, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload or {})
    items.pop(idx)
    # 方案 B 后 snapshot 不写回 DB,items 排序只对 mutator 内部无意义 → 跳过(原 30ms/5k)。
    # projection 读路径走 SQL ORDER BY,顺序由 index 保证。
    target["count"] = len(items)
    return target


def _normalize_name(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("write validation failed: name is required")
    return value


def create_account(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in accounts):
        raise ValueError("write validation failed: duplicated account name")
    sync_id = _new_sync_id("acc")
    account = {
        "syncId": sync_id,
        "name": name,
        "type": str(payload.get("account_type") or "") or None,
        "currency": str(payload.get("currency") or "") or None,
        "initialBalance": _to_float(payload.get("initial_balance")),
    }
    # 扩展字段:跟 mobile lib/data/db.dart Account 表 schema 对齐(driftCamel:
    # creditLimit / billingDay / paymentDueDay / bankName / cardLastFour /
    # note)。前端 web 字段是 snake_case,这里转 camelCase 写入 snapshot。
    _apply_account_optional_fields(account, payload)
    if "parent_account_id" in payload:
        _assert_valid_account_parent(
            accounts, sync_id, _to_optional_str(account.get("parentAccountId")),
            own_type=account.get("type"),
        )
    if "auto_pay_from_account_id" in payload:
        _assert_valid_auto_pay_source(
            accounts, sync_id, _to_optional_str(account.get("autoPayFromAccountId")),
        )
    _mark_entity_actor(account, payload, create=True)
    accounts.append(account)
    return target, sync_id


def update_account(snapshot: dict, account_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    _, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload)
    old_name = str(account.get("name") or "").strip()

    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != account_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in accounts
        ):
            raise ValueError("write validation failed: duplicated account name")
        account["name"] = new_name
    if "account_type" in payload:
        value = payload.get("account_type")
        account["type"] = str(value) if value else None
    if "currency" in payload:
        value = payload.get("currency")
        account["currency"] = str(value) if value else None
    if "initial_balance" in payload:
        account["initialBalance"] = _to_float(payload.get("initial_balance"))
    _apply_account_optional_fields(account, payload)
    if "parent_account_id" in payload:
        _assert_valid_account_parent(
            accounts, account_id, _to_optional_str(account.get("parentAccountId")),
            own_type=account.get("type"),
        )
    if "auto_pay_from_account_id" in payload:
        _assert_valid_auto_pay_source(
            accounts, account_id, _to_optional_str(account.get("autoPayFromAccountId")),
        )

    new_name = str(account.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx["accountName"] = new_name
            if tx.get("fromAccountName") == old_name:
                tx["fromAccountName"] = new_name
            if tx.get("toAccountName") == old_name:
                tx["toAccountName"] = new_name
    _mark_entity_actor(account, payload, create=False)
    return target


def delete_account(snapshot: dict, account_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    idx, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload or {})
    old_name = str(account.get("name") or "").strip()
    # 主帳戶(§2.9 Phase 4):群組帳戶還有子帳戶掛靠時拒絕刪除,避免子帳戶
    # parent_account_id 變成查無此帳戶的孤儿引用(billing-summary 之類的讀
    # 路径会直接 404)。跟下面"有关联交易拒绝删除"同一个"不要 orphan"原则。
    children = sum(1 for row in accounts if str(row.get("parentAccountId") or "") == account_id)
    if children > 0:
        raise ValueError(
            "write validation failed: account_group has linked child accounts; "
            f"unlink or delete the {children} child accounts first"
        )
    # 安全检查:任何关联交易都拒绝删除(用户决定:不要 warn-and-orphan 模式)。
    # 客户端必须先把交易改/删/迁走,账户的 tx_count 回到 0 才允许删。
    # mobile 自己走 sync_applier 路径不经过 snapshot_mutator,这条 guard 只对
    # web write API 生效;mobile 现有行为(orphan)保留不变。
    if old_name:
        linked = sum(
            1
            for tx in _ensure_list(target, "items")
            if (
                tx.get("accountName") == old_name
                or tx.get("fromAccountName") == old_name
                or tx.get("toAccountName") == old_name
            )
        )
        if linked > 0:
            raise ValueError(
                "write validation failed: account has linked transactions; "
                f"reassign or delete the {linked} transactions first"
            )
    accounts.pop(idx)
    if old_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx.pop("accountName", None)
            if tx.get("fromAccountName") == old_name:
                tx.pop("fromAccountName", None)
            if tx.get("toAccountName") == old_name:
                tx.pop("toAccountName", None)
    return target


def create_category(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    name = _normalize_name(payload.get("name"))
    kind = str(payload.get("kind") or "expense").strip()
    if kind not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid category kind")
    if any(
        str(row.get("name", "")).strip().lower() == name.lower()
        and str(row.get("kind", "")).strip() == kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")
    sync_id = _new_sync_id("cat")
    category = {
        "syncId": sync_id,
        "name": name,
        "kind": kind,
        "level": payload.get("level"),
        "sortOrder": payload.get("sort_order"),
        "icon": payload.get("icon"),
        "iconType": payload.get("icon_type"),
        "customIconPath": payload.get("custom_icon_path"),
        "iconCloudFileId": payload.get("icon_cloud_file_id"),
        "iconCloudSha256": payload.get("icon_cloud_sha256"),
        "parentName": payload.get("parent_name"),
    }
    _mark_entity_actor(category, payload, create=True)
    categories.append(category)
    return target, sync_id


def update_category(snapshot: dict, category_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    _, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload)
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()

    if "name" in payload:
        category["name"] = _normalize_name(payload.get("name"))
    if "kind" in payload:
        kind = str(payload.get("kind") or "").strip()
        if kind not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid category kind")
        category["kind"] = kind
    for req_key, snapshot_key in [
        ("level", "level"),
        ("sort_order", "sortOrder"),
        ("icon", "icon"),
        ("icon_type", "iconType"),
        ("custom_icon_path", "customIconPath"),
        ("icon_cloud_file_id", "iconCloudFileId"),
        ("icon_cloud_sha256", "iconCloudSha256"),
        ("parent_name", "parentName"),
    ]:
        if req_key in payload:
            category[snapshot_key] = payload.get(req_key)

    new_name = str(category.get("name") or "").strip()
    new_kind = str(category.get("kind") or "").strip()
    if any(
        str(row.get("syncId")) != category_id
        and str(row.get("name", "")).strip().lower() == new_name.lower()
        and str(row.get("kind", "")).strip() == new_kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")

    if old_name and old_kind and (old_name != new_name or old_kind != new_kind):
        for tx in _ensure_list(target, "items"):
            if tx.get("categoryName") == old_name and tx.get("categoryKind") == old_kind:
                tx["categoryName"] = new_name
                tx["categoryKind"] = new_kind
    _mark_entity_actor(category, payload, create=False)
    return target


def delete_category(snapshot: dict, category_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    idx, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload or {})
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()
    # 严格策略(跟 AccountsPage / mobile 对齐):有子分类或关联交易时拒绝删除,
    # 要求用户先迁移这些数据。比"允许删除并 orphan"安全 — 避免误删导致一堆
    # 无主交易污染 ledger。前端也有同款拦截,这里是兜底服务端校验防止旧客户
    # 端 / 直接 API 调用绕过。
    if old_name and old_kind:
        child_count = sum(
            1
            for row in categories
            if str(row.get("syncId") or "") != category_id
            and str(row.get("parentName") or "").strip() == old_name
            and str(row.get("kind") or "").strip() == old_kind
        )
        if child_count > 0:
            raise ValueError(
                f"write validation failed: category has {child_count} child categories"
            )
        tx_count = sum(
            1
            for tx in _ensure_list(target, "items")
            if tx.get("categoryName") == old_name
            and tx.get("categoryKind") == old_kind
        )
        if tx_count > 0:
            raise ValueError(
                f"write validation failed: category has {tx_count} transactions"
            )
    categories.pop(idx)
    return target


def _split_tags(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _join_tags(tags: list[str]) -> str | None:
    if not tags:
        return None
    return ",".join(dict.fromkeys(tags))


def create_tag(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in tags):
        raise ValueError("write validation failed: duplicated tag")
    sync_id = _new_sync_id("tag")
    item = {"syncId": sync_id, "name": name, "color": payload.get("color")}
    _mark_entity_actor(item, payload, create=True)
    tags.append(item)
    return target, sync_id


def update_tag(snapshot: dict, tag_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    _, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload)
    old_name = str(tag.get("name") or "").strip()
    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != tag_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in tags
        ):
            raise ValueError("write validation failed: duplicated tag")
        tag["name"] = new_name
    if "color" in payload:
        tag["color"] = payload.get("color")

    new_name = str(tag.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            tx_tags = _split_tags(tx.get("tags"))
            if not tx_tags:
                continue
            updated = [new_name if tag_name == old_name else tag_name for tag_name in tx_tags]
            merged = _join_tags(updated)
            if merged is None:
                tx.pop("tags", None)
            else:
                tx["tags"] = merged
    _mark_entity_actor(tag, payload, create=False)
    return target


def delete_tag(snapshot: dict, tag_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    idx, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload or {})
    old_name = str(tag.get("name") or "").strip()

    # 拦截关联交易:有交易引用此 tag 时禁止删除,让用户先把标签从交易里
    # 摘掉(或删交易)再来删标签。之前是"静默把 tag 从所有引用它的 tx
    # 里抽走",数据上可恢复但用户无感知,跟 app 行为(确认对话框 + 阻止)
    # 不一致,容易误删。
    # ValueError 由路由层抓出来翻译成 4xx 响应,error message 走 i18n。
    if old_name:
        in_use = sum(
            1
            for tx in _ensure_list(target, "items")
            if old_name in _split_tags(tx.get("tags"))
        )
        if in_use > 0:
            raise ValueError(
                f"write validation failed: tag has {in_use} linked transactions"
            )

    tags.pop(idx)
    return target


# ============================================================================
# Budgets —— 跟 mobile lib/data/db.dart Budget 表对齐:type / categoryId /
# amount / period / startDay / enabled。snapshot 用 driftCamel(categoryId,
# startDay)。type='total' 在每个账本只允许一条;'category' 同一 categoryId
# 也只允许一条(对应 mobile budget_edit_page._saveBudget 的 unique check)。
# ============================================================================


def _normalize_budget_period(raw: object) -> str:
    """空 / 无效时回退到 'monthly'(跟 mobile budget_repository 的 default 一致)。"""
    s = str(raw or "").strip().lower()
    if s in ("monthly", "weekly", "yearly"):
        return s
    return "monthly"


def _normalize_budget_type(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s == "category":
        return "category"
    return "total"


def create_budget(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    btype = _normalize_budget_type(payload.get("type"))
    category_id = _to_optional_str(payload.get("category_id"))
    period = _normalize_budget_period(payload.get("period"))
    # 唯一性:total 只一条;category 按 categoryId 唯一(对齐 mobile)。
    if btype == "total":
        if any(_normalize_budget_type(row.get("type")) == "total" for row in budgets):
            raise ValueError("write validation failed: total budget already exists")
    else:
        if not category_id:
            raise ValueError("write validation failed: category budget requires category_id")
        if any(
            _normalize_budget_type(row.get("type")) == "category"
            and str(row.get("categoryId") or "") == category_id
            for row in budgets
        ):
            raise ValueError("write validation failed: category budget already exists")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: budget amount must be > 0")
    start_day = _to_optional_int(payload.get("start_day"))
    if start_day is None:
        start_day = 1
    if start_day < 1 or start_day > 28:
        raise ValueError("write validation failed: start_day out of range")
    sync_id = _new_sync_id("bgt")
    enabled_raw = payload.get("enabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True
    # ledgerSyncId 必须显式带,mobile _applyBudgetChange 用它解析本地 ledger id;
    # 不带则 mobile 永远 skip 这条 change。
    ledger_sync_id = _to_optional_str(target.get("ledgerSyncId")) or _to_optional_str(
        payload.get("ledger_sync_id")
    )
    budget = {
        "syncId": sync_id,
        "type": btype,
        "categoryId": category_id if btype == "category" else None,
        "amount": amount,
        "period": period,
        "startDay": start_day,
        "enabled": enabled,
    }
    if ledger_sync_id:
        budget["ledgerSyncId"] = ledger_sync_id
    _mark_entity_actor(budget, payload, create=True)
    budgets.append(budget)
    return target, sync_id


def update_budget(snapshot: dict, budget_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    _, budget = _find_by_sync_id(budgets, budget_id, expected_prefix="bgt")
    _assert_actor_can_modify(budget, payload)
    # 历史 budget(snapshot_builder 从 projection 重建已经带 ledgerSyncId,但
    # 老 SyncChange 里的 payload 可能没带)被 update 时,补齐 ledgerSyncId,
    # 否则 mobile 收到这条 update change 还是因为缺 ledgerSyncId 直接 skip。
    if "ledgerSyncId" not in budget:
        ledger_sync_id = _to_optional_str(target.get("ledgerSyncId"))
        if ledger_sync_id:
            budget["ledgerSyncId"] = ledger_sync_id
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: budget amount must be > 0")
        budget["amount"] = amount
    if "period" in payload:
        budget["period"] = _normalize_budget_period(payload.get("period"))
    if "start_day" in payload:
        start_day = _to_optional_int(payload.get("start_day"))
        if start_day is None:
            start_day = 1
        if start_day < 1 or start_day > 28:
            raise ValueError("write validation failed: start_day out of range")
        budget["startDay"] = start_day
    if "enabled" in payload:
        budget["enabled"] = bool(payload.get("enabled"))
    # 不允许改 type 和 categoryId(语义混乱:从 total 改成 category 等于
    # 删一条新建一条,UI 走删除 + 新建路径更直观)。
    _mark_entity_actor(budget, payload, create=False)
    return target


def delete_budget(snapshot: dict, budget_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    idx, budget = _find_by_sync_id(budgets, budget_id, expected_prefix="bgt")
    _assert_actor_can_modify(budget, payload or {})
    budgets.pop(idx)
    return target


# ============================================================================
# Recurring rules(§2.2)—— 跟 budget 同款 boilerplate。snapshot 用
# driftCamel:txType/categoryId/accountId/fromAccountId/toAccountId/frequency/
# interval/nextRunAt/endAt/enabled。
# ============================================================================


def next_run_from(now: datetime, frequency: str, interval: int) -> datetime:
    """算下一次 next_run_at:frequency 单位 * interval。月/年用日历推进(处理
    月末溢出:借用 `add_months` 风格,31 号 + 1 月 → 2 月最后一天,不进 3 月)。"""
    interval = max(1, int(interval or 1))
    if frequency == "daily":
        return now + timedelta(days=interval)
    if frequency == "weekly":
        return now + timedelta(weeks=interval)
    if frequency == "yearly":
        return add_months(now, interval * 12)
    return add_months(now, interval)  # monthly(默认兜底)


def add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    import calendar

    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def create_recurring_rule(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: amount must be > 0")
    frequency = str(payload.get("frequency") or "monthly")
    if frequency not in {"daily", "weekly", "monthly", "yearly"}:
        raise ValueError("write validation failed: invalid frequency")
    next_run_at = payload.get("next_run_at")
    if next_run_at is None:
        raise ValueError("write validation failed: next_run_at is required")
    sync_id = _new_sync_id("rec")
    rule: dict[str, object] = {
        "syncId": sync_id,
        "txType": tx_type,
        "amount": amount,
        "frequency": frequency,
        "interval": _to_optional_int(payload.get("interval")) or 1,
        "nextRunAt": _to_iso8601(next_run_at),
        "enabled": bool(payload.get("enabled")) if payload.get("enabled") is not None else True,
    }
    if payload.get("note") is not None:
        rule["note"] = str(payload.get("note"))
    if payload.get("category_id") is not None:
        rule["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_id") is not None:
        rule["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_id") is not None:
        rule["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_id") is not None:
        rule["toAccountId"] = str(payload.get("to_account_id"))
    if payload.get("end_at") is not None:
        rule["endAt"] = _to_iso8601(payload.get("end_at"))
    # Phase 1.5(§2.12.2):进阶规则(snapshot 层保持嵌套 dict,序列化成 JSON
    # 字串是 projection.py upsert 时才做的事,跟 attachments/tagIds 同一套
    # "snapshot 存原生结构,projection 落库时才 json.dumps"惯例)+ 视窗生成
    # 进度,建规则的 write endpoint 用
    # recurring_schedule.plan_initial_generation 算出这次批次生成到哪里,
    # 一起传进来跟规则本身一次建好,不用建完再补一次 update。
    if payload.get("advanced_rule_json") is not None:
        rule["advancedRuleJson"] = payload.get("advanced_rule_json")
    if payload.get("generated_until_at") is not None:
        rule["generatedUntilAt"] = _to_iso8601(payload.get("generated_until_at"))
    _mark_entity_actor(rule, payload, create=True)
    rules.append(rule)
    return target, sync_id


def update_recurring_rule(snapshot: dict, rule_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    _, rule = _find_by_sync_id(rules, rule_id, expected_prefix="rec")
    _assert_actor_can_modify(rule, payload)

    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        rule["txType"] = tx_type
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: amount must be > 0")
        rule["amount"] = amount
    if "frequency" in payload:
        frequency = str(payload.get("frequency") or "")
        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValueError("write validation failed: invalid frequency")
        rule["frequency"] = frequency
    if "interval" in payload:
        rule["interval"] = _to_optional_int(payload.get("interval")) or 1
    if "next_run_at" in payload and payload.get("next_run_at") is not None:
        rule["nextRunAt"] = _to_iso8601(payload.get("next_run_at"))
    if "end_at" in payload:
        value = payload.get("end_at")
        if value is None:
            rule.pop("endAt", None)
        else:
            rule["endAt"] = _to_iso8601(value)
    if "enabled" in payload:
        rule["enabled"] = bool(payload.get("enabled"))
    for req_key, snapshot_key in (
        ("note", "note"),
        ("category_id", "categoryId"),
        ("account_id", "accountId"),
        ("from_account_id", "fromAccountId"),
        ("to_account_id", "toAccountId"),
    ):
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                rule.pop(snapshot_key, None)
            else:
                rule[snapshot_key] = str(value)
    if "advanced_rule_json" in payload:
        value = payload.get("advanced_rule_json")
        if value is None:
            rule.pop("advancedRuleJson", None)
        else:
            rule["advancedRuleJson"] = value
    if "generated_until_at" in payload:
        value = payload.get("generated_until_at")
        if value is None:
            rule.pop("generatedUntilAt", None)
        else:
            rule["generatedUntilAt"] = _to_iso8601(value)
    _mark_entity_actor(rule, payload, create=False)
    return target


def delete_recurring_rule(snapshot: dict, rule_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    idx, rule = _find_by_sync_id(rules, rule_id, expected_prefix="rec")
    _assert_actor_can_modify(rule, payload or {})
    rules.pop(idx)
    return target


# ============================================================================
# Installment plans(§2.3 / Phase 1.5 修正版 §2.12.1)—— 建计画时算好攤還
# 参数(repaymentMethod/interestPeriod/interestRate/roundAmounts/
# remainderPosition/gracePeriodMonths)存进这个实体,但**不**在这里跑攤還
# 演算法、也不生成任何 tx/period ——那需要 services.installment_amortization
# + 同时写 read_tx_projection + read_installment_period_projection,
# snapshot_mutator 只管这一个实体的字段。全部期数的生成放在 write endpoint
# 里跟计画一起提交(同一个 _commit_write 事务),见 routers/write/
# installment_plans.py。periodAmount/nextPeriodAt/paidPeriods 这三个字段
# Phase 1.5 之后只是历史相容占位(不再被任何排程更新),真正的每期明细以
# read_installment_period_projection 为准,读路径(snapshot_builder.build /
# read/ledgers.py)从 period 行即时算出展示用的 paidPeriods/nextPeriodAt,
# 不读这里存的值。
# ============================================================================


def create_installment_plan(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    total_amount = _to_optional_float(payload.get("total_amount"))
    if total_amount is None or total_amount <= 0:
        raise ValueError("write validation failed: total_amount must be > 0")
    periods = _to_optional_int(payload.get("periods"))
    if periods is None or periods < 1:
        raise ValueError("write validation failed: periods must be >= 1")
    first_period_at = payload.get("first_period_at")
    if first_period_at is None:
        raise ValueError("write validation failed: first_period_at is required")
    first_dt = datetime.fromisoformat(_to_iso8601(first_period_at))

    repayment_method = str(payload.get("repayment_method") or "equal_principal")
    if repayment_method not in {"equal_installment", "equal_principal", "fixed_interest"}:
        raise ValueError("write validation failed: invalid repayment_method")
    interest_period = str(payload.get("interest_period") or "monthly")
    if interest_period not in {"monthly", "daily"}:
        raise ValueError("write validation failed: invalid interest_period")
    interest_rate = _to_optional_float(payload.get("interest_rate"))
    if interest_rate is None:
        interest_rate = 0.0
    if interest_rate < 0:
        raise ValueError("write validation failed: interest_rate must be >= 0")
    round_amounts = (
        bool(payload.get("round_amounts")) if payload.get("round_amounts") is not None else True
    )
    remainder_position = str(payload.get("remainder_position") or "last")
    if remainder_position not in {"first", "last"}:
        raise ValueError("write validation failed: invalid remainder_position")
    grace_period_months = _to_optional_int(payload.get("grace_period_months"))
    if grace_period_months is None:
        grace_period_months = 0
    if not (0 <= grace_period_months < periods):
        raise ValueError(
            "write validation failed: grace_period_months must be >= 0 and < periods"
        )

    period_amount = round(total_amount / periods, 2)
    sync_id = _new_sync_id("ins")
    plan: dict[str, object] = {
        "syncId": sync_id,
        "totalAmount": total_amount,
        "periods": periods,
        "periodAmount": period_amount,
        "firstPeriodAt": _to_iso8601(first_period_at),
        "nextPeriodAt": _to_iso8601(add_months(first_dt, 1)),
        "paidPeriods": 1,
        "status": "active",
        "repaymentMethod": repayment_method,
        "interestPeriod": interest_period,
        "interestRate": interest_rate,
        "roundAmounts": round_amounts,
        "remainderPosition": remainder_position,
        "gracePeriodMonths": grace_period_months,
    }
    if payload.get("account_id") is not None:
        plan["accountId"] = str(payload.get("account_id"))
    if payload.get("category_id") is not None:
        plan["categoryId"] = str(payload.get("category_id"))
    if payload.get("note") is not None:
        plan["note"] = str(payload.get("note"))
    if payload.get("offset_breakdown"):
        plan["offsetBreakdownJson"] = json.dumps(payload.get("offset_breakdown"))
    _mark_entity_actor(plan, payload, create=True)
    plans.append(plan)
    return target, sync_id


def update_installment_plan(snapshot: dict, plan_id: str, payload: dict) -> dict:
    """`note`/`status` 是 plain PATCH 端点(`WriteInstallmentPlanUpdateRequest`)
    唯一暴露的字段 —— 金额/期数/攤還参数不给直接改,语义混乱(等同删了重建)。
    `interest_rate`/`repayment_method` 两个 key 只有 §2.12.1 的
    `rebalance-from` 端点内部会传(它自己的 request schema
    `WriteInstallmentRebalanceRequest` 不长这样,是 endpoint 手工组 payload
    调这里),用来把新利率/攤還方式记录回 plan 实体供之后展示/再次 rebalance
    用,不是让 client 能直接打这个字段。"""
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    _, plan = _find_by_sync_id(plans, plan_id, expected_prefix="ins")
    _assert_actor_can_modify(plan, payload)
    if "note" in payload:
        value = payload.get("note")
        if value is None or str(value).strip() == "":
            plan.pop("note", None)
        else:
            plan["note"] = str(value)
    if "status" in payload and payload.get("status") is not None:
        status = str(payload.get("status"))
        if status not in {"active", "settled", "terminated"}:
            raise ValueError("write validation failed: invalid status")
        plan["status"] = status
    if "interest_rate" in payload and payload.get("interest_rate") is not None:
        plan["interestRate"] = _to_float(payload.get("interest_rate"))
    if "repayment_method" in payload and payload.get("repayment_method") is not None:
        method = str(payload.get("repayment_method"))
        if method not in {"equal_installment", "equal_principal", "fixed_interest"}:
            raise ValueError("write validation failed: invalid repayment_method")
        plan["repaymentMethod"] = method
    _mark_entity_actor(plan, payload, create=False)
    return target


def delete_installment_plan(snapshot: dict, plan_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    idx, plan = _find_by_sync_id(plans, plan_id, expected_prefix="ins")
    _assert_actor_can_modify(plan, payload or {})
    plans.pop(idx)
    return target


# ============================================================================
# Installment periods(§2.12.1 Phase 1.5 新增)—— 每期本金/利息/合计明细。
# 只由 server 端写入口(建计画 / rebalance-from / early-repay-principal /
# payoff / terminate-future)生成,不接受 client 直接 POST 单笔 period,但
# 仍走完整 sync entity 六步(需要跨装置可见)。跟 budget/recurring_rule 同款
# boilerplate,snapshot 用 driftCamel:planId/periodNo/dueAt/principalAmount/
# interestAmount/totalAmount/status/txId。
# ============================================================================


def create_installment_period(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    plan_id = payload.get("plan_id")
    if not plan_id:
        raise ValueError("write validation failed: plan_id is required")
    period_no = _to_optional_int(payload.get("period_no"))
    if period_no is None or period_no < 1:
        raise ValueError("write validation failed: period_no must be >= 1")
    due_at = payload.get("due_at")
    if due_at is None:
        raise ValueError("write validation failed: due_at is required")
    sync_id = _new_sync_id("insp")
    period: dict[str, object] = {
        "syncId": sync_id,
        "planId": str(plan_id),
        "periodNo": period_no,
        "dueAt": _to_iso8601(due_at),
        "principalAmount": _to_float(payload.get("principal_amount")),
        "interestAmount": _to_float(payload.get("interest_amount")),
        "totalAmount": _to_float(payload.get("total_amount")),
        "status": str(payload.get("status") or "generated"),
    }
    if payload.get("tx_id") is not None:
        period["txId"] = str(payload.get("tx_id"))
    _mark_entity_actor(period, payload, create=True)
    periods_list.append(period)
    return target, sync_id


def update_installment_period(snapshot: dict, period_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    _, period = _find_by_sync_id(periods_list, period_id, expected_prefix="insp")
    _assert_actor_can_modify(period, payload)
    if "due_at" in payload and payload.get("due_at") is not None:
        period["dueAt"] = _to_iso8601(payload.get("due_at"))
    if "principal_amount" in payload and payload.get("principal_amount") is not None:
        period["principalAmount"] = _to_float(payload.get("principal_amount"))
    if "interest_amount" in payload and payload.get("interest_amount") is not None:
        period["interestAmount"] = _to_float(payload.get("interest_amount"))
    if "total_amount" in payload and payload.get("total_amount") is not None:
        period["totalAmount"] = _to_float(payload.get("total_amount"))
    if "status" in payload and payload.get("status") is not None:
        status = str(payload.get("status"))
        if status not in {"pending", "generated", "overridden", "refunded"}:
            raise ValueError("write validation failed: invalid status")
        period["status"] = status
    if "tx_id" in payload and payload.get("tx_id") is not None:
        period["txId"] = str(payload.get("tx_id"))
    _mark_entity_actor(period, payload, create=False)
    return target


def delete_installment_period(snapshot: dict, period_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    idx, period = _find_by_sync_id(periods_list, period_id, expected_prefix="insp")
    _assert_actor_can_modify(period, payload or {})
    periods_list.pop(idx)
    return target


# ============================================================================
# 借還款追蹤(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3)—— 跟 budget 同款
# boilerplate。`principalAmount` 建立后不可改(见 ReadDebtProjection
# docstring),PATCH 只暴露 counterpartyName/dueAt/note。remaining_amount/
# status 不是这个实体的字段,读路径从反查交易 derive。
# ============================================================================


def create_debt(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    direction = str(payload.get("direction") or "")
    if direction not in {"payable", "receivable"}:
        raise ValueError("write validation failed: invalid direction")
    counterparty_name = _normalize_name(payload.get("counterparty_name"))
    principal_amount = _to_optional_float(payload.get("principal_amount"))
    if principal_amount is None or principal_amount <= 0:
        raise ValueError("write validation failed: principal_amount must be > 0")
    sync_id = _new_sync_id("debt")
    debt: dict[str, object] = {
        "syncId": sync_id,
        "direction": direction,
        "counterpartyName": counterparty_name,
        "principalAmount": principal_amount,
    }
    if payload.get("due_at") is not None:
        debt["dueAt"] = _date_only_iso8601(payload.get("due_at"))
    if payload.get("note") is not None:
        debt["note"] = str(payload.get("note"))
    _mark_entity_actor(debt, payload, create=True)
    debts.append(debt)
    return target, sync_id


def update_debt(snapshot: dict, debt_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    _, debt = _find_by_sync_id(debts, debt_id, expected_prefix="debt")
    _assert_actor_can_modify(debt, payload)
    if "counterparty_name" in payload:
        debt["counterpartyName"] = _normalize_name(payload.get("counterparty_name"))
    if "due_at" in payload:
        value = payload.get("due_at")
        if value is None:
            debt.pop("dueAt", None)
        else:
            debt["dueAt"] = _date_only_iso8601(value)
    if "note" in payload:
        value = payload.get("note")
        if value is None or str(value).strip() == "":
            debt.pop("note", None)
        else:
            debt["note"] = str(value)
    if "closed_at" in payload:
        value = payload.get("closed_at")
        if value is None:
            debt.pop("closedAt", None)
        else:
            debt["closedAt"] = _to_iso8601(value)
    _mark_entity_actor(debt, payload, create=False)
    return target


def delete_debt(snapshot: dict, debt_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    idx, debt = _find_by_sync_id(debts, debt_id, expected_prefix="debt")
    _assert_actor_can_modify(debt, payload or {})
    debts.pop(idx)
    return target


# ============================================================================
# 交易範本(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)—— 跟 budget/tag 同款
# boilerplate,唯一多一步是 `POST .../apply` 端点(routers/write/
# tx_templates.py)另外调 `create_transaction` 把範本内容套成一笔新交易,
# 不在这个模块处理。
# ============================================================================


def create_tx_template(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    name = _normalize_name(payload.get("name"))
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: amount must be > 0")
    sync_id = _new_sync_id("tpl")
    template: dict[str, object] = {
        "syncId": sync_id,
        "name": name,
        "txType": tx_type,
        "amount": amount,
        "sortOrder": _to_optional_int(payload.get("sort_order")) or 0,
    }
    if payload.get("note") is not None:
        template["note"] = str(payload.get("note"))
    if payload.get("category_id") is not None:
        template["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_id") is not None:
        template["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_id") is not None:
        template["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_id") is not None:
        template["toAccountId"] = str(payload.get("to_account_id"))
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids = [str(v).strip() for v in tag_ids_raw if str(v).strip()]
        if tag_ids:
            template["tagIds"] = tag_ids
    _mark_entity_actor(template, payload, create=True)
    templates.append(template)
    return target, sync_id


def update_tx_template(snapshot: dict, template_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    _, template = _find_by_sync_id(templates, template_id, expected_prefix="tpl")
    _assert_actor_can_modify(template, payload)
    if "name" in payload:
        template["name"] = _normalize_name(payload.get("name"))
    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        template["txType"] = tx_type
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: amount must be > 0")
        template["amount"] = amount
    if "sort_order" in payload and payload.get("sort_order") is not None:
        template["sortOrder"] = _to_optional_int(payload.get("sort_order")) or 0
    for req_key, snapshot_key in (
        ("note", "note"),
        ("category_id", "categoryId"),
        ("account_id", "accountId"),
        ("from_account_id", "fromAccountId"),
        ("to_account_id", "toAccountId"),
    ):
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                template.pop(snapshot_key, None)
            else:
                template[snapshot_key] = str(value)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids = [str(v).strip() for v in raw if str(v).strip()]
            if tag_ids:
                template["tagIds"] = tag_ids
            else:
                template.pop("tagIds", None)
        elif raw is None:
            template.pop("tagIds", None)
    _mark_entity_actor(template, payload, create=False)
    return target


def delete_tx_template(snapshot: dict, template_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    idx, template = _find_by_sync_id(templates, template_id, expected_prefix="tpl")
    _assert_actor_can_modify(template, payload or {})
    templates.pop(idx)
    return target
