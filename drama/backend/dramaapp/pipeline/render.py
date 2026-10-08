"""Frame composition, audio mix, captions, mux, HLS packaging. Each function is one pipeline step
operating on files in a job work dir, so steps are individually resumable and testable."""

import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..providers.music_local import SR, write_wav
from ..providers.tts_espeak import read_wav
from .performer import background, render_bust

FPS = 24
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"]
SPEAKER_COLORS = ["#FFD166", "#8BE9FD", "#FF8FAB", "#B8F2A6"]


def ffmpeg_bin() -> str:
    b = shutil.which("ffmpeg")
    if not b:
        raise RuntimeError("ffmpeg not installed")
    return b


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")
    return r


# ----------------------------------------------------------------------------------------- frames
class CharacterAnimator:
    """Computes per-frame performance state for one character from the dialogue cues."""

    def __init__(self, key: str, cues: list[dict], seed: int):
        self.key = key
        self.own = [c for c in cues if c["speaker"] == key]
        self.all = cues
        rng = random.Random(seed)
        self.blinks, t = [], rng.uniform(0.5, 2.0)
        while t < 3600:
            self.blinks.append(t)
            t += rng.uniform(2.2, 5.0)
        self.phase = rng.uniform(0, 6.28)

    def _active(self, t: float, cues: list[dict]) -> dict | None:
        for c in cues:
            if c["start"] - 0.05 <= t <= c["start"] + c["duration"] + 0.05:
                return c
        return None

    def _last(self, t: float) -> dict | None:
        last = None
        for c in self.own:
            if c["start"] - 0.25 <= t:
                last = c
        return last

    def state(self, t: float, gaze_x: float) -> dict:
        speaking = self._active(t, self.own)
        w: dict[str, float] = {}
        last = self._last(t)
        if last and last["emotion"] != "neutral":
            ramp = min(1.0, max(0.0, (t - (last["start"] - 0.25)) / 0.35))
            age = t - (last["start"] + last["duration"])
            decay = 1.0 if age < 0 else max(0.35, 1 - age / 4)
            w[last["emotion"]] = last["intensity"] * ramp * decay
        other = self._active(t, self.all)
        if other and other["speaker"] != self.key and other["emotion"] in ("angry", "surprise", "sad"):
            react = {"angry": "fear", "surprise": "surprise", "sad": "sad"}[other["emotion"]]
            w[react] = max(w.get(react, 0), 0.35 * other["intensity"])
        viseme, open_ = "rest", 0.0
        if speaking:
            for v in speaking["visemes"]:
                if v["t"] <= t:
                    viseme = v["viseme"]
                else:
                    break
            env = speaking["envelope"]
            i = int((t - speaking["start"]) * 100)
            if 0 <= i < len(env):
                open_ = 0.35 + 0.65 * min(1.0, env[i] * 1.6)
        bl = 0.0
        for b in self.blinks:
            if b > t + 0.2:
                break
            if abs(t - b) < 0.09:
                bl = 1 - abs(t - b) / 0.09
        amp = 2.4 if speaking else 0.9
        tilt = amp * math.sin(t * (2.1 if speaking else 0.7) + self.phase)
        return {"w": w, "viseme": viseme, "open": open_, "blink": bl, "gaze": (gaze_x, 0.0), "tilt": tilt,
                "breath": math.sin(t * 1.6 + self.phase), "tear_phase": (t * 0.7) % 1.0}


def _camera(kind: str, u: float, t: float) -> tuple[float, float, float]:
    """zoom, x-offset, y-offset (fractions of frame) for camera move at shot progress u."""
    e = u * u * (3 - 2 * u)
    if kind == "push_in":
        return 1 + 0.10 * e, 0.0, 0.0
    if kind == "pull_out":
        return 1.10 - 0.10 * e, 0.0, 0.0
    if kind == "pan_left":
        return 1.06, 0.04 - 0.08 * e, 0.0
    if kind == "pan_right":
        return 1.06, -0.04 + 0.08 * e, 0.0
    if kind == "handheld":
        return 1.05, 0.006 * math.sin(t * 2.3) + 0.004 * math.sin(t * 5.1), 0.005 * math.sin(t * 3.1)
    return 1.0, 0.0, 0.0


FACE_Y = 170 / 450  # face centre as a fraction of bust height (performer design units)


