"""Each connection/device owns its own home heartbeat; other devices survive away."""

from threading import RLock
from time import monotonic

_home: dict[tuple[str, str], float] = {}
_lock = RLock()
TTL = 45


def update(user_id: str, device_id: str, state: str) -> None:
    with _lock:
        key = (user_id, device_id)
        if state == "home":
            _home[key] = monotonic()
        elif state == "away":
            _home.pop(key, None)


def is_home(user_id: str) -> bool:
    now = monotonic()
    with _lock:
        for key, at in list(_home.items()):
            if now - at >= TTL:
                del _home[key]
        return any(uid == user_id for uid, _ in _home)
