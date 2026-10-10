"""Episode render orchestration (spec §4).

Each step is idempotent and resumable: its outputs live in the job work dir and its manifest in JobStep.
A retried/resumed job skips steps already `done` whose outputs still exist. Credits are consumed per step
with ledger idempotency key `job:{id}:step:{name}`, so a retry never double-bills. Spend caps are checked
before each step; cancellation is honoured between steps and during frame rendering.
"""

import hashlib
import json
import shutil
import socket
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AuditEvent,
    Character,
    Episode,
    GenerationJob,
    JobStep,
    OutboxEvent,
    ProviderCall,
    ScriptVersion,
    Series,
    now,
)
from ..modules import ledger
from ..modules.moderation_rules import check_text
from ..providers import registry
from ..providers.base import ProviderUnavailable
from ..providers.google_media import ProviderError
from ..providers.pricing import (
    BUDGET_ROUTE,
    LOCAL_COMPUTE_MICROS_PER_RENDER_SECOND,
    PREMIUM_ROUTE,
    micros_to_credits,
    route_estimate,
)
from ..storage import put_file
from . import qc as qcmod
from . import render as R
from .timeline import DialogueCue, plan, to_manifest

STEPS = ["preflight", "voice", "plan", "music", "performance", "mix", "captions", "compose", "qc", "package"]
# realistic route: photoreal fictional actors (Nano Banana + Veo native audio), see pipeline/realistic.py
STEPS_REALISTIC = ["preflight", "references", "shotplan", "keyframes", "video", "assemble", "score", "mixdown",
                   "captions", "compose", "qc", "package"]
ROUTES = ("preview_2d", "realistic")


class Cancelled(Exception):
    pass


class StepError(Exception):
    def __init__(self, code: str, detail: str = "", retryable: bool = True):
        self.code, self.detail, self.retryable = code, detail, retryable
        super().__init__(f"{code}: {detail}")


def dna_hash(look: dict, voice: dict) -> str:
    return hashlib.sha256(json.dumps({"look": look, "voice": voice}, sort_keys=True).encode()).hexdigest()


def workdir(job_id: str) -> Path:
    p = get_settings().storage_dir.parent / "work" / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = get_settings().storage_dir.parent / "cache" / "tts"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ------------------------------------------------------------------------------------------ spec / estimate
def build_spec(db: Session, ep: Episode) -> dict:
    series = db.get(Series, ep.series_id)
    sv = db.scalar(select(ScriptVersion).where(ScriptVersion.episode_id == ep.id,
                                               ScriptVersion.version == ep.current_script_version))
    if not sv:
        raise StepError("script.missing", "Episode has no script", retryable=False)
    chars = db.scalars(select(Character).where(Character.series_id == series.id)).all()
    cmap = {}
    for c in chars:
        key = c.dna.get("key", c.id)
        cmap[key] = {"id": c.id, "name": c.name, "look": c.dna.get("look", {}), "voice": c.voice, "locked": c.locked,
                     "version": c.version, "likeness_source": c.likeness_source, "blocked_reason": c.blocked_reason,
                     "hash": dna_hash(c.dna.get("look", {}), c.voice)}
    used = {ln["speaker"] for sc in sv.content["scenes"] for ln in sc["lines"]} | {
        k for sc in sv.content["scenes"] for k in sc.get("characters", [])}
    missing = used - set(cmap)
    if missing:
        raise StepError("script.unknown_character", f"Unknown speakers: {sorted(missing)}", retryable=False)
    locs = {loc["key"]: loc for loc in series.bible.get("locations", [])}
    ordered = {k: cmap[k] for k in cmap if k in used}
    return {"episode_id": ep.id, "series_id": series.id, "title": f"{series.title} · {ep.number}",
            "lang": series.language, "genre": series.genre, "target_s": ep.target_duration_s,
            "scenes": sv.content["scenes"], "script_version": sv.version, "characters": ordered,
            "locations": locs, "seed": int(hashlib.sha256(ep.id.encode()).hexdigest()[:8], 16)}


