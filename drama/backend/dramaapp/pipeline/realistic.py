"""Realistic route: photoreal fictional actors with Nano Banana + Veo 3.1 (native speech audio).

Steps (each resumable; every paid call is cached by content hash and billed once):
  references  photoreal reference portrait per character, derived from Character DNA and reused while the
              DNA hash is unchanged (identity lock)
  shotplan    one establishing shot per scene + one shot per dialogue line (Veo clip length 4/6/8 s)
  keyframes   9:16 film still per shot, conditioned on the characters' reference portraits
  video       Veo image→video per shot; the prompt carries the exact line, language and emotion, so
              the model renders speech, lip-sync and facial performance in one pass
  assemble    normalise + concatenate shots; split picture and production audio
  then the shared steps: music → mix → captions → compose → qc → package

Known limits (stated in provenance and docs): voice consistency across shots depends on the video model,
since native audio is generated per clip; Turkish speech quality from Veo is not yet verified; caption
timing is energy-based (no ASR yet).
"""

import colorsys
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from sqlalchemy import select

from ..config import get_settings
from ..models import Asset, LedgerTransaction
from ..modules import ledger
from ..providers import registry
from ..providers.music_local import SR, write_wav
from ..providers.pricing import image_price_micros, micros_to_credits, veo_price_micros_per_s
from ..providers.tts_espeak import read_wav, rms_envelope, voiced_segments
from ..storage import local_path, put_file

LANG_NAME = {"tr": "Turkish", "en": "English"}
EMOTION_FACE = {
    "neutral": "a calm, neutral expression", "happy": "a warm genuine smile", "sad": "teary eyes, on the verge of crying",
    "angry": "an angry, tense expression with furrowed brows", "fear": "a frightened expression, eyes wide",
    "surprise": "a shocked, surprised expression, raised eyebrows", "tender": "a soft, tender expression",
}
EMOTION_VOICE = {
    "neutral": "calm", "happy": "warm, smiling", "sad": "sad, breaking", "angry": "angry, raised",
    "fear": "frightened, shaky", "surprise": "shocked", "tender": "soft, tender",
}
CAMERA = {
    "push_in": "Slow cinematic push-in", "pull_out": "Slow pull-out", "pan_left": "Gentle pan left",
    "pan_right": "Gentle pan right", "handheld": "Subtle handheld camera", "static": "Locked-off camera",
}
NEGATIVE = ("subtitles, captions, on-screen text, watermark, logo, cartoon, anime, 3d render, extra people, "
            "deformed face, distorted hands, background music")
NAMED = {
    "black": (20, 20, 20), "dark brown": (59, 36, 22), "brown": (107, 68, 35), "auburn": (160, 82, 45),
    "blonde": (216, 180, 106), "grey": (154, 154, 154), "red": (192, 57, 43), "navy blue": (44, 62, 80),
    "burgundy": (125, 46, 59), "teal": (31, 78, 95), "charcoal": (61, 61, 61), "purple": (91, 58, 110),
    "rust orange": (163, 92, 42), "forest green": (47, 93, 58), "white": (236, 240, 241), "gold": (212, 172, 13),
    "green": (46, 94, 78), "blue": (58, 90, 140),
}


def color_name(hx: str) -> str:
    h = hx.lstrip("#")
    rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return min(NAMED, key=lambda k: sum((a - b) ** 2 for a, b in zip(NAMED[k], rgb, strict=True)))


def skin_name(hx: str) -> str:
    h = hx.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lum = colorsys.rgb_to_hls(r, g, b)[1]
    for t, name in ((0.78, "fair"), (0.68, "light"), (0.58, "medium, olive"), (0.48, "tan"), (0.36, "brown")):
        if lum >= t:
            return name
    return "deep brown"


