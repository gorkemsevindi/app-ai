from __future__ import annotations

from pathlib import Path

import httpx


class LeaseLost(Exception):
    pass


class ApiClient:
    def __init__(self, base_url: str, token: str, worker_id: str, timeout: float = 30.0):
        self.worker_id = worker_id
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                 headers={"Authorization": f"Bearer {token}"})

    def _post(self, path: str, body: dict) -> httpx.Response:
        r = self.http.post(path, json=body)
        if r.status_code == 409:
            raise LeaseLost(r.text)
        r.raise_for_status()
        return r

    def claim(self, models: list[str], gpu_provider: str | None, gpu_type: str | None) -> dict | None:
        r = self.http.post("/internal/worker/claim", json={"worker_id": self.worker_id, "models": models,
                                                           "gpu_provider": gpu_provider, "gpu_type": gpu_type})
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()

    def heartbeat(self, job_id: str, attempt: int, status: str | None = None, progress: float | None = None) -> dict:
        return self._post(f"/internal/worker/jobs/{job_id}/heartbeat",
                          {"worker_id": self.worker_id, "attempt": attempt, "status": status,
                           "progress": progress}).json()

    def complete(self, job_id: str, attempt: int, output: dict, moderation: dict, metrics: dict) -> dict:
        return self._post(f"/internal/worker/jobs/{job_id}/complete",
                          {"worker_id": self.worker_id, "attempt": attempt, "output": output,
                           "moderation": moderation, "metrics": metrics}).json()

    def fail(self, job_id: str, attempt: int, code: str, message: str, retryable: bool, metrics: dict) -> dict:
        return self._post(f"/internal/worker/jobs/{job_id}/fail",
                          {"worker_id": self.worker_id, "attempt": attempt, "error_code": code,
                           "message": message[:500], "retryable": retryable, "metrics": metrics}).json()


def download(url: str, dest: Path, max_bytes: int = 200 * 1024 * 1024) -> Path:
    if url.startswith("memory://"):
        raise RuntimeError("memory:// URLs are test-only")
    with httpx.stream("GET", url, timeout=60, follow_redirects=False) as r:
        r.raise_for_status()
        n = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                n += len(chunk)
                if n > max_bytes:
                    raise RuntimeError("download too large")
                f.write(chunk)
    return dest


def upload(url: str, path: Path, mime: str) -> None:
    with open(path, "rb") as f:
        r = httpx.put(url, content=f.read(), headers={"Content-Type": mime}, timeout=120)
    r.raise_for_status()
