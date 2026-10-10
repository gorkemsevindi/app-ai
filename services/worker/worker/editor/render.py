"""V8 final render (Master Spec V8 §3/§6, ADR-4): a render manifest (rm1) → one deterministic ffmpeg filter graph.

- Visual layers in z order over a canvas-sized background: video (trim via in point, speed, reverse), image,
  text (drawtext from a text file, Unicode-safe), shape (rasterised with OpenCV). Transform = position offset from
  the canvas centre, scale relative to "fit inside the canvas", rotation, opacity; keyframes for x / y / scale /
  rotation / opacity become ffmpeg time expressions with the same easing as the shared TS `valueAt`.
- Fade-in transitions; audio: every audio item trimmed, speed via atempo, volume, fades, delayed and mixed.
- Outputs: MP4/H.264, HEVC (MP4), WebM/VP9 for video; PNG / JPEG / WebP (frame at 0 for photo projects).
- Quality presets scale the canvas so its short side is 720 / 1080 / 2160 px (never upscaled)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf")
SHORT_SIDE = {"720p": 720, "1080p": 1080, "2160p": 2160}


def _font(bold: bool) -> str | None:
    order = FONTS if bold else FONTS[1:] + FONTS[:1]
    return next((f for f in order if Path(f).exists()), None)


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-800:]}")


def _hex(c: str) -> str:
    c = (c or "#ffffff").lstrip("#")
    return "0x" + (c if len(c) in (6, 8) else "ffffff")


def _ease(u: str, easing: str) -> str:
    if easing == "ease_in":
        return f"({u})*({u})"
    if easing == "ease_out":
        return f"(1-(1-({u}))*(1-({u})))"
    if easing == "ease_in_out":
        return f"if(lt({u},0.5),2*({u})*({u}),1-2*(1-({u}))*(1-({u})))"
    return u


def kf_expr(kfs: list[dict] | None, base: float, local: str) -> str:
    """Piecewise expression of a keyframed property at local time `local` (seconds) — mirrors manifest.valueAt."""
    if not kfs:
        return f"{base}"
    k = [(x["t_ms"] / 1000, float(x["value"]), x.get("easing", "linear")) for x in kfs]
    expr = f"{k[-1][1]}"
    for i in range(len(k) - 2, -1, -1):
        (ta, va, _), (tb, vb, eb) = k[i], k[i + 1]
        if tb == ta or eb == "hold":
            seg = f"{va}"
        else:
            u = f"(({local})-{ta})/{tb - ta}"
            seg = f"({va}+({vb - va})*{_ease(u, eb)})"
        expr = f"if(lt({local},{tb}),{seg},{expr})"
    return f"if(lt({local},{k[0][0]}),{k[0][1]},{expr})"


def _shape_png(shape: dict, path: Path) -> Path:
    w, h = max(1, int(shape["width"])), max(1, int(shape["height"]))
    img = np.zeros((h, w, 4), np.uint8)
    c = shape.get("fill", "#ffffff").lstrip("#")
    bgr = (int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)) if len(c) >= 6 else (255, 255, 255)
    if shape.get("type") == "ellipse":
        cv2.ellipse(img, (w // 2, h // 2), (w // 2, h // 2), 0, 0, 360, (*bgr, 255), -1, cv2.LINE_AA)
    else:
        img[:] = (*bgr, 255)
    cv2.imwrite(str(path), img)
    return path


def render(manifest: dict, assets: dict[str, Path], fmt: str, quality: str, workdir: Path, watermark: bool = False,
           metadata: dict | None = None) -> Path:
    cv = manifest["canvas"]
    W, H, fps = int(cv["width"]), int(cv["height"]), int(cv["fps"])
    still = fmt in ("png", "jpeg", "webp")
    photo = manifest["type"] == "photo" or still
    dur = max(manifest["duration_ms"], 1) / 1000 if not still else 1 / fps
    bg = f"color=c={_hex(cv['background'])}:s={W}x{H}:r={fps}:d={dur:.3f}"
    inputs: list[list[str]] = [["-f", "lavfi", "-i", bg]]
    graph, last = [], "[0:v]"
    graph.append(f"{last}format=rgba[base]")
    last = "[base]"
    for n, L in enumerate(manifest["layers"]):
        start, end = L["start_ms"] / 1000, L["end_ms"] / 1000
        if photo:
            start, end = 0.0, dur
        length = max(end - start, 1 / fps)
        tf, kf = L["transform"], L.get("keyframes") or {}
        local = f"(t-{start})"
        idx = len(inputs)
        if L["kind"] == "video":
            src = L["in_ms"] / 1000
            inputs.append(["-ss", f"{src:.3f}", "-t", f"{length * L['speed'] + 0.05:.3f}",
                           "-i", str(assets[L["asset"]["id"]])])
            chain = f"[{idx}:v]setpts=(PTS-STARTPTS)/{L['speed']}" + (",reverse" if L.get("reverse") else "")
        elif L["kind"] == "image":
            inputs.append(["-loop", "1", "-t", f"{length:.3f}", "-i", str(assets[L["asset"]["id"]])])
            chain = f"[{idx}:v]setpts=PTS-STARTPTS"
        elif L["kind"] == "shape":
            png = _shape_png(L["shape"], workdir / f"shape{n}.png")
            inputs.append(["-loop", "1", "-t", f"{length:.3f}", "-i", str(png)])
            chain = f"[{idx}:v]setpts=PTS-STARTPTS"
        else:  # text: full-canvas transparent layer with drawtext; position from the transform
            txt = workdir / f"text{n}.txt"
            txt.write_text(L["text"]["content"], encoding="utf-8")
            inputs.append(["-f", "lavfi", "-t", f"{length:.3f}", "-i",
                           f"color=c=black@0.0:s={W}x{H}:r={fps},format=rgba"])
            t = L["text"]
            font = _font(t.get("weight") == "bold")
            size = max(4, int(t["size"] * float(tf.get("scale", 1))))
            xe = kf_expr(kf.get("x"), tf["x"], "t")
            ye = kf_expr(kf.get("y"), tf["y"], "t")
            ax = {"left": "40", "right": "(w-tw-40)"}.get(t.get("align"), "(w-tw)/2")
            box = f":box=1:boxcolor={_hex(t['background'])}:boxborderw=12" if t.get("background") else ""
            alpha = (f":alpha='{kf_expr(kf.get('opacity'), tf['opacity'], 't')}'"
                     if kf.get("opacity") or tf["opacity"] < 1 else "")
            ff = f"fontfile={font}:" if font else ""
            chain = (f"[{idx}:v]setpts=PTS-STARTPTS,drawtext={ff}textfile={txt}:"
                     f"fontsize={size}:fontcolor={_hex(t['color'])}:x={ax}+({xe}):y=(h-th)/2+({ye}){box}{alpha}")
            tr = L.get("transition_in") or {}
            if tr.get("type") in ("fade", "dissolve") and tr.get("ms"):
                chain += f",fade=t=in:st=0:d={tr['ms'] / 1000:.3f}:alpha=1"
            chain += f",setpts=PTS+{start}/TB[l{n}]"
            graph.append(chain)
            en = "" if photo else f":enable='between(t,{start},{end})'"
            graph.append(f"{last}[l{n}]overlay=0:0:eof_action=pass{en}[v{n}]")
            last = f"[v{n}]"
            continue
        # visual media: fit inside the canvas, then transform (shapes keep their own pixel size)
        if L["kind"] == "shape":
            chain += ",format=rgba"
        else:
            chain += f",scale={W}:{H}:force_original_aspect_ratio=decrease,format=rgba"
        sc = kf_expr(kf.get("scale"), tf["scale"], "t")
        if kf.get("scale") or tf["scale"] != 1:
            chain += f",scale=w='trunc(iw*({sc})/2)*2':h='trunc(ih*({sc})/2)*2':eval=frame"
        rot = kf_expr(kf.get("rotation"), tf["rotation"], "t")
        if kf.get("rotation") or tf["rotation"]:
            chain += f",rotate=a='({rot})*PI/180':c=none:ow='hypot(iw,ih)':oh='hypot(iw,ih)'"
        op = kf_expr(kf.get("opacity"), tf["opacity"], "T")
        if kf.get("opacity") or tf["opacity"] < 1:
            chain += f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*({op})'"
        tr = L.get("transition_in") or {}
        if tr.get("type") in ("fade", "dissolve") and tr.get("ms"):
            chain += f",fade=t=in:st=0:d={tr['ms'] / 1000:.3f}:alpha=1"
        chain += f",setpts=PTS+{start}/TB[l{n}]"
        graph.append(chain)
        xe = kf_expr(kf.get("x"), tf["x"], local)
        ye = kf_expr(kf.get("y"), tf["y"], local)
        en = "" if photo else f":enable='between(t,{start},{end})'"
        graph.append(f"{last}[l{n}]overlay=x='(W-w)/2+({xe})':y='(H-h)/2+({ye})':eval=frame:"
                     f"eof_action=pass{en}[v{n}]")
        last = f"[v{n}]"
    if watermark:
        font = _font(True)
        ff = f"fontfile={font}:" if font else ""
        graph.append(f"{last}drawtext={ff}text='AI VIDEO':fontsize={max(14, W // 40)}:"
                     f"fontcolor=white@0.6:borderw=2:bordercolor=black@0.5:x=w-tw-24:y=h-th-24[vw]")
        last = "[vw]"
    s = min(1.0, SHORT_SIDE.get(quality, 1080) / min(W, H))
    ow, oh = int(W * s) // 2 * 2, int(H * s) // 2 * 2
    pix = "yuv420p" if not still else ("yuvj420p" if fmt == "jpeg" else "rgba")
    graph.append(f"{last}scale={ow}:{oh},format={pix}[vout]")
    amaps: list[str] = []
    if not still:
        alabels = []
        for n, A in enumerate(manifest["audio"]):
            idx = len(inputs)
            length = (A["end_ms"] - A["start_ms"]) / 1000
            inputs.append(["-ss", f"{A['in_ms'] / 1000:.3f}", "-t", f"{length * A['speed'] + 0.05:.3f}",
                           "-i", str(assets[A["asset"]["id"]])])
            ch = f"[{idx}:a]asetpts=PTS-STARTPTS"
            sp = A["speed"]
            while sp > 2.0:
                ch += ",atempo=2.0"
                sp /= 2.0
            while sp < 0.5:
                ch += ",atempo=0.5"
                sp /= 0.5
            if sp != 1:
                ch += f",atempo={sp}"
            vol = kf_expr((A.get("keyframes") or {}).get("volume"), A["volume"], "t")
            ch += f",atrim=0:{length:.3f},volume='{vol}':eval=frame"
            if A["fade_in_ms"]:
                ch += f",afade=t=in:st=0:d={A['fade_in_ms'] / 1000:.3f}"
            if A["fade_out_ms"]:
                fo = A["fade_out_ms"] / 1000
                ch += f",afade=t=out:st={max(0.0, length - fo):.3f}:d={fo:.3f}"
            ch += f",adelay={A['start_ms']}:all=1[a{n}]"
            graph.append(ch)
            alabels.append(f"[a{n}]")
        if alabels:
            graph.append("".join(alabels) + f"amix=inputs={len(alabels)}:normalize=0:duration=longest,"
                         f"atrim=0:{dur:.3f},aresample=48000[aout]")
            amaps = ["-map", "[aout]"]
        else:
            inputs.append(["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"])
            amaps = ["-map", f"{len(inputs) - 1}:a"]
    ext = {"mp4": "mp4", "hevc": "mp4", "webm": "webm", "png": "png", "jpeg": "jpg", "webp": "webp"}[fmt]
    out = workdir / f"export.{ext}"
    codec = {"mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                     "-movflags", "+faststart"],
             "hevc": ["-c:v", "libx265", "-preset", "fast", "-crf", "24", "-tag:v", "hvc1", "-c:a", "aac",
                      "-b:a", "160k", "-movflags", "+faststart"],
             "webm": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-row-mt", "1", "-deadline", "realtime",
                      "-c:a", "libopus"],
             "png": ["-frames:v", "1", "-c:v", "png"], "jpeg": ["-frames:v", "1", "-q:v", "2"],
             "webp": ["-frames:v", "1", "-c:v", "libwebp_anim", "-lossless", "0", "-quality", "90"]}[fmt]
    meta = []
    for k, v in (metadata or {}).items():
        meta += ["-metadata", f"{k}={v}"]
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for i in inputs:
        cmd += i
    script = workdir / "graph.txt"
    script.write_text(";\n".join(graph), encoding="utf-8")
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]", *amaps, "-r", str(fps), *codec, *meta]
    if not still:
        cmd += ["-t", f"{dur:.3f}"]
    cmd.append(str(out))
    _run(cmd)
    return out