def describe(ch: dict) -> str:
    lk, v = ch["look"], ch["voice"]
    gender = {"female": "woman", "male": "man"}.get(v.get("gender"), "person")
    age = {"young": "in their early twenties", "adult": "in their thirties", "senior": "in their sixties"}.get(
        lk.get("age_band", "adult"), "adult")
    hair = "bald" if lk.get("hair_style") == "bald" else f"{color_name(lk['hair'])} {lk.get('hair_style', 'short')} hair"
    extras = [x for x, on in (("glasses", lk.get("glasses")), ("a trimmed beard", lk.get("beard"))) if on]
    return (f"{ch['name']}, a {gender} {age} with {skin_name(lk['skin'])} skin, {hair}, {color_name(lk['eyes'])} eyes, "
            f"wearing a {color_name(lk['outfit'])} top with {color_name(lk['accent'])} details"
            + (f", {' and '.join(extras)}" if extras else ""))


def _h(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def cache_dir(kind: str) -> Path:
    p = get_settings().storage_dir.parent / "cache" / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def charge(ctx, key: str, micros: int, memo: str, check_only: bool = False) -> int:
    """Spend-cap check + idempotent credit charge for one paid provider call."""
    from .orchestrator import StepError

    job, db = ctx["job"], ctx["db"]
    credits = micros_to_credits(micros)
    full_key = f"job:{job.id}:step:{key}"
    already = db.scalar(select(LedgerTransaction).where(LedgerTransaction.idempotency_key == full_key))
    if already:
        return 0
    if job.spent_credits + credits > job.max_spend_credits:
        raise StepError("credits.max_spend_exceeded",
                        f"Next call needs {credits} credits; job cap {job.max_spend_credits}, spent {job.spent_credits}",
                        retryable=False)
    if ledger.credit_balance(db, job.owner_id) < credits:
        raise StepError("credits.insufficient", "Wallet balance too low for the next provider call", retryable=False)
    if check_only:
        return credits
    if ledger.consume_credits(db, job.owner_id, credits, key=full_key, memo=memo, ref={"job_id": job.id}):
        job.spent_credits += credits
    db.commit()
    return credits


# ------------------------------------------------------------------------------------------ planning
SPEECH_WPS = 2.5  # natural delivery, words per second
CLIP_LENGTHS = (4, 6, 8)


def speech_seconds(text: str) -> float:
    return max(len(text.split()) / SPEECH_WPS, len(text) / 16)


def clip_seconds(need: float, max_s: int) -> int:
    for s in CLIP_LENGTHS:
        if need + 0.7 <= s <= max_s:
            return s
    return max_s


def split_line(text: str, max_s: int) -> list[str]:
    import re
    if speech_seconds(text) + 0.7 <= max_s:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[.!?…,])\s+", text) if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and speech_seconds(out[-1] + " " + p) + 0.7 <= max_s:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def plan_shots(spec: dict) -> list[dict]:
    """Scene 1 opens on an establishing shot; dialogue is grouped into clips of up to two consecutive lines
    by different speakers (a natural exchange, and fewer paid seconds than one clip per line)."""
    max_s = get_settings().max_shot_seconds
    shots: list[dict] = []
    for si, sc in enumerate(spec["scenes"]):
        loc = spec["locations"].get(sc["location"], {"name": sc["location"]})
        base = {"scene": si, "location": sc["location"], "location_name": loc.get("name", sc["location"]),
                "lighting": loc.get("lighting", "warm"), "ambience": loc.get("ambience", "room"),
                "time_of_day": sc.get("time_of_day", "day"), "mood": sc.get("mood", "tense")}
        if si == 0:
            shots.append({**base, "kind": "establishing", "characters": sc["characters"][:3], "focus": None,
                          "lines": [], "seconds": 4, "camera": "push_in"})
        pieces = [{**ln, "text": t} for ln in sc["lines"] for t in split_line(ln["text"], max_s)]
        i = 0
        while i < len(pieces):
            group = [pieces[i]]
            if i + 1 < len(pieces) and pieces[i + 1]["speaker"] != pieces[i]["speaker"]:
                pair = speech_seconds(pieces[i]["text"]) + speech_seconds(pieces[i + 1]["text"]) + 0.4
                if pair + 0.7 <= max_s:
                    group.append(pieces[i + 1])
            i += len(group)
            need = sum(speech_seconds(g["text"]) for g in group) + 0.4 * (len(group) - 1)
            lead = group[0]
            hot = lead.get("emotion") in ("angry", "fear", "surprise", "sad") and lead.get("intensity", 0.7) >= 0.75
            kind = "two" if len(group) == 2 else ("close" if hot else "single")
            chars = list(dict.fromkeys([g["speaker"] for g in group] + ([lead["look_at"]] if lead.get("look_at") else [])))
            shots.append({**base, "kind": kind, "characters": chars[:2], "focus": lead["speaker"], "lines": group,
                          "seconds": clip_seconds(need, max_s),
                          "camera": "static" if hot else (sc.get("camera", "push_in") if kind != "two" else "handheld")})
    for i, s in enumerate(shots):
        s["index"] = i
    return shots


