"""WSConnectionManager 连线上限 / 闲置回收 / 非阻塞广播(2026-08-15 修复
「惊群」事故:单用户堆积 256 条殭屍连线,广播拖慢写入 API 到 3~4 秒)。

用 duck-typed FakeWebSocket 取代真正的 fastapi.WebSocket —— manager 只调用
accept() / send_text() / close(),不需要真实网络连线。
"""
from __future__ import annotations

import asyncio

from src.websocket_manager import WSConnectionManager


class FakeWebSocket:
    def __init__(self, *, send_delay: float | None = None) -> None:
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.sent: list[str] = []
        self._send_delay = send_delay

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if self._send_delay is not None:
            await asyncio.sleep(self._send_delay)
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code


def test_connect_evicts_oldest_when_over_limit():
    async def scenario():
        manager = WSConnectionManager()
        manager.MAX_CONNECTIONS_PER_USER = 2
        ws1, ws2, ws3 = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()

        await manager.connect("u1", ws1)
        await manager.connect("u1", ws2)
        await manager.connect("u1", ws3)  # 触发踢掉 ws1

        assert ws1.closed is True
        assert ws1.close_code == 4001
        remaining = manager._connections["u1"]
        assert ws1 not in remaining
        assert ws2 in remaining and ws3 in remaining
        assert len(remaining) == 2

    asyncio.run(scenario())


def test_sweep_stale_closes_idle_sockets_but_keeps_fresh_ones():
    async def scenario():
        manager = WSConnectionManager()
        manager.IDLE_TIMEOUT_SECONDS = 0.05
        stale_ws, fresh_ws = FakeWebSocket(), FakeWebSocket()

        await manager.connect("u1", stale_ws)
        await manager.connect("u1", fresh_ws)
        # 人为把 stale_ws 的 last_seen 打回很久以前,fresh_ws 保持刚连上的时间戳。
        manager._connections["u1"][stale_ws] -= 10.0

        evicted = await manager.sweep_stale()

        assert evicted == 1
        assert stale_ws.closed is True
        assert stale_ws.close_code == 1001
        assert fresh_ws.closed is False
        remaining = manager._connections["u1"]
        assert stale_ws not in remaining
        assert fresh_ws in remaining

    asyncio.run(scenario())


def test_touch_refreshes_last_seen_so_sweep_spares_active_socket():
    async def scenario():
        manager = WSConnectionManager()
        manager.IDLE_TIMEOUT_SECONDS = 0.05
        ws = FakeWebSocket()
        await manager.connect("u1", ws)
        manager._connections["u1"][ws] -= 10.0  # 打回很久以前

        manager.touch("u1", ws)  # 模拟收到一帧(如 client ping)
        evicted = await manager.sweep_stale()

        assert evicted == 0
        assert ws.closed is False

    asyncio.run(scenario())


def test_broadcast_to_user_does_not_block_caller_on_slow_socket():
    async def scenario():
        manager = WSConnectionManager()
        manager.SEND_TIMEOUT_SECONDS = 0.05
        slow_ws = FakeWebSocket(send_delay=5.0)  # 模拟殭屍/半开连线,send 永远不返回
        healthy_ws = FakeWebSocket()
        await manager.connect("u1", slow_ws)
        await manager.connect("u1", healthy_ws)

        loop = asyncio.get_event_loop()
        start = loop.time()
        await manager.broadcast_to_user("u1", {"type": "sync_change"})
        elapsed = loop.time() - start

        # 调用方(相当于写入 endpoint 的 request handler)几乎立即拿回控制权,
        # 不会被卡在 slow_ws 的 SEND_TIMEOUT_SECONDS 后面。
        assert elapsed < 0.5

        # 背景 fan-out task 跑完后,慢连线应该被侦测超时、关闭并从池子移除;
        # 健康连线正常收到广播且不受影响。
        await asyncio.sleep(0.3)
        assert slow_ws.closed is True
        assert healthy_ws.sent  # 收到了广播 payload
        remaining = manager._connections.get("u1", {})
        assert slow_ws not in remaining
        assert healthy_ws in remaining

    asyncio.run(scenario())


def test_disconnect_removes_user_entry_when_last_socket_leaves():
    async def scenario():
        manager = WSConnectionManager()
        ws = FakeWebSocket()
        await manager.connect("u1", ws)
        manager.disconnect("u1", ws)
        assert "u1" not in manager._connections

    asyncio.run(scenario())