def _layout(shot: dict, W: int, H: int) -> list[tuple[str, float, float, float]]:
    """(character, centre x, bust height, bottom y). Faces sit in the upper ~45% of the frame so captions
    (lower safe zone) never cover a speaking mouth."""
    chars = shot["characters"]
    k = shot["kind"]

    def bottom(face_frac: float, h: float) -> float:
        return face_frac * H + (1 - FACE_Y) * h

    if k in ("establishing", "two"):
        n = len(chars)
        h = H * (0.42 if k == "establishing" else 0.60) * (1.0 if n <= 2 else 0.85)
        face = 0.50 if k == "establishing" else 0.42
        return [(c, W * (i + 1) / (n + 1), h, bottom(face, h)) for i, c in enumerate(chars)]
    focus = shot["focus"] or chars[0]
    h = H * {"single": 0.78, "close": 1.05}.get(k, 0.92)
    return [(focus, W * 0.5, h, bottom(0.40, h))]


def render_video(manifest: dict, spec: dict, out: Path, W: int, H: int, on_progress=None) -> dict:
    shots, cues = manifest["shots"], manifest["cues"]
    duration = manifest["duration"]
    looks = {k: v["look"] for k, v in spec["characters"].items()}
    anim = {k: CharacterAnimator(k, cues, seed=hash(k) & 0xFFFF) for k in looks}
    order = {k: i for i, k in enumerate(spec["characters"])}
    proc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                             "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                            stdin=subprocess.PIPE)
    bg_cache: dict[int, Image.Image] = {}
    n_frames = int(round(duration * FPS))
    rng = np.random.default_rng(spec.get("seed", 7))
    try:
        for f in range(n_frames):
            t = f / FPS
            shot = next((s for s in shots if s["start"] <= t < s["end"]), shots[-1])
            u = (t - shot["start"]) / max(1e-3, shot["end"] - shot["start"])
            z, ox, oy = _camera(shot["camera"], u, t)
            if shot["index"] not in bg_cache:
                bg_cache.clear()
                loc = spec["locations"].get(shot["location"], {})
                bg_cache[shot["index"]] = background(loc, int(W * 1.2), int(H * 1.2), seed=hash(shot["location"]) & 0xFFFF,
                                                     time_of_day=shot["time_of_day"])
            big = bg_cache[shot["index"]]
            # crop window shrinks as the camera zooms in; pans move it across the 1.2x oversized set
            cw, ch = W * 1.2 / (z * 1.12), H * 1.2 / (z * 1.12)
            cx = big.width / 2 + ox * W * 0.6
            cy = big.height / 2 + oy * H
            frame = big.resize((W, H), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
            layout = _layout(shot, W, H)
            xs = {c: x for c, x, _, _ in layout}
            speaking_cue = next((c for c in cues if c["start"] <= t <= c["start"] + c["duration"]), None)
            for c, x, h, bottom in layout:
                target = None
                if speaking_cue:
                    target = speaking_cue["look_at"] if speaking_cue["speaker"] == c else speaking_cue["speaker"]
                if target in xs and target != c:
                    gx = 0.9 if xs[target] > x else -0.9
                elif target and target in order and c in order:
                    gx = 0.55 if order[target] > order[c] else -0.55  # off-screen partner
                else:
                    gx = 0.0
                st = anim[c].state(t, gx)
                hz = int(h * z / 8) * 8
                bust = render_bust(looks[c], st, hz)
                px = int(W / 2 + (x - W / 2) * z + ox * W - bust.width / 2)
                py = int(H / 2 + (bottom - H / 2) * z + oy * H - bust.height)
                if py + bust.height < H:  # extend the torso to the frame edge
                    ImageDraw.Draw(frame).rectangle(
                        [px + bust.width * 0.035, py + bust.height - 2, px + bust.width * 0.965, H],
                        fill=tuple(int(looks[c].get("outfit", "#2c3e50").lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)))
                frame.paste(bust, (px, py), bust)
            loc = spec["locations"].get(shot["location"], {})
            if loc.get("ambience") == "rain":
                d = ImageDraw.Draw(frame)
                for _ in range(60):
                    x0, y0 = rng.uniform(0, W), rng.uniform(0, H)
                    d.line([(x0, y0), (x0 - 4, y0 + H * 0.03)], fill=(180, 190, 220), width=1)
            arr = np.asarray(frame, dtype=np.uint8)
            # fade through black at scene boundaries
            if shot["kind"] == "establishing":
                fade = min(1.0, (t - shot["start"]) / 0.3) if shot["scene"] > 0 or t < 0.3 else 1.0
                if fade < 1:
                    arr = (arr * fade).astype(np.uint8)
            if t > duration - 0.5:
                arr = (arr * max(0.0, (duration - t) / 0.5)).astype(np.uint8)
            proc.stdin.write(arr.tobytes())
            if on_progress and f % 48 == 0:
                on_progress(f / n_frames)
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError("ffmpeg video encode failed")
    return {"frames": n_frames, "fps": FPS, "width": W, "height": H}


# ----------------------------------------------------------------------------------------- audio
def _resample(x: np.ndarray, sr: int, target: int = SR) -> np.ndarray:
    if sr == target:
        return x
    n = int(len(x) * target / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def build_dialogue_track(cues: list[dict], duration: float, out: Path) -> None:
    track = np.zeros(int(duration * SR) + SR, dtype=np.float32)
    for c in cues:
        x, sr = read_wav(Path(c["audio"]))
        x = _resample(x, sr) * 0.9
        s = int(c["start"] * SR)
        e = min(len(track), s + len(x))
        track[s:e] += x[: e - s]
    write_wav(out, track[: int(duration * SR)])


def build_ambience(shots: list[dict], spec: dict, duration: float, music_provider, workdir: Path, out: Path) -> None:
    track = np.zeros(int(duration * SR), dtype=np.float32)
    scenes: dict[int, list[dict]] = {}
    for s in shots:
        scenes.setdefault(s["scene"], []).append(s)
    for si, ss in scenes.items():
        start, end = ss[0]["start"], ss[-1]["end"]
        loc = spec["locations"].get(ss[0]["location"], {})
        p = workdir / f"amb_{si}.wav"
        music_provider.ambience(kind=loc.get("ambience", "room"), duration=end - start, seed=si + 11, out=p)
        x, _ = read_wav(p)
        s0 = int(start * SR)
        e0 = min(len(track), s0 + len(x))
        track[s0:e0] += x[: e0 - s0]
    write_wav(out, track)


def mix_audio(dialogue: Path, music: Path, ambience: Path, out: Path) -> dict:
    """Ducking (sidechain on dialogue), mix, EBU R128 loudness normalisation to -14 LUFS (streaming)."""
    fc = ("[1:a]aformat=channel_layouts=stereo,volume=0.55[m];[0:a]aformat=channel_layouts=stereo,asplit=2[d1][d2];"
          "[m][d1]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[duck];"
          "[2:a]aformat=channel_layouts=stereo,volume=0.6[amb];"
          "[d2][duck][amb]amix=inputs=3:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11[out]")
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(dialogue), "-i", str(music), "-i", str(ambience),
         "-filter_complex", fc, "-map", "[out]", "-ar", "48000", "-c:a", "aac", "-b:a", "160k", str(out)])
    return {"loudness_target_lufs": -14}