def keyframe_prompt(shot: dict, spec: dict) -> str:
    chars = spec["characters"]
    style = spec.get("visual_style", "cinematic")
    look = {"cinematic": "photorealistic cinematic film still, shallow depth of field, 35mm",
            "noir": "photorealistic film-noir still, high contrast", "pastel": "photorealistic, soft pastel grade",
            "anime": "photorealistic cinematic film still"}.get(style, "photorealistic cinematic film still")
    where = f"{shot['location_name']}, {shot['time_of_day']}, {shot['lighting']} lighting, {shot['mood']} mood"
    if shot["kind"] == "establishing":
        people = "; ".join(describe(chars[c]) for c in shot["characters"] if c in chars)
        frame = f"Wide establishing shot of {where}. In the scene: {people}."
    elif shot["kind"] == "two":
        a, b = shot["lines"][0], shot["lines"][1]
        frame = (f"Medium two-shot in {where}, both faces clearly visible, facing each other at a slight angle. "
                 f"{describe(chars[a['speaker']])}, with {EMOTION_FACE.get(a.get('emotion'), 'a natural expression')}. "
                 f"{describe(chars[b['speaker']])}, with {EMOTION_FACE.get(b.get('emotion'), 'a natural expression')}.")
    else:
        ln = shot["lines"][0]
        size = "Close-up" if shot["kind"] == "close" else "Medium close-up"
        other = chars.get(ln.get("look_at") or "")
        frame = (f"{size} in {where}. {describe(chars[ln['speaker']])}, with "
                 f"{EMOTION_FACE.get(ln.get('emotion'), 'a natural expression')}"
                 + (f", looking just off-camera towards {other['name']}" if other else "") + ".")
    return (f"{look}, vertical 9:16 composition, faces in the upper half of the frame. {frame} "
            "Use the reference photos for the exact identity of each named character: same face, hair and outfit. "
            "Fictional people, not celebrities. No text, no captions, no watermark.")


def _who(ch: dict) -> str:
    g = {"female": "woman", "male": "man"}.get(ch["voice"].get("gender"), "person")
    return f"{ch['name']} (the {g}, {ch['voice'].get('timbre', 'natural')} voice)"


def video_prompt(shot: dict, spec: dict) -> str:
    chars = spec["characters"]
    cam = CAMERA.get(shot["camera"], "Cinematic camera")
    amb = {"rain": "rain falling", "city": "distant city traffic", "office": "quiet office hum", "cafe": "café murmur",
           "nature": "birds and wind", "night": "night city ambience", "room": "quiet room tone"}.get(shot["ambience"], "")
    if shot["kind"] == "establishing":
        return f"{cam}. The characters stay silent; subtle natural movement. Ambient sound: {amb}. No dialogue, no music."
    lang = LANG_NAME.get(spec["lang"], spec["lang"])
    said = " Then ".join(
        f"{_who(chars[ln['speaker']])} says in {lang}, in a {EMOTION_VOICE.get(ln.get('emotion'), 'natural')} tone, "
        f"with {EMOTION_FACE.get(ln.get('emotion'), 'a natural expression')}: \"{ln['text']}\""
        for ln in shot["lines"])
    return (f"{cam}. {said} Perfect lip-sync, natural blinking, subtle head movement and reactions from the listener. "
            "Speak exactly these words, in this order, and nothing else. "
            f"Ambient sound: {amb}. No background music.")


