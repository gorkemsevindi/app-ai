"""Studio Preview performance renderer (provider id: studio_preview_local).

A deterministic 2D character performance engine. It is NOT generative video: characters are stylised
vector portraits built from Character DNA. What it does model is the *performance contract* every real
video/lip-sync provider must satisfy, so the rest of the product can be built and QA'd today:
  - phoneme-aligned visemes + audio-energy jaw opening (lip-sync)
  - blended facial expressions: smile, sadness/crying, anger, fear, surprise, tenderness
  - eyebrows, blinking, eye gaze toward the addressed character, head tilt/nod, breathing
  - identity locked to Character DNA (same DNA hash -> pixel-identical identity across episodes)
Premium routes replace this stage with image->video + lip-sync providers behind the same timeline.
"""

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SS = 2  # supersampling factor for anti-aliasing
LW, LH = 320, 450  # bust layer design units

EMOTIONS = ("happy", "sad", "angry", "fear", "surprise", "tender")

VISEME_SHAPES = {  # width factor, open factor, teeth
    "rest": (1.0, 0.0, False), "AA": (1.05, 1.0, False), "EE": (1.22, 0.38, True), "OO": (0.68, 0.75, False),
    "UU": (0.55, 0.35, False), "MBP": (0.95, 0.0, False), "FV": (1.0, 0.16, True), "L": (1.0, 0.55, False),
    "S": (1.1, 0.2, True),
}


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def expression_params(w: dict[str, float]) -> dict[str, float]:
    g = lambda k: float(w.get(k, 0.0))  # noqa: E731
    return {
        "smile": g("happy") + 0.55 * g("tender") - 0.8 * g("sad") - 0.45 * g("angry") - 0.35 * g("fear"),
        "brow_raise": g("surprise") + 0.6 * g("fear") + 0.2 * g("happy") - 0.7 * g("angry"),
        "brow_inner": 1.0 * g("sad") + 0.8 * g("fear") - 1.3 * g("angry"),
        "eye_open": 1 + 0.5 * g("surprise") + 0.35 * g("fear") - 0.3 * g("angry") - 0.2 * g("happy") - 0.15 * g("sad"),
        "squint": 0.45 * g("happy") + 0.25 * g("tender"),
        "tears": max(0.0, g("sad") - 0.45) * 1.8,
        "blush": 0.6 * g("tender") + 0.25 * g("happy"),
        "jaw_drop": 0.55 * g("surprise") + 0.2 * g("fear"),
    }