def estimate(spec: dict, quality: str) -> dict:
    s = get_settings()
    words = sum(len(ln["text"].split()) for sc in spec["scenes"] for ln in sc["lines"])
    chars = sum(len(ln["text"]) for sc in spec["scenes"] for ln in sc["lines"])
    seconds = max(spec["target_s"], words / 2.6 + 4 * len(spec["scenes"]))
    per_min = s.render_credits_per_min_final if quality == "final" else s.render_credits_per_min_preview
    fee = max(1, int(per_min * seconds / 60 + 0.999))
    provider = micros_to_credits(int(seconds * LOCAL_COMPUTE_MICROS_PER_RENDER_SECOND))
    images = len(spec["characters"]) * 7
    return {
        "quality": quality, "est_seconds": round(seconds, 1), "dialogue_words": words, "characters": chars,
        "credits": fee + provider, "breakdown_credits": {"platform_render_fee": fee, "provider_pass_through": provider},
        "route": "local (studio_preview_local + espeak_local + procedural_local)",
        "shadow_estimates_usd": {
            "budget_route": route_estimate(BUDGET_ROUTE, seconds=seconds, chars=chars, images=images),
            "premium_route": route_estimate(PREMIUM_ROUTE, seconds=seconds, chars=chars, images=images),
        },
    }


# ------------------------------------------------------------------------------------------ job lifecycle
def create_job(db: Session, *, owner_id: str, ep: Episode, quality: str, max_spend: int | None,
               route: str = "preview_2d") -> GenerationJob:
    from ..errors import AppError
    from . import realistic

    spec = build_spec(db, ep)
    for k, c in spec["characters"].items():
        if c["blocked_reason"]:
            raise AppError("rights.character_blocked", f"Character '{k}' is blocked: {c['blocked_reason']}", 403)
    if route == "realistic":
        registry.google_media()  # raises ProviderUnavailable (-> 503) before any job is created
        est = realistic.estimate(spec, quality, db)
    else:
        est = estimate(spec, quality)
    s = get_settings()
    bal = ledger.credit_balance(db, owner_id)
    if bal < est["credits"]:
        raise AppError("credits.insufficient", "Not enough production credits", 402, needed=est["credits"], balance=bal)
    cap = s.daily_spend_cap_credits
    spent = ledger.credits_spent_since(db, owner_id, now() - timedelta(days=1))
    if spent + est["credits"] > cap:
        raise AppError("credits.daily_cap", "Daily spend cap reached", 429, cap=cap, spent=spent)
    limit = max_spend if max_spend is not None else est["credits"] * 2
    if limit < est["credits"]:
        raise AppError("credits.max_spend_too_low", "max_spend is below the preflight estimate", 400,
                       estimate=est["credits"])
    job = GenerationJob(owner_id=owner_id, episode_id=ep.id, kind="episode_render", quality=quality,
                        params={"spec": spec, "estimate": est, "route": route}, estimate_credits=est["credits"],
                        max_spend_credits=limit, priority=50 if quality == "preview" else 100)
    for i, name in enumerate(STEPS_REALISTIC if route == "realistic" else STEPS):
        job.steps.append(JobStep(seq=i, name=name))
    db.add(job)
    ep.status = "rendering"
    db.flush()
    _event(db, "GenerationRequested", job.id, {"episode_id": ep.id, "quality": quality})
    return job


def _event(db: Session, type_: str, key: str, payload: dict) -> None:
    db.add(OutboxEvent(type=type_, dedup_key=f"{type_}:{key}", payload=payload))


def claim_next(db: Session, worker_id: str) -> GenerationJob | None:
    """Lease the next runnable job (queued, or running with an expired lease = crashed worker)."""
    t = now()
    q = (select(GenerationJob.id).where(or_(GenerationJob.status == "queued",
                                            (GenerationJob.status == "running") & (GenerationJob.lease_expires_at < t)))
         .order_by(GenerationJob.priority, GenerationJob.created_at).limit(1))
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    jid = db.scalar(q)
    if not jid:
        return None
    lease = t + timedelta(seconds=get_settings().job_lease_seconds)
    res = db.execute(update(GenerationJob).where(GenerationJob.id == jid, or_(
        GenerationJob.status == "queued", GenerationJob.lease_expires_at < t)).values(
        status="running", lease_owner=worker_id, lease_expires_at=lease,
        started_at=func.coalesce(GenerationJob.started_at, t)))
    db.commit()
    return db.get(GenerationJob, jid) if res.rowcount else None


def _heartbeat(db: Session, job: GenerationJob) -> None:
    job.lease_expires_at = now() + timedelta(seconds=get_settings().job_lease_seconds)
    db.commit()


