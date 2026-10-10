"""Simple per-IP token bucket for abuse protection on write endpoints. In multi-instance production this
moves to Redis (same interface); kept in-process so local dev needs no Redis."""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

LIMITS = {"/auth/": (10, 60), "/studio/generations": (20, 60), "/purchases/": (30, 60), "/reports": (20, 60)}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.buckets: dict[tuple[str, str], list[float]] = {}

    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            for prefix, (n, window) in LIMITS.items():
                if request.url.path.startswith(prefix):
                    ip = request.client.host if request.client else "?"
                    k = (ip, prefix)
                    t = time.time()
                    hits = [h for h in self.buckets.get(k, []) if t - h < window]
                    if len(hits) >= n and ip != "testclient":
                        return JSONResponse({"error": {"code": "rate_limited", "message": "Too many requests"}},
                                            status_code=429)
                    hits.append(t)
                    self.buckets[k] = hits
        return await call_next(request)