def draw_bust(look: dict, st: dict) -> Image.Image:
    """Render one character bust (RGBA, design units * SS)."""
    S = SS
    img = Image.new("RGBA", (LW * S, LH * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    skin = hex_rgb(look.get("skin", "#e0ac83"))
    hair = hex_rgb(look.get("hair", "#3b2416"))
    if look.get("age_band") == "senior":
        hair = mix(hair, (190, 190, 190), 0.7)
    outfit = hex_rgb(look.get("outfit", "#2c3e50"))
    accent = hex_rgb(look.get("accent", "#c0392b"))
    eyes_c = hex_rgb(look.get("eyes", "#3b2a1a"))
    fw = float(look.get("face_width", 1.0))
    style = look.get("hair_style", "short")
    p = expression_params(st["w"])
    shade = mix(skin, (40, 20, 20), 0.25)

    def E(cx, cy, rx, ry, **kw):
        d.ellipse([(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S], **kw)

    cx, cy, rx, ry = 160, 170, 92 * fw, 116
    # back hair
    if style == "long":
        d.rounded_rectangle([(cx - rx - 14) * S, (cy - 60) * S, (cx + rx + 14) * S, (cy + 175) * S], 60 * S, fill=hair)
    elif style == "bob":
        d.rounded_rectangle([(cx - rx - 12) * S, (cy - 70) * S, (cx + rx + 12) * S, (cy + 70) * S], 50 * S, fill=hair)
    elif style == "bun":
        E(cx, cy - ry - 18, 36, 32, fill=hair)
    # body / shoulders (breathing)
    breath = st.get("breath", 0.0) * 2
    E(cx, 440 + breath, 150, 112, fill=outfit)
    d.polygon([((cx - 30) * S, (330 + breath) * S), ((cx + 30) * S, (330 + breath) * S), (cx * S, (392 + breath) * S)],
              fill=accent)
    d.rectangle([(cx - 30) * S, 250 * S, (cx + 30) * S, (338 + breath) * S], fill=shade)
    E(cx, 330 + breath, 34, 12, fill=shade)
    # ears + head
    E(cx - rx + 2, cy + 8, 12, 20, fill=shade)
    E(cx + rx - 2, cy + 8, 12, 20, fill=shade)
    E(cx, cy, rx, ry, fill=skin)
    if look.get("beard"):
        bc = mix(hair, skin, 0.25)
        d.chord([(cx - rx + 6) * S, (cy - 10) * S, (cx + rx - 6) * S, (cy + ry + 4) * S], 10, 170, fill=bc)
    # front hair
    if style != "bald":
        d.chord([(cx - rx - 8) * S, (cy - ry - 14) * S, (cx + rx + 8) * S, (cy - 25) * S], 180, 360, fill=hair)
        if style == "curly":
            for k in range(9):
                a = math.pi + k * math.pi / 8
                E(cx + math.cos(a) * (rx + 2), cy - 40 + math.sin(a) * (ry * 0.7), 20, 20, fill=hair)
        d.pieslice([(cx - rx) * S, (cy - ry - 6) * S, (cx + rx * 0.4) * S, (cy - 30) * S], 180, 300, fill=hair)
    else:
        E(cx - rx + 8, cy - 10, 10, 26, fill=hair)
        E(cx + rx - 8, cy - 10, 10, 26, fill=hair)
    if p["blush"] > 0.05:
        bl = mix(skin, (230, 90, 100), min(0.5, p["blush"] * 0.6))
        E(cx - 52, cy + 38, 18, 9, fill=bl)
        E(cx + 52, cy + 38, 18, 9, fill=bl)
    # eyes, lids, brows
    gx, gy = st["gaze"]
    open_ = max(0.0, min(1.5, p["eye_open"])) * (1 - st["blink"])
    for side in (-1, 1):
        ex, ey = cx + side * 38 * fw, cy - 4
        erx, ery = 17, 12
        oy = ery * max(0.08, open_)
        E(ex, ey, erx, oy, fill=(250, 248, 245))
        if open_ > 0.15:
            ix, iy = ex + gx * 7, ey + gy * 3
            E(ix, iy, 8.5, min(8.5, oy), fill=eyes_c)
            E(ix, iy, 4, min(4, oy), fill=(15, 10, 10))
            E(ix + 2.5, iy - 2.5, 1.6, 1.6, fill=(255, 255, 255))
        if p["squint"] > 0.05:
            d.chord([(ex - erx - 3) * S, (ey + oy * (1 - 2 * p["squint"])) * S, (ex + erx + 3) * S,
                     (ey + oy * 2.4) * S], 180, 360, fill=skin)
        d.arc([(ex - erx) * S, (ey - oy) * S, (ex + erx) * S, (ey + oy) * S], 190, 350,
              fill=mix(skin, (30, 20, 20), 0.65), width=3 * S)
        by = ey - 26 - 9 * p["brow_raise"]
        inner = by - 8 * p["brow_inner"]
        outer = by + 2 * p["brow_inner"]
        x_in, x_out = ex - side * 15, ex + side * 19
        d.line([(x_in * S, inner * S), (ex * S, (by - 3) * S), (x_out * S, outer * S)], fill=mix(hair, (0, 0, 0), 0.3),
               width=6 * S, joint="curve")
    if look.get("glasses"):
        for side in (-1, 1):
            ex = cx + side * 38 * fw
            d.ellipse([(ex - 25) * S, (cy - 24) * S, (ex + 25) * S, (cy + 16) * S], outline=(30, 30, 30), width=3 * S)
        d.line([((cx - 13) * S, (cy - 6) * S), ((cx + 13) * S, (cy - 6) * S)], fill=(30, 30, 30), width=3 * S)
    d.line([(cx * S, (cy + 8) * S), ((cx - 6) * S, (cy + 30) * S), ((cx + 4) * S, (cy + 32) * S)], fill=shade,
           width=3 * S, joint="curve")
    if p["tears"] > 0.05:
        ph = st.get("tear_phase", 0.0)
        for side in (-1, 1):
            ty = cy + 10 + (ph * 70) % 70
            E(cx + side * 42 * fw, ty, 4, 7, fill=(150, 200, 240))
    _draw_mouth(d, cx, cy + 62, skin, p, st)
    return img


def _draw_mouth(d: ImageDraw.ImageDraw, mx: float, my: float, skin, p: dict, st: dict) -> None:
    S = SS
    wf, of, teeth = VISEME_SHAPES.get(st["viseme"], VISEME_SHAPES["rest"])
    open_amt = max(of * st["open"], p["jaw_drop"] * 0.6 if st["viseme"] == "rest" else 0)
    smile = max(-1.0, min(1.0, p["smile"]))
    W = 46 * wf * (1 + 0.22 * max(0.0, smile))
    H = 30 * open_amt
    corner = -smile * 9
    xs = np.linspace(-1, 1, 15)
    up = [(mx + x * W / 2, my + corner * x * x - (1 - x * x) * (2 + 0.25 * H)) for x in xs]
    lo = [(mx + x * W / 2, my + corner * x * x + (1 - x * x) * (2 + H)) for x in xs[::-1]]
    lip = mix(skin, (170, 60, 70), 0.55)
    d.polygon([(x * S, y * S) for x, y in up + lo], fill=lip)
    if H > 3.5:
        ui = [(x, y + 2.5) for x, y in up[2:-2]]
        li = [(x, y - 2.5) for x, y in lo[2:-2]]
        d.polygon([(x * S, y * S) for x, y in ui + li], fill=(70, 20, 28))
        if teeth or H > 12:
            band = [(x, y + min(6, H * 0.35)) for x, y in ui[::-1]]
            d.polygon([(x * S, y * S) for x, y in ui + band], fill=(245, 242, 235))
    else:
        d.line([(x * S, (y + 1) * S) for x, y in up], fill=mix(lip, (60, 20, 25), 0.5), width=2 * S)


def _q(v: float, step: float) -> float:
    return round(round(v / step) * step, 3)


def quantize_state(st: dict) -> tuple:
    """Quantise a per-frame state so identical-looking frames share one cached layer."""
    sad = st["w"].get("sad", 0) > 0.45
    return (
        ("blink", _q(st["blink"], 0.25)), ("breath", _q(st.get("breath", 0), 0.5)),
        ("gaze", (_q(st["gaze"][0], 0.25), _q(st["gaze"][1], 0.5))), ("open", _q(st["open"], 0.1)),
        ("tear_phase", _q(st.get("tear_phase", 0), 0.125) if sad else 0.0), ("tilt", _q(st["tilt"], 0.5)),
        ("viseme", st["viseme"]), ("w", tuple((e, _q(st["w"].get(e, 0), 0.1)) for e in EMOTIONS)),
    )


@lru_cache(maxsize=192)
def _scaled_layer(look_items: tuple, qst: tuple, h: int) -> Image.Image:
    """Draw at SS x resolution, downsample once (anti-aliasing) to the on-screen size."""
    look = dict(look_items)
    st = dict(qst)
    st["w"] = dict(st["w"])
    img = draw_bust(look, st)
    return img.resize((int(LW * h / LH), h), Image.BILINEAR, reducing_gap=2.0)


@lru_cache(maxsize=384)
def _posed_layer(look_items: tuple, qst: tuple, h: int, tilt: float) -> Image.Image:
    img = _scaled_layer(look_items, qst, h)
    if abs(tilt) > 0.01:  # head/body tilt pivots at the neck; rotating the small layer keeps this cheap
        img = img.rotate(tilt, resample=Image.BICUBIC, center=(img.width / 2, img.height * 330 / LH))
    return img


def clear_caches() -> None:
    _scaled_layer.cache_clear()
    _posed_layer.cache_clear()


def render_bust(look: dict, st: dict, height_px: int) -> Image.Image:
    q = quantize_state(st)
    tilt = dict(q)["tilt"]
    q = tuple((k, 0.0 if k == "tilt" else v) for k, v in q)
    return _posed_layer(tuple(sorted(look.items())), q, int(height_px), tilt)


# ------------------------------------------------------------------------------- backgrounds
LIGHT = {"warm": (255, 214, 170), "cool": (170, 200, 255), "noir": (200, 200, 200), "night": (110, 130, 200),
         "daylight": (255, 250, 240), "neon": (255, 120, 220)}


def background(loc: dict, w: int, h: int, seed: int, time_of_day: str = "day") -> Image.Image:
    """Procedural set: palette gradient + set dressing per ambience, graded by lighting preset."""
    rng = np.random.default_rng(seed)
    pal = [hex_rgb(c) for c in (loc.get("palette") or ["#2b2d42", "#8d99ae"])]
    light = LIGHT.get(loc.get("lighting", "warm"), LIGHT["warm"])
    if time_of_day == "night":
        light = mix(light, (60, 70, 120), 0.5)
    top, bot = np.array(pal[0], float), np.array(pal[-1], float)
    grad = np.linspace(0, 1, h)[:, None, None]
    arr = (top * (1 - grad) + bot * grad) * np.ones((1, w, 1))
    img = Image.fromarray(arr.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    amb = loc.get("ambience", "room")
    if amb in ("room", "cafe"):
        wx = int(w * 0.55)
        d.rectangle([wx, int(h * 0.12), wx + int(w * 0.32), int(h * 0.42)], fill=mix(light, (255, 255, 255), 0.3))
        d.line([wx + int(w * 0.16), int(h * 0.12), wx + int(w * 0.16), int(h * 0.42)], fill=mix(pal[0], (0, 0, 0), .3), width=6)
        d.rectangle([0, int(h * 0.62), w, h], fill=mix(pal[-1], (20, 12, 8), 0.55))
        d.ellipse([int(w * 0.08), int(h * 0.2), int(w * 0.22), int(h * 0.27)], fill=mix(light, (255, 255, 200), .5))
        if amb == "cafe":
            d.rectangle([0, int(h * 0.30), w, int(h * 0.34)], fill=(70, 120, 160))
    elif amb == "office":
        for i in range(10):
            for j in range(14):
                if rng.random() < 0.55:
                    x0, y0 = int(w * (0.04 + i * 0.095)), int(h * (0.08 + j * 0.035))
                    d.rectangle([x0, y0, x0 + int(w * 0.05), y0 + int(h * 0.018)],
                                fill=mix(light, (255, 255, 180), rng.random() * 0.5))
        d.rectangle([0, int(h * 0.62), w, h], fill=(35, 38, 48))
    elif amb in ("rain", "city", "night"):
        x = 0
        while x < w:
            bw = int(w * rng.uniform(0.12, 0.25))
            bh = int(h * rng.uniform(0.25, 0.55))
            d.rectangle([x, int(h * 0.62) - bh, x + bw, int(h * 0.62)], fill=mix(pal[0], (5, 5, 10), 0.6))
            for _ in range(int(bw * bh / 900)):
                lx, ly = x + int(rng.uniform(4, bw - 8)), int(h * 0.62) - int(rng.uniform(8, bh - 4))
                d.rectangle([lx, ly, lx + 4, ly + 6], fill=(255, 220, 140))
            x += bw + int(w * 0.02)
        d.rectangle([0, int(h * 0.62), w, h], fill=(25, 28, 36))
    elif amb == "nature":
        d.rectangle([0, int(h * 0.6), w, h], fill=(48, 80, 52))
    img = img.filter(ImageFilter.GaussianBlur(radius=max(1, w // 240)))  # depth-of-field feel
    graded = Image.blend(img, Image.new("RGB", img.size, light), 0.12)
    return graded