def run_job(db: Session, job: GenerationJob, *, stop_after: str | None = None,
            fail_hook: Callable[[str], None] | None = None) -> GenerationJob:
    """Execute remaining steps. `stop_after` / `fail_hook` exist for crash/resume tests."""
    spec = job.params["spec"]
    wd = workdir(job.id)
    ctx = {"db": db, "job": job, "spec": spec, "wd": wd,
           "record": lambda step, cap, info: (_record_call(db, job, step, cap, info), db.commit())}
    realistic_route = job.params.get("route") == "realistic"
    if job.status not in ("running",):
        job.status = "running"
        job.started_at = job.started_at or now()
    job.attempts += 1
    db.commit()
    _event(db, "GenerationStarted", f"{job.id}:{job.attempts}", {"job_id": job.id})
    for step in job.steps:
        db.refresh(job)
        if job.status == "cancelled":
            _finish(db, job, "cancelled")
            return job
        if step.status == "done" and _outputs_exist(wd, step.manifest):
            continue
        est = job.params["estimate"]["breakdown_credits"]
        step_cost = 0 if realistic_route else _step_credits(step.name, est)  # realistic bills per provider call
        if job.spent_credits + step_cost > job.max_spend_credits:
            _fail(db, job, step, StepError("credits.max_spend_exceeded", "Job spend cap reached", retryable=False))
            return job
        step.status, step.started_at, step.attempts = "running", now(), step.attempts + 1
        db.commit()
        try:
            if fail_hook:
                fail_hook(step.name)
            manifest = STEP_FUNCS[step.name](ctx)
        except Cancelled:
            step.status = "pending"
            _finish(db, job, "cancelled")
            return job
        except StepError as e:
            _fail(db, job, step, e)
            return job
        except ProviderError as e:
            _fail(db, job, step, StepError(e.code, e.detail, retryable=e.retryable))
            return job
        except ProviderUnavailable as e:
            _fail(db, job, step, StepError("provider.unavailable", str(e), retryable=False))
            return job
        except Exception as e:  # noqa: BLE001 - provider/ffmpeg errors become retryable step failures
            _fail(db, job, step, StepError("step.exception", f"{type(e).__name__}: {e}"[:2000]))
            return job
        step.manifest = manifest or {}
        step.input_hash = hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()
        step.status, step.finished_at = "done", now()
        if step_cost and ledger.consume_credits(db, job.owner_id, step_cost, key=f"job:{job.id}:step:{step.name}",
                                                memo=f"render {step.name}", ref={"job_id": job.id}):
            job.spent_credits += step_cost
        step.cost_credits = step_cost
        _heartbeat(db, job)
        if stop_after == step.name:
            return job  # simulate a crash: lease stays, job stays "running"
    _finish(db, job, "succeeded" if job.output.get("qc_passed") else "needs_review")
    return job


def _step_credits(name: str, est: dict) -> int:
    # fee is charged when the expensive work completes; provider pass-through at voice + performance
    return {"performance": est["platform_render_fee"],
            "voice": est["provider_pass_through"]}.get(name, 0)


def _outputs_exist(wd: Path, manifest: dict) -> bool:
    return all((wd / f).exists() for f in manifest.get("outputs", []))


def _fail(db: Session, job: GenerationJob, step: JobStep, e: StepError) -> None:
    s = get_settings()
    step.status, step.error_code, step.finished_at = "failed", e.code, now()
    job.error_code, job.error_detail = e.code, e.detail
    if e.retryable and job.attempts <= s.max_render_retries:
        job.status, job.lease_owner, job.lease_expires_at = "queued", None, None  # retry later from this step
        db.commit()
        return
    _finish(db, job, "failed")


def _finish(db: Session, job: GenerationJob, status: str) -> None:
    job.status, job.finished_at, job.lease_owner, job.lease_expires_at = status, now(), None, None
    ep = db.get(Episode, job.episode_id) if job.episode_id else None
    if status == "failed":
        # refund policy: platform-side failures refund all credits consumed by this job
        from ..models import LedgerTransaction
        txns = db.scalars(select(LedgerTransaction).where(LedgerTransaction.idempotency_key.like(f"job:{job.id}:step:%"))).all()
        for t in txns:
            ledger.reverse(db, t, idempotency_key=f"refund:{t.idempotency_key}", memo="failed generation refund")
        job.output = {**job.output, "refunded_credits": job.spent_credits}
        if ep:
            ep.status = "draft"
        _event(db, "GenerationFailed", job.id, {"job_id": job.id, "error": job.error_code})
    elif status in ("succeeded", "needs_review"):
        _event(db, "GenerationCompleted", job.id, {"job_id": job.id, "qc_passed": job.output.get("qc_passed")})
    elif status == "cancelled" and ep and ep.status == "rendering":
        ep.status = "draft"
    db.add(AuditEvent(actor_id=job.owner_id, action=f"job.{status}", target_type="job", target_id=job.id))
    db.commit()


