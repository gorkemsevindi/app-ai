"""Small async load test for the API hot paths (V4 Stage E): feed, template list, remote config, credits,
generation status polling. Measures throughput, p50/p95/p99 latency and error rate per endpoint.

    python infra/scripts/loadtest.py --base http://localhost:8000 --users 50 --requests 20

Signs up `--accounts` users (auth is rate limited per IP, so virtual users share accounts). This is a smoke /
regression benchmark for one process; production capacity must be measured on the deployed topology."""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
import uuid
from collections import defaultdict

import httpx

PATHS = [("GET", "/feed", 4), ("GET", "/templates", 2), ("GET", "/config", 1), ("GET", "/credits", 2),
         ("GET", "/generations", 3), ("GET", "/healthz", 1)]


def pct(v: list[float], q: float) -> float:
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))] * 1000, 1)


async def signup(c: httpx.AsyncClient) -> str:
    r = await c.post("/auth/signup", json={"email": f"load{uuid.uuid4().hex[:10]}@example.com",
                                           "password": "correct-horse-1", "age_confirmed": True,
                                           "terms_accepted": True, "country": "us"})
    r.raise_for_status()
    return r.json()["access_token"]


async def vuser(c: httpx.AsyncClient, token: str, n: int, lat: dict, errs: dict) -> None:
    weighted = [p for p in PATHS for _ in range(p[2])]
    for _ in range(n):
        method, path, _w = random.choice(weighted)  # noqa: S311 - load mix, not security
        t0 = time.perf_counter()
        try:
            r = await c.request(method, path, headers={"Authorization": f"Bearer {token}"})
            ok = r.status_code < 500
        except httpx.HTTPError:
            ok = False
        lat[path].append(time.perf_counter() - t0)
        if not ok:
            errs[path] += 1


async def main(base: str, users: int, requests: int, accounts: int) -> dict:
    lat: dict[str, list[float]] = defaultdict(list)
    errs: dict[str, int] = defaultdict(int)
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as c:
        tokens = [await signup(c) for _ in range(accounts)]
        t0 = time.perf_counter()
        await asyncio.gather(*(vuser(c, tokens[i % accounts], requests, lat, errs) for i in range(users)))
        wall = time.perf_counter() - t0
    total = sum(len(v) for v in lat.values())
    out = {"requests": total, "wall_s": round(wall, 2), "rps": round(total / wall, 1),
           "error_rate": round(sum(errs.values()) / total, 4) if total else None,
           "all": {"p50_ms": pct(sum(lat.values(), []), 0.5), "p95_ms": pct(sum(lat.values(), []), 0.95),
                   "p99_ms": pct(sum(lat.values(), []), 0.99)},
           "endpoints": {p: {"n": len(v), "p50_ms": pct(v, 0.5), "p95_ms": pct(v, 0.95),
                             "mean_ms": round(statistics.mean(v) * 1000, 1), "errors": errs[p]}
                         for p, v in sorted(lat.items())}}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--users", type=int, default=50)
    ap.add_argument("--requests", type=int, default=20)
    ap.add_argument("--accounts", type=int, default=5)
    a = ap.parse_args()
    import json

    print(json.dumps(asyncio.run(main(a.base, a.users, a.requests, a.accounts)), indent=2))  # noqa: T201
