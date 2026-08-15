import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket

from .metrics import metrics

logger = logging.getLogger(__name__)


class WSConnectionManager:
    """User → WebSocket 连接池,带上限保护 + 闲置回收 + 非阻塞广播。

    2026-08-15 修复「惊群」事故(单用户堆积 256 条连线,广播导致写入
    API 回應飆到 3~4 秒):
      1. ``MAX_CONNECTIONS_PER_USER``:超过上限时踢掉最旧的一条(FIFO),
         不是拒绝新连线 —— 新连线通常是使用者当下真正在用的分頁。
      2. 闲置回收(``sweep_stale`` + ``run_sweeper``):后端只在
         ``receive_text()`` 抛异常时才会发现断线,但代理/NAT/睡眠唤醒
         可能让 TCP 半开(client 端已经死了,server 端 `await` 永远收不到
         异常),连线会永久卡在 `_connections` 里。改成记录每条连线的
         `last_seen`(每次收到任何帧,含 client 的心跳 ping 都算),背景
         迴圈每 ``SWEEP_INTERVAL_SECONDS`` 巡一次,超过
         ``IDLE_TIMEOUT_SECONDS`` 没有任何帧的连线视为殭屍,主动关闭并
         从池子移除。閾值大于前端 45s 的心跳超时重连窗口,避免正常但
         暂时卡顿的连线被误杀。
      3. ``broadcast_to_user`` 不再『同步』把 ``send_text`` 一个个 await
         完才返回 —— 原本这个 await 链是内嵌在写入 endpoint 的 request
         handler 里(``push.py`` 等 `await broadcast_to_ledger(...)` 后才
         回 HTTP response),殭屍连线的 send 会在 TCP 层挂起,直接拖慢
         使用者自己那次写入请求的回應时间。现在 fan-out 送到
         ``asyncio.create_task`` 背景执行、每条 send 各自套
         ``SEND_TIMEOUT_SECONDS`` 超时,调用方(写入 endpoint)几乎立即
         拿回控制权,不再被卡死连线拖慢。
    """

    MAX_CONNECTIONS_PER_USER = 5
    IDLE_TIMEOUT_SECONDS = 70.0
    SWEEP_INTERVAL_SECONDS = 20.0
    SEND_TIMEOUT_SECONDS = 5.0

    def __init__(self) -> None:
        # dict 保留插入顺序 → 最旧的一条永远是 next(iter(conns))。
        # value = 该连线最近一次收到任何帧的 time.monotonic() 时间戳。
        self._connections: dict[str, dict[WebSocket, float]] = defaultdict(dict)

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        conns = self._connections[user_id]
        while len(conns) >= self.MAX_CONNECTIONS_PER_USER:
            oldest_ws = next(iter(conns))
            conns.pop(oldest_ws, None)
            logger.warning(
                "ws.evict user=%s reason=max_connections_exceeded limit=%d",
                user_id,
                self.MAX_CONNECTIONS_PER_USER,
            )
            metrics.inc("beecount_ws_evicted_total")
            try:
                await oldest_ws.close(code=4001, reason="too many connections")
            except Exception:
                pass
        conns[websocket] = time.monotonic()
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(user_id)
        if conns is not None:
            conns.pop(websocket, None)
            if not conns:
                del self._connections[user_id]
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    def touch(self, user_id: str, websocket: WebSocket) -> None:
        """收到任何帧(含心跳 ping)时刷新 last_seen,供闲置回收判活。"""
        conns = self._connections.get(user_id)
        if conns is not None and websocket in conns:
            conns[websocket] = time.monotonic()

    async def broadcast_to_user(self, user_id: str, payload: dict) -> None:
        conns = self._connections.get(user_id)
        if not conns:
            return
        sockets = list(conns.keys())
        # fire-and-forget:真正的 send 在背景 task 里跑,不阻塞调用方(通常
        # 是某个写入 endpoint 的 request handler,不该被 fan-out 拖慢)。
        asyncio.create_task(self._fanout(user_id, sockets, payload))

    async def _fanout(self, user_id: str, sockets: list[WebSocket], payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str)

        async def send_one(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=self.SEND_TIMEOUT_SECONDS)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*(send_one(ws) for ws in sockets))
        stale = [ws for ws in results if ws is not None]

        logger.info(
            "ws.broadcast user=%s type=%s sockets=%d stale=%d",
            user_id,
            payload.get("type"),
            len(sockets),
            len(stale),
        )

        for ws in stale:
            try:
                await ws.close(code=1011)
            except Exception:
                pass
            self.disconnect(user_id, ws)

    async def sweep_stale(self) -> int:
        """关闭所有超过 IDLE_TIMEOUT_SECONDS 没有任何帧的连线,回传清掉的数量。"""
        now = time.monotonic()
        evicted = 0
        for user_id, conns in list(self._connections.items()):
            stale = [ws for ws, last_seen in list(conns.items()) if now - last_seen > self.IDLE_TIMEOUT_SECONDS]
            for ws in stale:
                logger.warning("ws.sweep.stale user=%s", user_id)
                metrics.inc("beecount_ws_stale_swept_total")
                try:
                    await ws.close(code=1001)
                except Exception:
                    pass
                self.disconnect(user_id, ws)
                evicted += 1
        return evicted

    async def run_sweeper(self) -> None:
        """背景常駐迴圈,定期清掉殭屍连线。由 app startup 挂成 task。"""
        while True:
            await asyncio.sleep(self.SWEEP_INTERVAL_SECONDS)
            try:
                evicted = await self.sweep_stale()
                if evicted:
                    logger.info("ws.sweep.done evicted=%d", evicted)
            except Exception:
                logger.exception("ws.sweep.failed")

    def online_user_ids(self) -> Iterable[str]:
        return self._connections.keys()