def _record_call(db: Session, job: GenerationJob, step: str, capability: str, info, cache_hit=False, latency_ms=0):
    db.add(ProviderCall(job_id=job.id, step=step, capability=capability, provider=info.provider, model=info.model,
                        model_version=info.model_version, is_mock=info.is_mock, units=info.units,
                        cost_usd_micros=info.cost_usd_micros, seed=info.seed, cache_hit=cache_hit,
                        latency_ms=latency_ms))


# ------------------------------------------------------------------------------------------ steps
def step_preflight(ctx) -> dict:
    spec, db, job = ctx["spec"], ctx["db"], ctx["job"]
    text = "\n".join(ln["text"] for sc in spec["scenes"] for ln in sc["lines"])
    mod = check_text(text)
    if not mod["ok"]:
        raise StepError("moderation.blocked", ",".join(mod["blocked"]), retryable=False)
    flags = []
    for k, c in spec["characters"].items():
        if c["likeness_source"] == "real_person":
            from ..models import Character as C
            from ..modules.rights import active_grant_for
            ch = db.get(C, c["id"])
            if not active_grant_for(db, ch):
                raise StepError("rights.consent_required", f"Character '{k}' uses a real likeness without an "
                                "approved, unrevoked rights grant", retryable=False)
            flags.append(f"real_likeness:{k}")  # allowed, but forces manual review before publish
    del job
    return {"moderation": mod, "flags": flags, "outputs": []}


def step_voice(ctx) -> dict:
    spec, db, job, wd = ctx["spec"], ctx["db"], ctx["job"], ctx["wd"]
    tts = registry.tts()
    clips = []
    li = hits = 0
    for sc in spec["scenes"]:
        for ln in sc["lines"]:
            voice = spec["characters"][ln["speaker"]]["voice"]
            key = hashlib.sha256(json.dumps([tts.id, spec["lang"], voice, ln["text"], ln.get("emotion"),
                                             ln.get("intensity")], sort_keys=True).encode()).hexdigest()[:24]
            wav, meta = cache_dir() / f"{key}.wav", cache_dir() / f"{key}.json"
            t0 = time.time()
            if wav.exists() and meta.exists():
                data = json.loads(meta.read_text())
                hit = True
            else:
                clip = tts.synthesize(ln["text"], lang=spec["lang"], voice=voice, emotion=ln.get("emotion", "neutral"),
                                      intensity=float(ln.get("intensity", 0.7)), out=wav)
                data = {"duration": clip.duration, "words": [asdict(w) for w in clip.words],
                        "visemes": [asdict(v) for v in clip.visemes], "envelope": clip.envelope,
                        "info": asdict(clip.info)}
                meta.write_text(json.dumps(data))
                hit = False
            from ..providers.base import CallInfo
            _record_call(db, job, "voice", "tts", CallInfo(**data["info"]), cache_hit=hit,
                         latency_ms=int((time.time() - t0) * 1000))
            shutil.copyfile(wav, wd / f"line_{li:03d}.wav")
            clips.append(data)
            hits += int(hit)
            li += 1
    (wd / "voice.json").write_text(json.dumps(clips))
    db.commit()
    return {"lines": li, "provider": tts.id, "outputs": ["voice.json"],
            "cache_hits": hits}


def step_plan(ctx) -> dict:
    spec, wd = ctx["spec"], ctx["wd"]
    clips = json.loads((wd / "voice.json").read_text())
    shots, starts = plan(spec["scenes"], [c["duration"] for c in clips], spec["target_s"])
    cues = []
    li = 0
    for sc in spec["scenes"]:
        for ln in sc["lines"]:
            st = starts[li]["start"]
            c = clips[li]
            cues.append(DialogueCue(
                line_index=li, speaker=ln["speaker"], text=ln["text"], emotion=ln.get("emotion", "neutral"),
                intensity=float(ln.get("intensity", 0.7)), start=st, duration=c["duration"], look_at=ln.get("look_at"),
                words=[{"word": w["word"], "start": round(st + w["start"], 3), "end": round(st + w["end"], 3)}
                       for w in c["words"]],
                visemes=[{"t": round(st + v["t"], 3), "viseme": v["viseme"]} for v in c["visemes"]],
                envelope=c["envelope"], audio=str(wd / f"line_{li:03d}.wav")))
            li += 1
    manifest = to_manifest(shots, cues)
    R.write_json(wd / "timeline.json", manifest)
    return {"shots": len(shots), "duration": manifest["duration"], "outputs": ["timeline.json"]}


