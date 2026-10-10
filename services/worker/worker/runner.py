"""GPU worker main loop: claim -> download inputs -> adapter.generate (with heartbeat thread)
-> encode 9:16 -> QA scores -> upload -> complete. Any exception becomes a typed failure so the
API can retry (new attempt, no extra charge) or fail + refund."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path

from .adapters.base import AdapterError, Cancelled, GenerationRequest
from .client import ApiClient, LeaseLost, download, upload
from .pipeline.encode import encode_vertical
from .pipeline.qa import moderation_report
from .registry import load_adapters

log = logging.getLogger("worker")

GPU_PRICE_PER_HOUR = float(os.environ.get("GPU_PRICE_PER_HOUR", "0"))


class Heartbeat(threading.Thread):
    def __init__(self, api: ApiClient, job_id: str, attempt: int, interval: float):
        super().__init__(daemon=True)
        self.api, self.job_id, self.attempt, self.interval = api, job_id, attempt, interval
        self.progress = 0.0
        self.status: str | None = None
        self.cancel = threading.Event()
        self.lost = threading.Event()
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self.interval):
            self.beat()

    def beat(self) -> None:
        try:
            r = self.api.heartbeat(self.job_id, self.attempt, self.status, self.progress)
            self.status = None
            if r.get("cancel"):
                self.cancel.set()
        except LeaseLost:
            self.lost.set()
            self.cancel.set()  # stop burning GPU on a job someone else now owns
        except Exception:  # transient network error: keep trying until lease expiry
            log.warning("heartbeat failed", exc_info=True)

    def update(self, fraction: float, stage: str | None = None) -> None:
        self.progress = fraction
        if stage:
            self.status = stage

    def stop(self) -> None:
        self._halt.set()


def process(api: ApiClient, adapters: dict, payload: dict, workdir: Path) -> None:
    job_id, attempt = payload["job_id"], payload["attempt"]
    hb = Heartbeat(api, job_id, attempt, interval=max(5.0, payload.get("lease_s", 120) / 4))
    hb.start()
    t0 = time.time()
    metrics: dict = {"gpu_provider": os.environ.get("GPU_PROVIDER"), "gpu_type": os.environ.get("GPU_TYPE")}
    try:
        adapter = adapters.get(payload["model"])
        if adapter is None:
            raise AdapterError("model_unavailable", f"{payload['model']} not loaded on this worker")
        if payload.get("kind") in ("analysis", "multi_replace"):
            _process_multiperson(api, adapter, payload, workdir, hb, metrics, t0)
            return
        if payload.get("kind") in ("studio_shot", "studio_assemble"):
            _process_studio(api, adapter, payload, workdir, hb, metrics, t0)
            return
        if payload.get("kind") == "character_asset":
            _process_character(api, adapter, payload, workdir, hb, metrics, t0)
            return
        refs = []
        for i, a in enumerate(payload["identity_assets"]):
            if a["kind"] == "photo":
                refs.append(download(a["url"], workdir / f"id_{i:02d}.img"))
        src = download(payload["source_video_url"], workdir / "source.mp4") if payload.get("source_video_url") else None
        w, h = (int(x) for x in payload.get("params", {}).get("resolution", "720x1280").split("x"))
        req = GenerationRequest(job_id=job_id, task=payload["capability"], prompt=payload["prompt"],
                                negative_prompt=payload.get("negative_prompt", ""),
                                duration_s=float(payload["duration_s"]), width=w, height=h, identity_images=refs,
                                workdir=workdir, params=payload.get("params", {}), source_video=src)
        hb.update(0.05, "generating")
        result = adapter.generate(req, hb.update, hb.cancel)
        if hb.lost.is_set():
            return
        hb.update(0.9, "postprocessing")
        enc = encode_vertical(result.video_path, workdir, watermark=payload.get("watermark", True), job_id=job_id)
        report = moderation_report(enc.video)
        upload(payload["upload"]["video"]["url"], enc.video, "video/mp4")
        upload(payload["upload"]["thumbnail"]["url"], enc.thumbnail, "image/jpeg")
        gpu_s = result.gpu_seconds or (time.time() - t0)
        metrics.update(gpu_seconds=gpu_s, est_cost_usd=round(gpu_s / 3600 * GPU_PRICE_PER_HOUR, 5),
                       wall_seconds=time.time() - t0, **result.metrics)
        hb.stop()
        api.complete(job_id, attempt, {"width": enc.width, "height": enc.height, "duration_ms": enc.duration_ms,
                                       "codec": "h264"}, report, metrics)
    except LeaseLost:
        log.warning("lease lost for %s; dropping work", job_id)
    except Cancelled:
        hb.stop()
        _safe_fail(api, job_id, attempt, "cancelled", "cancelled", False, metrics)
    except AdapterError as e:
        hb.stop()
        _safe_fail(api, job_id, attempt, e.code, str(e), e.retryable, metrics)
    except Exception as e:
        hb.stop()
        log.exception("job %s crashed", job_id)
        _safe_fail(api, job_id, attempt, "worker_exception", repr(e), True, metrics)
    finally:
        hb.stop()


def _process_multiperson(api: ApiClient, adapter, payload: dict, workdir: Path, hb: Heartbeat, metrics: dict,
                         t0: float) -> None:
    job_id, attempt = payload["job_id"], payload["attempt"]
    source = download(payload["source_video_url"], workdir / "source.mp4")
    if payload["kind"] == "analysis":
        hb.update(0.05, "generating")
        res = adapter.run(payload, source, workdir, hb.update, hb.cancel)
        if hb.lost.is_set():
            return
        hb.update(0.9, "postprocessing")
        ups = payload["upload"]
        upload(ups["tracks"]["url"], Path(res.pop("_tracks_path")), "application/json")
        thumbs = res.pop("_thumbnails")
        for p in res["persons"]:
            slot = ups["thumbnails"].get(str(p["worker_track_id"]))
            local = thumbs.get(str(p["worker_track_id"]))
            if slot and local:
                upload(slot["url"], Path(local), "image/jpeg")
                p["thumbnail_key"] = slot["key"]
        timing = res.pop("timing", {})
        elapsed = time.time() - t0
        metrics.update(gpu_seconds=elapsed, est_cost_usd=round(elapsed / 3600 * GPU_PRICE_PER_HOUR, 5), **timing)
        hb.stop()
        api.complete(job_id, attempt, {"analysis": res}, {}, metrics)
        return

    tracks_path = download(payload["tracks_url"], workdir / "tracks.json")
    identities: dict[str, list[Path]] = {}
    for track, assets in payload["identities"].items():
        identities[track] = [download(a["url"], workdir / f"id_{track}_{i:02d}.img")
                             for i, a in enumerate(assets) if a["kind"] == "photo"]
    hb.update(0.05, "generating")
    audio_cfg = payload.get("spec", {}).get("audio") or {}
    custom_audio = download(payload["audio_url"], workdir / "custom_audio.bin") if payload.get("audio_url") else None
    kw = {"audio": custom_audio} if custom_audio else {}
    out, qa = adapter.run(payload, source, json.loads(tracks_path.read_text()), identities, workdir, hb.update,
                          hb.cancel, **kw)
    if hb.lost.is_set():
        return
    hb.update(0.9, "postprocessing")
    w, h = (int(x) for x in payload["spec"].get("resolution", "720x1280").split("x"))
    # Spec §2.3/§27: keep the original soundtrack unless the user chose licensed custom audio or none.
    mode = audio_cfg.get("audio_mode", "none")
    audio_src = custom_audio if mode == "custom" else (source if mode == "original" else None)
    enc = encode_vertical(out, workdir, width=w, height=h, watermark=payload.get("watermark", True), job_id=job_id,
                          audio=audio_src)
    report = moderation_report(enc.video)
    upload(payload["upload"]["video"]["url"], enc.video, "video/mp4")
    upload(payload["upload"]["thumbnail"]["url"], enc.thumbnail, "image/jpeg")
    gpu_s = qa.pop("gpu_seconds", time.time() - t0)
    metrics.update(gpu_seconds=gpu_s, est_cost_usd=round(gpu_s / 3600 * GPU_PRICE_PER_HOUR, 5), qa=qa)
    hb.stop()
    api.complete(job_id, attempt, {"width": enc.width, "height": enc.height, "duration_ms": enc.duration_ms,
                                   "codec": "h264", "qa": qa}, report, metrics)


def _process_studio(api: ApiClient, adapter, payload: dict, workdir: Path, hb: Heartbeat, metrics: dict,
                    t0: float) -> None:
    from .studio.assemble import assemble

    job_id, attempt, spec = payload["job_id"], payload["attempt"], payload["spec"]
    w, h = (int(x) for x in spec["resolution"].split("x"))
    hb.update(0.05, "generating")
    if payload["kind"] == "studio_shot":
        refs = {k: [download(u, workdir / f"ref_{k}_{i}.img") for i, u in enumerate(urls)]
                for k, urls in (payload.get("reference_images") or {}).items()}
        boundary = download(payload["boundary_video_url"], workdir / "boundary.mp4") \
            if payload.get("boundary_video_url") else None
        raw, info = adapter.render(spec, refs, workdir, hb.update, hb.cancel, boundary=boundary)
        if hb.lost.is_set():
            return
        if boundary is not None:  # spec V4 §4: measure transition seam quality
            from .studio.shots import seam_score

            info["qa"] = {"seam_score": seam_score(boundary, raw, spec["extend"]["direction"])}
        if spec.get("identity_gate") and refs:  # V6.5: measure each cast member's identity in the shot
            from .characters.qc import shot_identity

            info.setdefault("qa", {})["identity"] = shot_identity(raw, refs)
        hb.update(0.9, "postprocessing")
        # shots are intermediates: no watermark here, it is applied once on the assembled film
        enc = encode_vertical(raw, workdir, width=w, height=h, watermark=False, job_id=job_id, audio=raw)
        captions = None
    else:
        shots = [(download(s["url"], workdir / f"shot_{i:02d}.mp4"), s) for i, s in enumerate(payload["shots"])]
        music = download(payload["music_url"], workdir / "music.bin") if payload.get("music_url") else None
        hb.update(0.3, "postprocessing")
        film, captions = assemble(shots, spec, workdir, music)
        info = {"shots": len(shots), "music": music is not None, "captions": bool(captions)}
        (workdir / "enc").mkdir()
        enc = encode_vertical(film, workdir / "enc", width=w, height=h, watermark=payload.get("watermark", True),
                              job_id=job_id, audio=film)
    report = moderation_report(enc.video)
    upload(payload["upload"]["video"]["url"], enc.video, "video/mp4")
    upload(payload["upload"]["thumbnail"]["url"], enc.thumbnail, "image/jpeg")
    if captions is not None and payload["upload"].get("captions"):
        upload(payload["upload"]["captions"]["url"], captions, "text/vtt")
    elapsed = time.time() - t0
    metrics.update(gpu_seconds=elapsed, est_cost_usd=round(elapsed / 3600 * GPU_PRICE_PER_HOUR, 5), **info)
    hb.stop()
    api.complete(job_id, attempt, {"width": enc.width, "height": enc.height, "duration_ms": enc.duration_ms,
                                   "codec": "h264"}, report, metrics)


def _process_character(api: ApiClient, adapter, payload: dict, workdir: Path, hb: Heartbeat, metrics: dict,
                       t0: float) -> None:
    """One character identity image: generate, measure against the master (views), moderate, upload."""
    import hashlib

    from .characters.qc import compare_view, dhash

    job_id, attempt, spec = payload["job_id"], payload["attempt"], payload["spec"]
    refs = [download(u, workdir / f"ref_{i}.img") for i, u in enumerate(payload.get("reference_images") or [])]
    hb.update(0.05, "generating")
    img_path, info = adapter.generate(spec, refs, workdir, hb.update, hb.cancel)
    if hb.lost.is_set():
        return
    hb.update(0.9, "postprocessing")
    import cv2

    img = cv2.imread(str(img_path))
    qc = {"dhash": dhash(img)}
    if spec["asset_kind"] == "view" and refs:
        qc.update(compare_view(img_path, refs[0], bool(spec.get("face_meaningful"))))
    report = moderation_report(img_path)
    upload(payload["upload"]["image"]["url"], img_path, "image/png")
    elapsed = time.time() - t0
    metrics.update(gpu_seconds=elapsed, est_cost_usd=0.0 if info.get("mock") else None, qa={
        k: v for k, v in qc.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
    if metrics["est_cost_usd"] is None:
        metrics.pop("est_cost_usd")  # priced per image by the API config, not by GPU time
    hb.stop()
    api.complete(job_id, attempt, {"sha256": hashlib.sha256(img_path.read_bytes()).hexdigest(),
                                   "width": int(img.shape[1]), "height": int(img.shape[0]),
                                   "model": info.get("model"), "mock": bool(info.get("mock")), "qc": qc},
                 report, metrics)


def _safe_fail(api, job_id, attempt, code, msg, retryable, metrics) -> None:
    try:
        api.fail(job_id, attempt, code, msg, retryable, metrics)
    except LeaseLost:
        pass
    except Exception:
        log.exception("could not report failure; lease expiry will requeue %s", job_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    api = ApiClient(os.environ["API_URL"], os.environ["WORKER_TOKEN"],
                    os.environ.get("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"))
    adapters = load_adapters()
    healthy = {n: a for n, a in adapters.items() if a.healthcheck().get("ok")}
    log.info("adapters loaded=%s healthy=%s", list(adapters), list(healthy))
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())  # finish current job, then exit (spot preemption)
    idle = 1.0
    while not stopping.is_set():
        try:
            payload = api.claim(list(healthy), os.environ.get("GPU_PROVIDER"), os.environ.get("GPU_TYPE"))
        except Exception:
            log.warning("claim failed", exc_info=True)
            payload = None
        if payload is None:
            time.sleep(idle)
            idle = min(idle * 1.5, 10.0)
            continue
        idle = 1.0
        workdir = Path(tempfile.mkdtemp(prefix=f"job-{payload['job_id']}-"))
        try:
            process(api, healthy, payload, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)  # never keep user media on GPU nodes


if __name__ == "__main__":
    main()
