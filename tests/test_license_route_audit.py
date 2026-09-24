"""授權金鑰防繞過稽核(docs/LICENSE_KEYS.md)。

授權檢查集中在 `deps.get_current_user` / `deps.get_mcp_scopes`(PAT)/
`routers/ws.py` / `mcp/auth.py` 四個入口。這支測試掃過 app 上**每一條**
HTTP 路由,確保:

1. 需要登入的路由一定經過 `get_current_user` 或 `get_mcp_scopes`
   (只掛 `require_scopes` / `oauth2_scheme` 而沒解出 user 的路由會繞過授權)。
2. 完全不需要登入的路由只能是下面白名單裡的那幾支 —— 新增公開端點時
   必須有意識地加進白名單(並確認它不會吐出使用者資料),不能不小心
   多出一個沒鎖的端點。
"""
from __future__ import annotations

from fastapi.routing import APIRoute, APIWebSocketRoute

from src import deps
from src.main import app
from src.mcp import server as mcp_server
from src.routers import ws as ws_router

# (method, path) —— 不需要登入的公開端點。
PUBLIC_ROUTES: set[tuple[str, str]] = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/auth/sso/status"),
    ("GET", "/api/v1/auth/sso/login"),
    ("GET", "/api/v1/auth/sso/callback"),
    ("POST", "/api/v1/auth/2fa/verify"),
    ("GET", "/api/v1/app-version/latest"),
    ("GET", "/api/v1/version"),
    # 頭像圖片本來就公開(App 用 NetworkImage 直接載入),不含帳務資料。
    ("GET", "/api/v1/profile/avatar/{user_id}"),
    ("GET", "/healthz"),
    ("GET", "/ready"),
    ("GET", "/metrics"),
    ("GET", "/.well-known/oauth-protected-resource"),
    ("GET", "/.well-known/oauth-protected-resource/{_resource_path:path}"),
    # SPA 靜態檔(只在有 build 產物時註冊),不含任何 API 資料。
    ("GET", "/"),
    ("GET", "/{full_path:path}"),
}

_AUTH_ENTRY_CALLS = {deps.get_current_user, deps.get_mcp_scopes}


def _calls(dependant, acc: set) -> set:
    for sub in dependant.dependencies:
        acc.add(sub.call)
        _calls(sub, acc)
    return acc


def _iter_routes():
    """展開 FastAPI 0.14x 的 `_IncludedRouter`,取得每條實際路由。"""
    for route in app.routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route.path, getattr(route, "methods", None) or set(), route.dependant, route
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                yield ctx.path, ctx.methods or set(), ctx.dependant, ctx.original_route
        else:
            yield getattr(route, "path", ""), getattr(route, "methods", None) or set(), None, route


def test_every_route_goes_through_license_gate_or_is_public():
    offenders: list[str] = []
    for path, methods, dependant, original in _iter_routes():
        if isinstance(original, APIWebSocketRoute):
            # 唯一的 WS 端點自己做授權檢查(tests/test_license_keys.py 覆蓋)
            assert original.endpoint is ws_router.websocket_endpoint, path
            continue
        if dependant is None:
            # FastAPI 內建 docs 路由 + MCP ASGI 端點(自己的 PATAuthMiddleware 擋授權)
            if path in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}:
                continue
            if path in {"/api/v1/mcp", "/api/v1/mcp/"}:
                assert getattr(original, "endpoint", None) is mcp_server.app, path
                continue
            offenders.append(f"{sorted(methods)} {path}: unknown non-FastAPI route")
            continue
        calls = _calls(dependant, set())
        if calls & _AUTH_ENTRY_CALLS:
            continue
        for method in methods or {"GET"}:
            if (method, path) not in PUBLIC_ROUTES:
                offenders.append(f"{method} {path}")
    assert not offenders, (
        "以下路由沒經過 get_current_user / get_mcp_scopes,會繞過授權金鑰檢查;"
        "若確實是公開端點請加進 PUBLIC_ROUTES:\n" + "\n".join(offenders)
    )
