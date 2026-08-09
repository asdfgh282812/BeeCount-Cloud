"""SwipeSmart2 Personal API Key 管理 + 卡片目錄查詢(Phase 14,
docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md §3.3.1(a)/§3.3.2)。

路由設計比照 `routers/pats.py`(使用者自己的機敏憑證 CRUD),但更簡單 ——
單一 Key 欄位(不是列表),不需要 prefix/last_used 追蹤。明文 Key **絕不**
在 GET 回應裡出現,只在使用者剛貼上那次的 POST 回應裡以遮罩形式確認已存
(連建立當下都不回顯明文 —— 跟 PAT/SwipeSmart 自己的 Key 不同,這把 Key
是使用者從別處複製貼上的,不是這裡生成的,沒有「只顯示一次」的必要,遮罩
即可)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import User, UserProfile
from ..security import SCOPE_APP_WRITE, SCOPE_OPS_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ..services import secret_crypto, swipesmart_client

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE)
_WRITE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE)


def _mask(plaintext: str) -> str:
    s = plaintext.strip()
    if len(s) <= 4:
        return "•" * len(s)
    return f"{s[:8]}••••"


def _get_profile(db: Session, user_id: str) -> UserProfile | None:
    return db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))


class SwipeSmartKeyStatusOut(BaseModel):
    has_key: bool
    masked: str | None = None


class SwipeSmartKeySetRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=255)


class SwipeSmartCardOut(BaseModel):
    card_id: str
    bank_name: str
    card_name: str


@router.get("", response_model=SwipeSmartKeyStatusOut)
def get_swipesmart_key_status(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SwipeSmartKeyStatusOut:
    profile = _get_profile(db, current_user.id)
    encrypted = profile.swipesmart_api_key_encrypted if profile is not None else None
    if not encrypted:
        return SwipeSmartKeyStatusOut(has_key=False, masked=None)
    try:
        plaintext = secret_crypto.decrypt(encrypted)
    except ValueError:
        # JWT_SECRET 轮换等导致解不开:视同没有 key,提示使用者重新设定。
        logger.warning("swipesmart: key decrypt failed for user=%s", current_user.id)
        return SwipeSmartKeyStatusOut(has_key=False, masked=None)
    return SwipeSmartKeyStatusOut(has_key=True, masked=_mask(plaintext))


@router.post("", response_model=SwipeSmartKeyStatusOut)
def set_swipesmart_key(
    req: SwipeSmartKeySetRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SwipeSmartKeyStatusOut:
    plaintext = req.api_key.strip()
    if not plaintext:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="api_key is required")

    encrypted = secret_crypto.encrypt(plaintext)
    profile = _get_profile(db, current_user.id)
    if profile is None:
        profile = UserProfile(user_id=current_user.id, swipesmart_api_key_encrypted=encrypted)
        db.add(profile)
    else:
        profile.swipesmart_api_key_encrypted = encrypted
    db.commit()
    logger.info("swipesmart.key_set user=%s", current_user.id)
    return SwipeSmartKeyStatusOut(has_key=True, masked=_mask(plaintext))


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_swipesmart_key(
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = _get_profile(db, current_user.id)
    if profile is not None and profile.swipesmart_api_key_encrypted:
        profile.swipesmart_api_key_encrypted = None
        db.commit()
    logger.info("swipesmart.key_delete user=%s", current_user.id)


@router.get("/cards", response_model=list[SwipeSmartCardOut])
async def list_swipesmart_cards(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SwipeSmartCardOut]:
    """卡片對照設定視窗用的目錄查詢(§3.3.2)。SwipeSmart 沒有匿名端點,查目
    錄也要帶使用者自己的 Personal Key —— 沒設定就沒辦法查,回 400 讓前端提示
    先貼 Key。SwipeSmart 本身不可用時降級回空陣列(不報錯,呼應「外部依賴不
    擋核心流程」原則 —— 這裡「核心流程」是設定視窗本身,不是記帳,可以稍微
    寬鬆地仍回 200 空陣列讓前端顯示「暫時無法取得卡片清單」)。
    """
    profile = _get_profile(db, current_user.id)
    encrypted = profile.swipesmart_api_key_encrypted if profile is not None else None
    if not encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SwipeSmart personal API key not configured",
        )
    try:
        plaintext = secret_crypto.decrypt(encrypted)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SwipeSmart personal API key not configured",
        ) from None
    cards = await swipesmart_client.get_cards(plaintext)
    return [
        SwipeSmartCardOut(card_id=c["cardId"], bank_name=c["bankName"], card_name=c["cardName"])
        for c in cards
    ]