# ----------------------------------------------------------------------------------------- captions
def _fmt_ts(t: float, sep: str) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}{sep}{int(round((s - int(s)) * 1000)):03d}"


def caption_chunks(cues: list[dict], max_chars: int = 34) -> list[dict]:
    """Split cues into on-screen chunks (≤2 lines) using word timings."""
    chunks = []
    for c in cues:
        cur: list[dict] = []
        for w in c["words"]:
            if cur and len(" ".join(x["word"] for x in cur + [w])) > max_chars * 2:
                chunks.append({"speaker": c["speaker"], "words": cur})
                cur = []
            cur.append(w)
        if cur:
            chunks.append({"speaker": c["speaker"], "words": cur})
    for ch in chunks:
        ch["start"], ch["end"] = ch["words"][0]["start"], ch["words"][-1]["end"] + 0.25
        ch["text"] = " ".join(w["word"] for w in ch["words"])
    for a, b in zip(chunks, chunks[1:], strict=False):
        a["end"] = min(a["end"], b["start"] - 0.01)
    return chunks


def write_captions(cues: list[dict], spec: dict, workdir: Path, W: int, H: int) -> dict:
    chunks = caption_chunks(cues)
    names = {k: v["name"] for k, v in spec["characters"].items()}
    colors = {k: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, k in enumerate(spec["characters"])}
    srt, vtt = [], ["WEBVTT", ""]
    for i, ch in enumerate(chunks, 1):
        srt += [str(i), f"{_fmt_ts(ch['start'], ',')} --> {_fmt_ts(ch['end'], ',')}", ch["text"], ""]
        vtt += [f"{_fmt_ts(ch['start'], '.')} --> {_fmt_ts(ch['end'], '.')} line:80%",
                f"<v {names.get(ch['speaker'], ch['speaker'])}>{ch['text']}", ""]
    (workdir / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    (workdir / "captions.vtt").write_text("\n".join(vtt), encoding="utf-8")
    # ASS with karaoke word highlight, speaker colours and a safe zone above the app's bottom UI
    fs = int(H * 0.042)
    margin_v = int(H * 0.20)

    def ass_col(hx: str) -> str:
        hx = hx.lstrip("#")
        return f"&H00{hx[4:6]}{hx[2:4]}{hx[0:2]}"

    styles = "\n".join(
        f"Style: {k},DejaVu Sans,{fs},{ass_col(colors[k])},&H00FFFFFF,&H00101010,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, fs // 12)},1,2,{int(W * 0.07)},{int(W * 0.07)},{margin_v},1" for k in spec["characters"])
    ev = []
    for ch in chunks:
        parts = []
        for w in ch["words"]:
            cs = max(1, int(round((w["end"] - w["start"]) * 100)))
            parts.append(f"{{\\k{cs}}}{w['word']}")
        ev.append(f"Dialogue: 0,{_fmt_ts(ch['start'], '.')[:-1]},{_fmt_ts(ch['end'], '.')[:-1]},{ch['speaker']},,0,0,0,,"
                  + " ".join(parts))
    ass = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n[V4+ Styles]\n"
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
           f"MarginR, MarginV, Encoding\n{styles}\n\n[Events]\n"
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + "\n".join(ev) + "\n")
    (workdir / "captions.ass").write_text(ass, encoding="utf-8")
    return {"chunks": len(chunks), "files": ["captions.srt", "captions.vtt", "captions.ass"]}


