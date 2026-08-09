"""SwipeSmart2 外部 API 客戶端(Phase 14,docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md
+ docs/EXTERNAL_INTEGRATION_SPEC.md)。

風格比照 `services/exchange_rate/fetcher.py`/`services/ai/provider_client.py`
的既有慣例:每次呼叫各自建立 `httpx.AsyncClient`,逾時/失敗一律吞掉、記錄、
回傳「空」值,絕不讓外部服務的可用性拖垮 BeeCount 自己的核心記帳流程
(EXTERNAL_INTEGRATION_SPEC.md §5.2 的硬性容錯要求)。

`SWIPESMART_BASE_URL` 空字串 = 功能整體停用,所有函式直接短路回傳空值,不
嘗試連線。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# 卡片目錄(GET /api/cards)是 SwipeSmart 使用者全域共用資料(不分呼叫方是誰),
# 建議 TTL 快取(EXTERNAL_INTEGRATION_SPEC.md §4.2),1 小時。單一全域快取項,
# 用 asyncio.Lock 防同時多個請求都打上游(比照 exchange_rate fetcher 的
# 並發防擊穿模式)。
_CARDS_CACHE_TTL_SECONDS = 3600.0
_cards_cache: dict[str, Any] = {"data": None, "fetched_at": 0.0}
_cards_lock = asyncio.Lock()


def _headers(api_key: str) -> dict[str, str]:
    return {"X-Service-Api-Key": api_key, "Content-Type": "application/json"}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(get_settings().swipesmart_timeout)


def _base_url() -> str:
    return get_settings().swipesmart_base_url.rstrip("/")


async def get_cards(api_key: str) -> list[dict]:
    """GET /api/cards,全域 TTL 快取。任何呼叫方的 key 都能刷新快取(資料
    本身跟呼叫者身分無關)。失敗時若有舊快取就回舊的(即使過期),否則回 []。
    """
    base = _base_url()
    if not base:
        return []

    now = time.monotonic()
    cached = _cards_cache.get("data")
    if cached is not None and now - _cards_cache["fetched_at"] < _CARDS_CACHE_TTL_SECONDS:
        return cached

    async with _cards_lock:
        # 双检:等锁期间别的请求可能已经刷新
        now = time.monotonic()
        cached = _cards_cache.get("data")
        if cached is not None and now - _cards_cache["fetched_at"] < _CARDS_CACHE_TTL_SECONDS:
            return cached
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                resp = await client.get(f"{base}/api/cards", headers=_headers(api_key))
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("swipesmart: GET /api/cards failed err=%s", exc)
            return cached if cached is not None else []
        _cards_cache["data"] = data
        _cards_cache["fetched_at"] = now
        return data


async def fetch_user_usages(api_key: str) -> list[dict]:
    """GET /api/user/usages —— 該使用者所有卡片目前的已用回饋額度,直接透傳
    給 /api/recommend 的 userUsages(見 PH14 SD 的刻意偏離:比自己用
    credit_card_billing 近似更準確,見 plan §1)。失敗 → []。"""
    base = _base_url()
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(f"{base}/api/user/usages", headers=_headers(api_key))
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("swipesmart: GET /api/user/usages failed err=%s", exc)
        return []


async def recommend(
    api_key: str, *, amount: float, merchant: str, user_usages: list[dict]
) -> list[dict] | None:
    """POST /api/recommend。失敗/逾時 → None(呼叫方視同「建議暫時無法使用」)。"""
    base = _base_url()
    if not base:
        return None
    body = {
        "amount": amount,
        "merchantName": merchant,
        "userUsages": user_usages,
        "favoriteCardIds": [],
    }
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                f"{base}/api/recommend", headers=_headers(api_key), json=body
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("swipesmart: POST /api/recommend failed err=%s", exc)
        return None


async def recompute_usage(api_key: str, *, card_id: str, transactions: list[dict]) -> bool:
    """POST /api/user/usages/recompute —— 整批覆蓋(不是累加)這張卡當期的
    usedCapAmount(見 EXTERNAL_INTEGRATION_SPEC.md §4.4)。呼叫方(usage
    backfill job)只在乎成功與否,失敗一律記錄後略過、不重試阻塞。

    注意:截至本次實作,這支端點在 SwipeSmart2 本地 git checkout 裡尚不存在
    (docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md context 已記錄這個落差)——
    404 會被下面的 raise_for_status 當一般失敗吞掉,行為上等同「這次回填
    先跳過」,不會拋出例外影響呼叫方。
    """
    base = _base_url()
    if not base:
        return False
    body = {"cardId": card_id, "transactions": transactions}
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                f"{base}/api/user/usages/recompute", headers=_headers(api_key), json=body
            )
            resp.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "swipesmart: POST /api/user/usages/recompute failed card_id=%s err=%s",
            card_id, exc,
        )
        return False