async def broadcast_to_ledger(
    *,
    db,
    ws_manager: WSConnectionManager,
    ledger_id: int,
    payload: dict,
    exclude_user_id: str | None = None,
    extra_user_ids: list[str] | None = None,
) -> None:
    """共享账本 fan-out:把 payload 广播给该 ledger 所有 LedgerMember。

    ``ledger_id`` 必须是 ``Ledger.id`` (INT 主键),不是 ``external_id`` UUID
    字符串 — LedgerMember.ledger_id 是 INT 外键。传 external_id 会让查询匹
    配不到任何成员,fan-out 静默失败。

    ``exclude_user_id`` 可用于跳过某个特定用户(如 push 调用方,避免回播给
    自己,虽然 mobile 端有 device_id 去重一般不需要)。
    ``extra_user_ids`` 用于"被踢的用户"场景:已经从 LedgerMember 删了,但仍
    需要通知一次让 client 触发本地清理。
    """
    from .ledger_access import list_ledger_members

    targets: set[str] = set()
    for member_user_id, _role in list_ledger_members(db, ledger_id=ledger_id):
        targets.add(member_user_id)
    if extra_user_ids:
        targets.update(extra_user_ids)
    if exclude_user_id:
        targets.discard(exclude_user_id)
    logger.info(
        "ws.fanout.ledger ledger_id=%s targets=%d type=%s payload_keys=%s",
        ledger_id,
        len(targets),
        payload.get("type"),
        list(payload.keys()),
    )
    for uid in targets:
        await ws_manager.broadcast_to_user(uid, payload)


async def broadcast_to_user_ledgers(
    *,
    db,
    ws_manager: WSConnectionManager,
    user_id: str,
    payload: dict,
    exclude_self: bool = True,
) -> None:
    """跨账本 fan-out:user-global change 时,推给该 user 作为 owner 的所有共享
    账本的非 owner member(即 Editor)。

    `exclude_self=True`(默认)排除 actor 自己 — 同设备 push 收到推送会被 device_id
    去重,但跨设备(用户在 mobile 推,自己 web 同步)需要推。所以推所有 member
    包含 actor 自己,由 client 用 device_id 去重。设置 False 关闭去重。
    """
    from sqlalchemy import select, func
    from .models import Ledger, LedgerMember

    # 该用户作为 owner 的 ledger,且 member_count > 1(共享账本)
    rows = db.execute(
        select(Ledger.id, Ledger.external_id, func.count(LedgerMember.user_id).label("cnt"))
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(Ledger.user_id == user_id)
        .group_by(Ledger.id, Ledger.external_id)
        .having(func.count(LedgerMember.user_id) > 1)
    ).all()
    shared_ledger_ids = [(r.id, r.external_id) for r in rows]

    for lid, _ext in shared_ledger_ids:
        # 推该 ledger 所有非 owner member
        from .ledger_access import list_ledger_members
        for member_user_id, role in list_ledger_members(db, ledger_id=lid):
            if role == "owner":
                continue
            payload_with_ledger = dict(payload)
            payload_with_ledger.setdefault("ledgerId", _ext)
            await ws_manager.broadcast_to_user(member_user_id, payload_with_ledger)
