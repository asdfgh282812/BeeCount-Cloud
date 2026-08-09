"""通用機敏字串加解密(Fernet,key 從 JWT_SECRET 派生)。

從 `services/totp.py` 抽出來的共用實作 —— totp_secret 跟後續其它「需要能
解回明文用」的機敏欄位(例如 Phase 14 的 SwipeSmart Personal API Key)本質
上是同一個需求,不該各自維護一份 key 派生邏輯(兩份邏輯分歧 = 同一把
JWT_SECRET 却推出不同 Fernet key 的風險)。`totp.py` 的
`encrypt_totp_secret`/`decrypt_totp_secret` 委派呼叫這裡,函式名/簽章不變。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def _derive_fernet_key() -> bytes:
    """从 JWT_SECRET sha256 → urlsafe_b64 → 32 字节 Fernet key。

    複用 JWT_SECRET 是為了少一個機密管理負擔(JWT_SECRET 已經是部署級機密,
    丟了 = JWT 全失效,所以這類機敏欄位跟它綁同一安全域是合理的)。
    JWT_SECRET 輪換時所有靠這把 key 加密的欄位全失效。
    """
    settings = get_settings()
    raw = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw)


_fernet_singleton: Fernet | None = None


def _fernet() -> Fernet:
    """懒加载,首次调用时再读 settings — 避免 import 期 settings 未初始化。"""
    global _fernet_singleton
    if _fernet_singleton is None:
        _fernet_singleton = Fernet(_derive_fernet_key())
    return _fernet_singleton


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("secret decrypt failed (JWT_SECRET 轮换?)") from e