# ------------------------------------------------------------------------------------------ steps
def step_references(ctx) -> dict:
    db, spec = ctx["db"], ctx["spec"]
    media = registry.google_media()
    made, reused = [], []
    for key, ch in spec["characters"].items():
        a = reference_asset(db, ch["id"], ch["hash"])
        if a:
            reused.append(key)
            continue
        out = cache_dir("refs") / f"{ch['hash']}.png"
        if not out.exists():
            charge(ctx, f"ref:{ch['hash']}", image_price_micros(get_settings().image_model), f"reference {key}",
                   check_only=True)
            prompt = (f"Photorealistic head-and-shoulders portrait photograph of {describe(ch)}. Facing the camera, "
                      "neutral expression, soft studio key light, plain dark grey background, sharp focus, natural "
                      "skin texture. Fictional person, not a celebrity. No text.")
            info = media.image(prompt, [], out, seed=int(ch["hash"][:6], 16))
            charge(ctx, f"ref:{ch['hash']}", info.cost_usd_micros, f"reference {key}")
            ctx["record"]("references", "image", info)
        put_file(db, out, f"characters/{ch['id']}/photo-{ch['hash'][:12]}.png", "character_ref_photo",
                 ctx["job"].owner_id, {"dna_hash": ch["hash"], "provider": media.id, "synthetic": True},
                 character_id=ch["id"])
        db.commit()
        made.append(key)
    return {"generated": made, "reused": reused, "outputs": []}


def reference_asset(db, character_id: str, dna_hash: str) -> Asset | None:
    for a in db.scalars(select(Asset).where(Asset.character_id == character_id, Asset.kind == "character_ref_photo")
                        .order_by(Asset.created_at.desc())):
        if a.meta.get("dna_hash") == dna_hash and local_path(a.storage_key).exists():
            return a
    return None


def step_shotplan(ctx) -> dict:
    shots = plan_shots(ctx["spec"])
    (ctx["wd"] / "shots.json").write_text(json.dumps(shots, ensure_ascii=False))
    return {"shots": len(shots), "seconds": sum(s["seconds"] for s in shots), "outputs": ["shots.json"]}


def step_keyframes(ctx) -> dict:
    db, spec, wd = ctx["db"], ctx["spec"], ctx["wd"]
    media = registry.google_media()
    shots = json.loads((wd / "shots.json").read_text())
    refs = {k: local_path(reference_asset(db, c["id"], c["hash"]).storage_key) for k, c in spec["characters"].items()}
    model = get_settings().image_model
    for s in shots:
        prompt = keyframe_prompt(s, spec)
        r = [refs[c] for c in s["characters"] if c in refs][:3]
        h = _h(model, prompt, [p.name for p in r], spec["seed"] + s["index"])
        cached = cache_dir("keyframes") / f"{h}.png"
        if not cached.exists():
            charge(ctx, f"kf:{h}", image_price_micros(model), f"keyframe {s['index']}", check_only=True)
            info = media.image(prompt, r, cached, seed=(spec["seed"] + s["index"]) % 2**31)
            charge(ctx, f"kf:{h}", info.cost_usd_micros, f"keyframe {s['index']}")
            ctx["record"]("keyframes", "image", info)
        shutil.copyfile(cached, wd / f"kf_{s['index']:03d}.png")
        s["keyframe_hash"] = h
        _progress(ctx, "keyframes", (s["index"] + 1) / len(shots))
    (wd / "shots.json").write_text(json.dumps(shots, ensure_ascii=False))
    return {"keyframes": len(shots), "outputs": [f"kf_{s['index']:03d}.png" for s in shots]}