def compose_final(workdir: Path, video: str, audio: str, ass: str | None, out: str) -> None:
    """Mux picture + mix, burn stylised captions (optional) and embed the synthetic-media disclosure."""
    vf = "vignette=PI/6"
    if ass:
        vf = f"ass={ass}," + vf
    r = subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", video, "-i", audio, "-vf", vf,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
                        "-shortest", "-movflags", "+faststart", "-metadata", "comment=AI-generated synthetic media",
                        out], cwd=workdir, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not (workdir / out).exists():
        raise RuntimeError(f"compose failed: {r.stderr[-1500:]}")


def package_hls(mp4: Path, outdir: Path, H: int) -> dict:
    """Adaptive HLS: two renditions (full + half height) with a master playlist."""
    outdir.mkdir(parents=True, exist_ok=True)
    renditions = [(H, "2400k"), (H // 2 if H >= 720 else int(H * 0.75) // 2 * 2, "900k")]
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    for h, br in renditions:
        name = f"{h}p"
        w = int(h * 9 / 16) // 2 * 2
        run([ffmpeg_bin(), "-y", "-loglevel", "error", "-i", str(mp4), "-vf", f"scale={w}:{h}", "-c:v", "libx264",
             "-preset", "veryfast", "-b:v", br, "-maxrate", br, "-bufsize", br, "-g", "48", "-keyint_min", "48",
             "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-hls_time", "4", "-hls_playlist_type", "vod",
             "-hls_segment_filename", str(outdir / f"{name}_%03d.ts"), str(outdir / f"{name}.m3u8")])
        bw = int(br.rstrip("k")) * 1000 + 128000
        lines += [f"#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={w}x{h}", f"{name}.m3u8"]
    (outdir / "master.m3u8").write_text("\n".join(lines) + "\n")
    return {"renditions": [f"{h}p" for h, _ in renditions]}


def thumbnail(mp4: Path, at: float, title: str, out: Path) -> None:
    run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(mp4), "-frames:v", "1", str(out)])
    img = Image.open(out).convert("RGB")
    d = ImageDraw.Draw(img)
    font = next((ImageFont.truetype(p, int(img.height * 0.06)) for p in FONT_PATHS if Path(p).exists()),
                ImageFont.load_default())
    tw = d.textlength(title, font=font)
    y = int(img.height * 0.08)
    d.text(((img.width - tw) / 2, y), title, font=font, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
    img.save(out, quality=88)


def write_json(p: Path, data) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