def step_music(ctx) -> dict:
    spec, wd, db, job = ctx["spec"], ctx["wd"], ctx["db"], ctx["job"]
    tl = json.loads((wd / "timeline.json").read_text())
    mp = registry.music()
    mood = spec["scenes"][0].get("mood", "tense") if spec["scenes"] else "tense"
    info = mp.compose(mood=mood, genre=spec["genre"], duration=tl["duration"], seed=spec["seed"], out=wd / "music.wav")
    R.build_ambience(tl["shots"], spec, tl["duration"], mp, wd, wd / "ambience.wav")
    _record_call(db, job, "music", "music", info)
    db.commit()
    return {"mood": mood, "provider": mp.id, "outputs": ["music.wav", "ambience.wav"]}


def _height(job: GenerationJob) -> int:
    s = get_settings()
    return s.final_height if job.quality == "final" else s.preview_height


def step_performance(ctx) -> dict:
    spec, wd, db, job = ctx["spec"], ctx["wd"], ctx["db"], ctx["job"]
    tl = json.loads((wd / "timeline.json").read_text())
    H = _height(job)
    W = int(H * 9 / 16) // 2 * 2
    last = [time.time()]

    def progress(p: float) -> None:
        if time.time() - last[0] > 5:
            last[0] = time.time()
            db.refresh(job)
            if job.status == "cancelled":
                raise Cancelled()
            job.output = {**job.output, "progress": {"step": "performance", "pct": round(p * 100)}}
            _heartbeat(db, job)

    info = R.render_video(tl, spec, wd / "picture.mp4", W, H, on_progress=progress)
    from ..providers.base import CallInfo
    _record_call(db, job, "performance", "video", CallInfo(provider="studio_preview_local", model="2d-performer",
                                                          model_version="1", units={"seconds": tl["duration"]}))
    _record_call(db, job, "performance", "lipsync", CallInfo(provider="viseme_local", model="viseme+energy",
                                                            model_version="1", units={"seconds": tl["duration"]}))
    db.commit()
    return {**info, "outputs": ["picture.mp4"]}


def step_mix(ctx) -> dict:
    wd = ctx["wd"]
    tl = json.loads((wd / "timeline.json").read_text())
    R.build_dialogue_track(tl["cues"], tl["duration"], wd / "dialogue.wav")
    info = R.mix_audio(wd / "dialogue.wav", wd / "music.wav", wd / "ambience.wav", wd / "mix.m4a")
    return {**info, "outputs": ["dialogue.wav", "mix.m4a"]}


def step_captions(ctx) -> dict:
    wd, spec, job = ctx["wd"], ctx["spec"], ctx["job"]
    tl = json.loads((wd / "timeline.json").read_text())
    H = _height(job)
    W = int(H * 9 / 16) // 2 * 2
    info = R.write_captions(tl["cues"], spec, wd, W, H)
    return {**info, "outputs": info["files"]}


def step_compose(ctx) -> dict:
    wd = ctx["wd"]
    burn = ctx["job"].params.get("burn_captions", True)
    R.compose_final(wd, "picture.mp4", "mix.m4a", "captions.ass" if burn else None, "final.mp4")
    return {"burned_captions": burn, "outputs": ["final.mp4"]}


def step_qc(ctx) -> dict:
    wd, spec, job, db = ctx["wd"], ctx["spec"], ctx["job"], ctx["db"]
    tl = json.loads((wd / "timeline.json").read_text())
    pre = next(st for st in job.steps if st.name == "preflight").manifest
    identity = {}
    for k, c in spec["characters"].items():
        ch = db.get(Character, c["id"])
        cur = dna_hash(ch.dna.get("look", {}), ch.voice)
        identity[k] = {"locked_hash": cur if ch.locked else c["hash"], "render_hash": c["hash"]}
    report = qcmod.run_qc(final_mp4=wd / "final.mp4", dialogue_wav=wd / "dialogue.wav", cues=tl["cues"],
                          target_s=spec["target_s"], script_lang=spec["lang"], voice_langs={spec["lang"]},
                          identity=identity, flags=pre.get("flags", []))
    R.write_json(wd / "qc.json", report)
    job.output = {**job.output, "qc_passed": report["passed"]}
    db.commit()
    return {"passed": report["passed"], "outputs": ["qc.json"]}


