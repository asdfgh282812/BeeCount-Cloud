import logging

from fastapi import APIRouter, Query, WebSocket
from sqlalchemy import select

from ..database import SessionLocal
from ..models import User
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE, decode_token
from ..services import license as license_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(default=""),
    app_version: str | None = Query(default=None),
) -> None:
    if not token:
        logger.warning("ws.reject reason=no_token")
        await websocket.close(code=1008)
        return

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            logger.warning("ws.reject reason=bad_type type=%r jti=%r", payload.get("type"), payload.get("jti"))
            await websocket.close(code=1008)
            return
        scopes = payload.get("scopes", [])
        if not isinstance(scopes, list):
            logger.warning("ws.reject reason=scopes_not_list jti=%r", payload.get("jti"))
            await websocket.close(code=1008)
            return
        normalized = {str(scope) for scope in scopes if isinstance(scope, str)}
        if SCOPE_APP_WRITE not in normalized and SCOPE_WEB_WRITE not in normalized:
            logger.warning(
                "ws.reject reason=scope_missing jti=%r scopes=%r", payload.get("jti"), sorted(normalized)
            )
            await websocket.close(code=1008)
            return
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("ws.reject reason=no_sub jti=%r", payload.get("jti"))
            await websocket.close(code=1008)
            return
    except Exception as exc:
        logger.warning("ws.reject reason=decode_error error=%s: %s", type(exc).__name__, exc)
        await websocket.close(code=1008)
        return

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.id == user_id))
        if user is None or not user.is_enabled:
            logger.warning("ws.reject reason=user_not_found user_id=%r jti=%r", user_id, payload.get("jti"))
            await websocket.close(code=1008)
            return
        # 跟 deps.get_current_user 同一組門檻(docs/LICENSE_KEYS.md):App 版本
        # 過舊(WS 沒辦法帶自訂 header,App 改用 `?app_version=` 帶)→ 4426;
        # 沒有有效授權 → 4402。
        client_type = payload.get("client_type")
        if client_type == "app" or (client_type is None and SCOPE_APP_WRITE in normalized):
            if license_service.app_version_rejection(db, app_version) is not None:
                logger.warning("ws.reject reason=app_version_too_old user=%s version=%r", user_id, app_version)
                await websocket.close(code=4426)
                return
        if not license_service.is_user_licensed(db, user):
            logger.warning("ws.reject reason=license_required user=%s", user_id)
            await websocket.close(code=4402)
            return
    finally:
        db.close()

    manager = websocket.app.state.ws_manager
    await manager.connect(user_id, websocket)
    logger.info("ws.connect user=%s", user_id)
    try:
        while True:
            msg = await websocket.receive_text()
            # 任何帧(含下面的 ping)都刷新 last_seen,供 WSConnectionManager
            # 的闲置回收(sweep_stale)判断这条连线还活着 —— 避免代理/NAT/
            # 睡眠唤醒造成的半开连线永久卡在连线池里堆积成殭屍。
            manager.touch(user_id, websocket)
            # Support client-initiated heartbeat: the client sends {"type":"ping"}
            # every ~25s and waits for a pong. If the socket is silently broken,
            # the pong won't arrive and the client's no-frames timer forces a
            # reconnect. Tolerate malformed payloads silently.
            if msg and '"ping"' in msg:
                try:
                    await websocket.send_text('{"type":"pong"}')
                except Exception:
                    break
    except Exception:
        pass
    finally:
        manager.disconnect(user_id, websocket)
        logger.info("ws.disconnect user=%s", user_id)