def step_video(ctx) -> dict:
    spec, wd, job = ctx["spec"], ctx["wd"], ctx["job"]
    media = registry.google_media()
    s_ = get_settings()
    model = s_.video_model_final if job.quality == "final" else s_.video_model_preview
    shots = json.loads((wd / "shots.json").read_text())
    for s in shots:
        prompt = video_prompt(s, spec)
        h = _h(model, prompt, s["keyframe_hash"], s["seconds"], job.quality, spec["seed"] + s["index"])
        cached = cache_dir("veo") / f"{h}.mp4"
        if not cached.exists():
            cost = veo_price_micros_per_s(model, True) * s["seconds"]
            charge(ctx, f"veo:{h}", cost, f"video shot {s['index']}", check_only=True)
            res = media.video(prompt, wd / f"kf_{s['index']:03d}.png", cached, seconds=s["seconds"],
                              quality=job.quality, seed=(spec["seed"] + s["index"]) % 2**31, negative=NEGATIVE)
            charge(ctx, f"veo:{h}", res.info.cost_usd_micros, f"video shot {s['index']}")
            ctx["record"]("video", "video", res.info)
        shutil.copyfile(cached, wd / f"shot_{s['index']:03d}.mp4")
        _progress(ctx, "video", (s["index"] + 1) / len(shots))
    return {"shots": len(shots), "model": model, "outputs": [f"shot_{s['index']:03d}.mp4" for s in shots]}


def step_assemble(ctx) -> dict:
    """Normalise each Veo clip, concatenate, split picture (silent) and production audio, build cues."""
    wd, job = ctx["wd"], ctx["job"]
    s_ = get_settings()
    H = s_.final_height if job.quality == "final" else s_.preview_height
    W = int(H * 9 / 16) // 2 * 2
    shots = json.loads((wd / "shots.json").read_text())
    norm = []
    for s in shots:
        src, dst = wd / f"shot_{s['index']:03d}.mp4", wd / f"norm_{s['index']:03d}.mp4"
        has_audio = _has_audio(src)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src)]
        if not has_audio:
            cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={s['seconds']}"]
        cmd += ["-t", str(s["seconds"]), "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=24",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "48000", "-ac", "2"]
        if not has_audio:
            cmd += ["-map", "0:v", "-map", "1:a", "-shortest"]
        subprocess.run(cmd + [str(dst)], check=True, capture_output=True)
        norm.append(dst)
    lst = wd / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in norm))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy",
                    "joined.mp4"], cwd=wd, check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "joined.mp4", "-an", "-c:v", "copy", "picture.mp4"],
                   cwd=wd, check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", "joined.mp4", "-vn", "-ac", "1", "-ar", str(SR),
                    "dialogue.wav"], cwd=wd, check=True, capture_output=True)
    write_wav(wd / "ambience.wav", np.zeros(int(sum(s["seconds"] for s in shots) * SR), dtype=np.float32))
    # timeline + caption cues: speech window detected by energy inside each dialogue shot (no ASR yet)
    x, sr = read_wav(wd / "dialogue.wav")
    t, cues, tl_shots = 0.0, [], []
    for s in shots:
        start, end = t, t + s["seconds"]
        tl_shots.append({"index": s["index"], "scene": s["scene"], "kind": s["kind"], "start": start, "end": end,
                         "location": s["location"], "camera": s["camera"], "characters": s["characters"],
                         "focus": s["focus"], "time_of_day": s["time_of_day"], "mood": s["mood"]})
        if s["lines"]:
            seg = x[int(start * sr): int(end * sr)]
            segs = voiced_segments(rms_envelope(seg, sr), min_gap_s=0.35)
            s0, s1 = (segs[0][0], segs[-1][1]) if segs else (0.3, s["seconds"] - 0.3)
            if s1 - s0 < 0.6:
                s0, s1 = 0.3, s["seconds"] - 0.3
            weights = [speech_seconds(ln["text"]) for ln in s["lines"]]
            gap = 0.3 if len(s["lines"]) > 1 else 0.0
            span = (s1 - s0) - gap * (len(s["lines"]) - 1)
            acc = start + s0
            for ln, wgt in zip(s["lines"], weights, strict=True):
                dur = span * wgt / sum(weights)
                words = ln["text"].split()
                wts = [max(1, len(w)) for w in words]
                wl, a2 = [], acc
                for w, wt in zip(words, wts, strict=True):
                    d = dur * wt / sum(wts)
                    wl.append({"word": w, "start": round(a2, 3), "end": round(a2 + d, 3)})
                    a2 += d
                cues.append({"line_index": len(cues), "speaker": ln["speaker"], "text": ln["text"],
                             "emotion": ln.get("emotion", "neutral"), "intensity": ln.get("intensity", 0.7),
                             "start": round(acc, 3), "duration": round(dur, 3), "look_at": ln.get("look_at"),
                             "words": wl, "visemes": [], "envelope": [], "audio": ""})
                acc += dur + gap
        t = end
    tl = {"shots": tl_shots, "cues": cues, "duration": t, "alignment": "energy-window (approximate, no ASR)"}
    (wd / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False))
    # platform render fee, charged once the paid generation is assembled
    fee = max(1, int((s_.render_credits_per_min_final if job.quality == "final" else s_.render_credits_per_min_preview)
                     * t / 60 + 0.999))
    if ledger.consume_credits(ctx["db"], job.owner_id, fee, key=f"job:{job.id}:step:assemble:fee", memo="render fee",
                              ref={"job_id": job.id}):
        job.spent_credits += fee
        ctx["db"].commit()
    return {"duration": round(t, 2), "shots": len(shots), "cues": len(cues), "outputs": ["picture.mp4", "dialogue.wav",
                                                                                         "timeline.json"]}


