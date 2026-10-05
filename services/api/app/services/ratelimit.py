"""Fixed-window rate limiter. Redis-backed; if Redis is unreachable it degrades to a
per-process limiter instead of failing open globally or blocking all traffic."""

import threading
import time

from ..config import get_settings
from ..errors import ApiError

_local: dict[str, tuple[int, int]] = {}
_lock = threading.Lock()
_redis = None


def _client():
    global _redis
    if _redis is None:
        import redis

        _redis = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.2, socket_connect_timeout=0.2)
    return _redis


def _hit_local(key: str, window: int) -> int:
    now_w = int(time.time() // window)
    with _lock:
        w, n = _local.get(key, (now_w, 0))
        if w != now_w:
            w, n = now_w, 0
        n += 1
        _local[key] = (w, n)
        return n


def hit(bucket: str, ident: str, limit: int, window_s: int = 60) -> None:
    key = f"rl:{bucket}:{ident}:{int(time.time() // window_s)}"
    try:
        pipe = _client().pipeline()
        pipe.incr(key)
        pipe.expire(key, window_s + 1)
        count = int(pipe.execute()[0])
    except Exception:
        count = _hit_local(key, window_s)
    if count > limit:
        raise ApiError(429, "rate_limited", "too many requests", {"retry_after_s": window_s})


def reset_local() -> None:
    with _lock:
        _local.clear()
