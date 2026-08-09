"""SwipeSmart 卡片自動比對(PH14 SD 修訂:§0/§3.3.1(b) 從「刻意不做字串自動
比對」改成「名稱相近/相同時自動對應,不再要求所有使用者都要手動一一勾選」)。

只處理**目前未對照**(`swipesmart_card_id is None`)的信用卡帳戶——已經有值
的一律跳過,不覆蓋(不論那個值是使用者手動選的,還是先前某次自動比對寫入
的,也不論使用者是不是曾經在對照視窗裡刻意選過「未對照」——`swipesmart_
card_id` 欄位本身分不出「從未處理過」跟「使用者刻意清空」這兩種狀態,是
刻意接受的簡化,不為此新增額外欄位)。

比對到超過一張候選卡(名稱包含關係同時命中多張)視為無法判斷,略過交給使用者
在卡片對照視窗手動選,避免猜錯帳戶。

寫入方式跟 `services/card_rewards.py::_ensure_user_global_category`、
`scripts/backfill_recurring_installment_categories.py` 同一套「SyncChange +
`sync_applier.apply_user_change_to_projection`」局部更新寫法——`account` 是
user-global entity(`scope="user"`,不需要 `ledger_id`),這樣其它裝置下次
同步也拉得到這次自動配對的結果,不是直接改 projection 表。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import SyncChange, UserAccountProjection
from ..sync_applier import apply_user_change_to_projection

logger = logging.getLogger(__name__)

_DEVICE_ID = "server-swipesmart-auto-match"
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return _WHITESPACE_RE.sub("", value).strip().lower()


def _is_fuzzy_match(account_name: str, card: dict) -> bool:
    """包含式模糊比對:帳戶名稱 vs 卡片的 `bankName`+`cardName`。任一方整段
    包含另一方,或單看 `cardName`(通常是最有辨識度的部分)互相包含,就算命中。
    """
    norm_account = _normalize(account_name)
    bank = _normalize(card.get("bankName"))
    card_name = _normalize(card.get("cardName"))
    combined = bank + card_name
    if not norm_account or not combined:
        return False
    if combined in norm_account or norm_account in combined:
        return True
    return bool(card_name) and (card_name in norm_account or norm_account in card_name)


def _find_unique_match(account_name: str, cards: list[dict]) -> str | None:
    matches = [c["cardId"] for c in cards if _is_fuzzy_match(account_name, c)]
    if len(matches) != 1:
        return None
    return matches[0]


def auto_match_unmapped_accounts(db: Session, *, user_id: str, cards: list[dict]) -> int:
    """回傳本次新配對成功的帳戶數。呼叫方(貼上 Personal API Key 當下 / 每次
    打開卡片對照視窗)自行決定要不要 `db.commit()`。"""
    if not cards:
        return 0
    accounts = db.scalars(
        select(UserAccountProjection).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.account_type == "credit_card",
            UserAccountProjection.swipesmart_card_id.is_(None),
        )
    ).all()
    if not accounts:
        return 0

    now = datetime.now(timezone.utc)
    matched = 0
    for account in accounts:
        if not account.name:
            continue
        card_id = _find_unique_match(account.name, cards)
        if card_id is None:
            continue
        change = SyncChange(
            user_id=user_id,
            ledger_id=None,
            scope="user",
            entity_type="account",
            entity_sync_id=account.sync_id,
            action="upsert",
            payload_json={"syncId": account.sync_id, "swipesmartCardId": card_id},
            updated_at=now,
            updated_by_device_id=_DEVICE_ID,
            updated_by_user_id=user_id,
        )
        db.add(change)
        db.flush()
        apply_user_change_to_projection(db, user_id=user_id, change=change)
        matched += 1
        logger.info(
            "swipesmart.auto_match user=%s account=%s card=%s", user_id, account.sync_id, card_id,
        )
    return matched
