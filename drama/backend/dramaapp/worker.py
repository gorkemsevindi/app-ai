"""Generation worker. Production: run `python -m dramaapp.worker` as separately scaled processes (GPU nodes
for premium providers). Dev: DRAMA_INLINE_WORKER=1 starts one background thread inside the API process.
Jobs are leased in the database (SKIP LOCKED on Postgres); a crashed worker's lease expires and another
worker resumes the job from its first unfinished step."""

import logging
import os
import socket
import threading
import time

from .db import session_factory
from .pipeline.orchestrator import claim_next, run_job

log = logging.getLogger("dramaapp.worker")
_wake = threading.Event()


def kick() -> None:
    _wake.set()


def run_once(worker_id: str) -> bool:
    db = session_factory()()
    try:
        job = claim_next(db, worker_id)
        if not job:
            return False
        log.info("running job %s", job.id)
        run_job(db, job)
        return True
    finally:
        db.close()


def loop(worker_id: str | None = None, stop: threading.Event | None = None) -> None:
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    while not (stop and stop.is_set()):
        try:
            if run_once(worker_id):
                continue
        except Exception:  # noqa: BLE001
            log.exception("worker iteration failed")
        _wake.wait(timeout=2.0)
        _wake.clear()


def start_inline() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=loop, kwargs={"worker_id": "inline", "stop": stop}, daemon=True).start()
    return stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from .db import create_all

    create_all()
    while True:
        try:
            loop()
        except KeyboardInterrupt:
            break
        time.sleep(1)