def _has_audio(p: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                        str(p)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _progress(ctx, step: str, frac: float) -> None:
    job, db = ctx["job"], ctx["db"]
    db.refresh(job)
    if job.status == "cancelled":
        from .orchestrator import Cancelled
        raise Cancelled()
    job.output = {**job.output, "progress": {"step": step, "pct": round(frac * 100)}}
    db.commit()


def estimate(spec: dict, quality: str, db=None) -> dict:
    s_ = get_settings()
    shots = plan_shots(spec)
    model = s_.video_model_final if quality == "final" else s_.video_model_preview
    secs = sum(s["seconds"] for s in shots)
    missing = sum(1 for c in spec["characters"].values()
                  if not (db is not None and reference_asset(db, c["id"], c["hash"])))
    img = image_price_micros(s_.image_model)
    video_m = veo_price_micros_per_s(model, True) * secs
    image_m = img * (len(shots) + missing)
    fee = max(1, int((s_.render_credits_per_min_final if quality == "final" else s_.render_credits_per_min_preview)
                     * secs / 60 + 0.999))
    provider = micros_to_credits(video_m + image_m)
    return {"route": f"realistic (Nano Banana {s_.image_model} + Veo {model}, native audio)", "quality": quality,
            "est_seconds": secs, "shots": len(shots), "reference_portraits_needed": missing,
            "dialogue_words": sum(len(ln["text"].split()) for sc in spec["scenes"] for ln in sc["lines"]),
            "characters": sum(len(ln["text"]) for sc in spec["scenes"] for ln in sc["lines"]),
            "credits": provider + fee, "breakdown_credits": {"platform_render_fee": fee, "provider_pass_through": provider},
            "provider_cost_usd": {"video": round(video_m / 1e6, 2), "images": round(image_m / 1e6, 2),
                                  "total": round((video_m + image_m) / 1e6, 2)},
            "note": "First-take cost. Retakes of individual shots are billed separately."}