def step_package(ctx) -> dict:
    wd, spec, job, db = ctx["wd"], ctx["spec"], ctx["job"], ctx["db"]
    ep = db.get(Episode, job.episode_id)
    tl = json.loads((wd / "timeline.json").read_text())
    H = _height(job)
    R.package_hls(wd / "final.mp4", wd / "hls", H)
    first_close = next((s for s in tl["shots"] if s["kind"] in ("close", "single")), tl["shots"][0])
    R.thumbnail(wd / "final.mp4", first_close["start"] + 0.6, spec["title"], wd / "thumb.jpg")
    calls = db.scalars(select(ProviderCall).where(ProviderCall.job_id == job.id)).all()
    provenance = {
        "synthetic_media": True, "label": "AI-generated", "job_id": job.id, "script_version": spec["script_version"],
        "models": sorted({f"{c.capability}:{c.provider}/{c.model}@{c.model_version}" for c in calls}),
        "mock_components": sorted({f"{c.capability}:{c.provider}" for c in calls if c.is_mock}),
        "characters": {k: {"id": c["id"], "version": c["version"], "likeness": c["likeness_source"],
                           "dna_hash": c["hash"]} for k, c in spec["characters"].items()},
        "rendered_at": datetime.now(UTC).isoformat(), "renderer_host": socket.gethostname(),
    }
    base = f"episodes/{ep.id}/{job.id}"
    owner = job.owner_id
    meta = {"provenance": provenance}
    video = put_file(db, wd / "final.mp4", f"{base}/final.mp4", "video", owner, meta)
    hls = put_file(db, wd / "hls", f"{base}/hls", "hls", owner, meta)
    vtt = put_file(db, wd / "captions.vtt", f"{base}/captions.vtt", "captions", owner)
    put_file(db, wd / "captions.srt", f"{base}/captions.srt", "captions_srt", owner)
    thumb = put_file(db, wd / "thumb.jpg", f"{base}/thumb.jpg", "image", owner)
    qc = json.loads((wd / "qc.json").read_text())
    ep.video_asset_id, ep.hls_asset_id, ep.captions_asset_id, ep.thumbnail_asset_id = video.id, hls.id, vtt.id, thumb.id
    ep.duration_s, ep.qc_report, ep.provenance, ep.status = qc["duration_s"], qc, provenance, "rendered"
    series = db.get(Series, ep.series_id)
    if not series.cover_asset_id:
        series.cover_asset_id = thumb.id
    job.output = {**job.output, "video_asset_id": video.id, "hls_asset_id": hls.id, "progress": {"pct": 100}}
    db.commit()
    return {"assets": [video.id, hls.id, vtt.id, thumb.id], "outputs": ["final.mp4", "thumb.jpg"]}


def step_score(ctx) -> dict:
    """Realistic route: music bed only (Veo already rendered production sound and ambience)."""
    spec, wd, db, job = ctx["spec"], ctx["wd"], ctx["db"], ctx["job"]
    tl = json.loads((wd / "timeline.json").read_text())
    mp = registry.music()
    mood = spec["scenes"][0].get("mood", "tense") if spec["scenes"] else "tense"
    info = mp.compose(mood=mood, genre=spec["genre"], duration=tl["duration"], seed=spec["seed"], out=wd / "music.wav")
    _record_call(db, job, "score", "music", info)
    db.commit()
    return {"mood": mood, "outputs": ["music.wav"]}


def step_mixdown(ctx) -> dict:
    wd = ctx["wd"]
    info = R.mix_audio(wd / "dialogue.wav", wd / "music.wav", wd / "ambience.wav", wd / "mix.m4a")
    return {**info, "outputs": ["mix.m4a"]}


def _realistic(name: str):
    def call(ctx):
        from . import realistic
        return getattr(realistic, f"step_{name}")(ctx)
    return call


STEP_FUNCS = {"preflight": step_preflight, "voice": step_voice, "plan": step_plan, "music": step_music,
              "references": _realistic("references"), "shotplan": _realistic("shotplan"),
              "keyframes": _realistic("keyframes"), "video": _realistic("video"), "assemble": _realistic("assemble"),
              "score": step_score, "mixdown": step_mixdown,
              "performance": step_performance, "mix": step_mix, "captions": step_captions, "compose": step_compose,
              "qc": step_qc, "package": step_package}
