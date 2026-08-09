"""OpenID Connect (OIDC) client for SSO login (see src/routers/auth.py 的
`/auth/sso/*` 端點)。

只支援 Authorization Code flow(confidential client,用 client_secret 在
後端跟 IdP 換 token,不是 SPA 直接對話)。PKCE 對這個場景沒有額外防護,
故不啟用 —— 跟 SwipeSmart(../SwipeSmart/src/CardStrategy.Api/Program.cs)
的 `UsePkce = false` 同款簡化。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from ..config import get_settings

settings = get_settings()

_metadata_cache: dict[str, Any] = {}
_metadata_cache_at: float = 0.0
# discovery 文件幾乎不變,快取 1 小時足夠;主要是避免每次登入都多打一次
# 網路請求拖慢 /auth/sso/login 的 redirect 延遲。
_METADATA_TTL_SECONDS = 3600

_jwk_client_cache: dict[str, jwt.PyJWKClient] = {}


class OidcError(RuntimeError):
    pass


def _authority_base() -> str:
    return settings.oidc_authority.rstrip("/")


def get_metadata() -> dict[str, Any]:
    """抓取(並快取)`{authority}/.well-known/openid-configuration`。"""
    global _metadata_cache, _metadata_cache_at
    now = time.monotonic()
    if _metadata_cache and (now - _metadata_cache_at) < _METADATA_TTL_SECONDS:
        return _metadata_cache
    url = f"{_authority_base()}/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    _metadata_cache = data
    _metadata_cache_at = now
    return data


def build_authorize_url(*, state: str, redirect_uri: str) -> str:
    metadata = get_metadata()
    authorize_endpoint = metadata["authorization_endpoint"]
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scope,
        "state": state,
    }
    query = httpx.QueryParams(params)
    return f"{authorize_endpoint}?{query}"


def exchange_code(*, code: str, redirect_uri: str) -> dict[str, Any]:
    metadata = get_metadata()
    token_endpoint = metadata["token_endpoint"]
    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        },
        headers={"Accept": "application/json"},
        timeout=10.0,
    )
    if resp.status_code >= 400:
        raise OidcError(f"token endpoint returned {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _jwk_client() -> jwt.PyJWKClient:
    jwks_uri = get_metadata()["jwks_uri"]
    client = _jwk_client_cache.get(jwks_uri)
    if client is None:
        client = jwt.PyJWKClient(jwks_uri)
        _jwk_client_cache[jwks_uri] = client
    return client


# 只信任非對稱簽章演算法。刻意不直接採信 discovery 文件裡
# `id_token_signing_alg_values_supported` 的原始清單 —— 若其中混進 HS256
# 這類對稱演算法,搭配 PyJWKClient 回傳的非對稱公鑰物件,會開放經典的
# "algorithm confusion" 攻擊(偽造 token 把 alg header 換成 HS256、拿公鑰
# 當 HMAC 密鑰簽章)。這裡跟 discovery 清單取交集,永遠只允許非對稱演算法。
_ASYMMETRIC_ALGORITHMS = {
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
}


def verify_id_token(id_token: str) -> dict[str, Any]:
    """驗簽 + 驗 audience/issuer,回傳 id_token 的 claims dict。"""
    metadata = get_metadata()
    signing_key = _jwk_client().get_signing_key_from_jwt(id_token)
    advertised = metadata.get("id_token_signing_alg_values_supported") or ["RS256"]
    algorithms = [alg for alg in advertised if alg in _ASYMMETRIC_ALGORITHMS] or ["RS256"]
    issuer = metadata.get("issuer") or _authority_base()
    claims = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=algorithms,
        audience=settings.oidc_client_id,
        issuer=issuer,
    )
    return claims
