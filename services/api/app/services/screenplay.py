"""Screenplay parser + shot planner (Master Spec V7 §2/§9: script → episode → scene → shot).

Deterministic and labelled as such: it never invents story, never rewrites dialogue. Supported input:
- scene headings: `INT. KITCHEN - NIGHT`, `EXT.`, `İÇ.`, `DIŞ.`, `SAHNE 3`, `SCENE 3`, `# Title`
- character cues in CAPITALS on their own line, optional parenthetical next line `(fısıltıyla)` / `(shouting)`,
  then the dialogue lines; or inline `NAME: line`
- everything else is action/description.

The shot planner sizes each scene from its dialogue (reading speed) and action, scales the whole episode to the
target duration (10–30 min episodes = many short shots), and splits scenes into shots of the allowed durations
with at most 6 lines each. Dialogue text is copied byte-for-byte (`exact`), so slang and swearing survive."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^\s*(INT\.?|EXT\.?|INT/EXT\.?|İÇ\.?|DIŞ\.?|IC\.|DIS\.|SAHNE\b|SCENE\b|#)\s*(.*)$",
                        re.IGNORECASE)
INLINE_RE = re.compile(r"^\s*([A-ZÇĞİÖŞÜa-zçğıöşü][\wÇĞİÖŞÜçğıöşü .'-]{0,38}?)\s*(\(([^)]{1,40})\))?\s*:\s*(.+)$")
PAREN_RE = re.compile(r"^\s*\(([^)]{1,60})\)\s*$")
TIME_WORDS = {"night": "night", "gece": "night", "day": "day", "gündüz": "day", "gunduz": "day", "morning": "morning",
              "sabah": "morning", "evening": "evening", "akşam": "evening", "aksam": "evening", "dawn": "dawn",
              "şafak": "dawn", "dusk": "dusk"}
DELIVERY = {"shout": "shout", "shouting": "shout", "yelling": "shout", "bağırarak": "shout", "bagirarak": "shout",
            "bağırır": "shout", "whisper": "whisper", "whispering": "whisper", "fısıltıyla": "whisper",
            "fisiltiyla": "whisper", "fısıldar": "whisper", "crying": "cry", "ağlayarak": "cry", "aglayarak": "cry",
            "sarcastic": "sarcastic", "alaycı": "mock", "alayci": "mock", "laughing": "laugh", "gülerek": "laugh",
            "angry": "angry", "öfkeyle": "angry", "ofkeyle": "angry", "sinirli": "angry", "calm": "calm",
            "sakin": "calm"}
ACTION_WORDS = r"\b(fight|run|chase|crash|explod|punch|kick|shoot|kavga|koş|kos|kovala|patla|vur|tekme|yumruk|çarp)"
LANDSCAPE_WORDS = r"\b(sky|sea|city|mountain|landscape|panorama|gökyüzü|deniz|şehir|dağ|manzara)"
MONTAGE_WORDS = r"\b(montage|montaj|time passes|zaman geçer)"
WORDS_PER_SECOND = 2.5


def _is_cue(line: str) -> bool:
    s = line.strip()
    core = re.sub(r"\(.*?\)", "", s).strip()
    return (1 <= len(core) <= 40 and core == core.upper() and any(ch.isalpha() for ch in core)
            and not HEADING_RE.match(s) and not core.endswith((".", "!", "?")) and len(core.split()) <= 4)


def slug(name: str) -> str:
    t = unicodedata.normalize("NFKD", name.lower()).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return (t or "x")[:32]


@dataclass
class Line:
    character: str
    text: str
    delivery: str | None = None
    emotion: str | None = None


@dataclass
class ParsedScene:
    heading: str
    location: str | None = None
    time_of_day: str | None = None
    action: list[str] = field(default_factory=list)
    beats: list[tuple[str, object]] = field(default_factory=list)  # ("action", str) | ("line", Line)


def parse(text: str) -> list[ParsedScene]:
    scenes: list[ParsedScene] = []
    cur: ParsedScene | None = None
    lines = (text or "").replace("\r\n", "\n").split("\n")
    i = 0

    def scene() -> ParsedScene:
        nonlocal cur
        if cur is None:
            cur = ParsedScene(heading="")
            scenes.append(cur)
        return cur

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s:
            continue
        m = HEADING_RE.match(s)
        if m and (m.group(1) != "#" or s.startswith("#")):
            rest = m.group(2).strip(" .-:")
            loc, tod = rest, None
            parts = re.split(r"\s[-–—]\s|\s·\s", rest)
            if len(parts) > 1 and parts[-1].strip().lower() in TIME_WORDS:
                tod, loc = TIME_WORDS[parts[-1].strip().lower()], " - ".join(parts[:-1])
            cur = ParsedScene(heading=s.lstrip("#").strip()[:120], location=loc[:120] or None, time_of_day=tod)
            scenes.append(cur)
            continue
        if _is_cue(s) and i < len(lines) and lines[i].strip():
            name = re.sub(r"\(.*?\)", "", s).strip()
            paren = None
            if PAREN_RE.match(lines[i]):
                paren = PAREN_RE.match(lines[i]).group(1).strip()
                i += 1
            said = []
            while i < len(lines) and lines[i].strip() and not _is_cue(lines[i]) and not HEADING_RE.match(lines[i]):
                if PAREN_RE.match(lines[i]) and said:  # a new parenthetical starts a new line of the same character
                    scene().beats.append(("line", _line(name, " ".join(said), paren)))
                    paren, said = PAREN_RE.match(lines[i]).group(1).strip(), []
                else:
                    said.append(lines[i].strip())
                i += 1
            if said:
                scene().beats.append(("line", _line(name, " ".join(said), paren)))
            continue
        m = INLINE_RE.match(raw)
        if m and len(m.group(1).split()) <= 3 and not re.search(r"https?$", m.group(1)):
            scene().beats.append(("line", _line(m.group(1).strip(), m.group(4).strip(), m.group(3))))
            continue
        scene().beats.append(("action", s))
        scene().action.append(s)
    return [sc for sc in scenes if sc.beats]


def _line(name: str, text: str, paren: str | None) -> Line:
    delivery = emotion = None
    if paren:
        low = paren.lower().strip()
        delivery = next((v for k, v in DELIVERY.items() if k in low), None)
        emotion = None if delivery else low[:40]
    return Line(character=name, text=text, delivery=delivery, emotion=emotion)


def classify(sc: ParsedScene) -> str:
    lines = [b for b in sc.beats if b[0] == "line"]
    act = " ".join(sc.action).lower()
    if re.search(MONTAGE_WORDS, act):
        return "montage"
    if re.search(ACTION_WORDS, act):
        return "action"
    if not lines and re.search(LANDSCAPE_WORDS, act):
        return "landscape"
    if lines:
        return "dialogue"
    return "transition" if len(act) < 60 else "landscape"


def _seconds(sc: ParsedScene) -> float:
    total = 0.0
    for kind, b in sc.beats:
        if kind == "line":
            total += len(b.text.split()) / WORDS_PER_SECOND + 0.6
        else:
            total += 3.0
    return max(total, 4.0)


CAMERAS = {"dialogue": ["medium shot", "over-the-shoulder", "close-up", "two-shot"],
           "action": ["wide tracking shot", "handheld close-up", "low angle", "whip pan"],
           "landscape": ["wide establishing shot", "slow aerial", "static wide"],
           "montage": ["quick cut", "match cut", "slow push-in"],
           "transition": ["static wide", "slow pan"], "animation": ["medium shot", "wide shot"]}


def plan(scenes: list[ParsedScene], target_s: int, allowed: list[int], cast_keys: dict[str, str],
         default_style: str | None = None, max_lines: int = 6) -> tuple[dict, dict]:
    """-> (storyboard fragment {scenes, characters}, report). `cast_keys` maps a lower-cased name/alias to the
    storyboard character key of a cast member; unknown speakers become uncast plain characters (reported)."""
    allowed = sorted(int(x) for x in allowed)
    est = [_seconds(sc) for sc in scenes]
    scale = max(0.5, min(4.0, target_s / sum(est))) if est else 1.0
    # pass 1: size every scene (shot count n, shot length per), then correct the total towards the target
    sizes = []
    for sc, secs in zip(scenes, est, strict=True):
        dur = max(allowed[0], secs * scale)
        n_lines = sum(1 for k, _ in sc.beats if k == "line")
        min_n = max(1, -(-n_lines // max_lines))
        n = min(40, max(min_n, round(dur / allowed[-1])))
        sizes.append({"dur": dur, "min_n": min_n, "n": n, "per": min(allowed, key=lambda d: abs(d - dur / n))})

    def total_s() -> int:
        return sum(z["n"] * z["per"] for z in sizes)

    for _ in range(4000):
        t = total_s()
        if t > target_s * 1.02:
            z = max(sizes, key=lambda z: z["n"] * z["per"] - z["dur"])
            lower = [d for d in allowed if d < z["per"]]
            if lower:
                z["per"] = lower[-1]
            elif z["n"] > z["min_n"]:
                z["n"] -= 1
            else:
                z["dur"] = z["n"] * z["per"]  # can't shrink: stop considering it
                if all(x["n"] * x["per"] <= x["dur"] for x in sizes):
                    break
        elif t < target_s * 0.98:
            z = min(sizes, key=lambda z: z["n"] * z["per"] - z["dur"])
            higher = [d for d in allowed if d > z["per"]]
            if higher:
                z["per"] = higher[0]
            elif z["n"] < 40:
                z["n"] += 1
            else:
                break
        else:
            break
    out_scenes, chars, uncast, total = [], {}, set(), 0
    line_no = 0
    for si, (sc, z) in enumerate(zip(scenes, sizes, strict=True), 1):
        stype = classify(sc)
        beats = list(sc.beats)
        lines = [b for k, b in beats if k == "line"]
        n, per = z["n"], z["per"]
        key = f"s{si}"
        speakers = []
        for ln in lines:
            k = cast_keys.get(ln.character.lower()) or cast_keys.get(slug(ln.character))
            if k is None:
                k = slug(ln.character)
                uncast.add(ln.character)
            chars.setdefault(k, ln.character)
            if k not in speakers:
                speakers.append(k)
        if len(lines) > n * max_lines:  # never drop dialogue silently
            raise ValueError(f"scene {si} has {len(lines)} lines; split it into smaller scenes")
        action = [b for kind, b in beats if kind == "action"]
        shots = []
        per_shot = -(-len(lines) // n) if lines else 0  # fill shots in order, never more than max_lines each
        for j in range(n):
            chunk = lines[j * per_shot:(j + 1) * per_shot] if lines else []
            desc = action[j * len(action) // n:(j + 1) * len(action) // n] if action else []
            text = " ".join(desc) or (f"{', '.join(chars[k] for k in speakers)} talk"
                                      + (f" in {sc.location}" if sc.location else ""))
            dialogue, t = [], 0.0
            for ln in chunk[:max_lines]:
                line_no += 1
                k = cast_keys.get(ln.character.lower()) or cast_keys.get(slug(ln.character)) or slug(ln.character)
                d = {"id": f"{key}-l{line_no}", "character": k, "text": ln.text,
                     "start_s": round(min(t, per - 0.5), 2), "exact": True}
                if ln.delivery:
                    d["delivery"] = ln.delivery
                if ln.emotion:
                    d["emotion"] = ln.emotion
                dialogue.append(d)
                t += min(per / max(1, len(chunk)), len(ln.text.split()) / WORDS_PER_SECOND + 0.6)
            in_shot = sorted({d["character"] for d in dialogue}) or speakers[:4]
            prompt = text if len(text) >= 5 else f"{sc.heading or 'Scene'}: {text}".strip()
            shots.append({"key": f"{key}x{j + 1}", "duration_s": per, "prompt": prompt[:1500],
                          "camera": CAMERAS[stype][(j + si) % len(CAMERAS[stype])],
                          "characters": in_shot[:4], "dialogue": dialogue,
                          "caption": None, "transition": "cut" if j else "fade"})
            total += per
        scene = {"key": key, "title": sc.heading[:120], "shots": shots, "scene_type": stype}
        if sc.location:
            scene["location"] = sc.location
        if sc.time_of_day:
            scene["time_of_day"] = sc.time_of_day
        if speakers:
            scene["characters_present"] = speakers[:12]
        out_scenes.append(scene)
    report = {"scenes": len(out_scenes), "shots": sum(len(s["shots"]) for s in out_scenes), "duration_s": total,
              "target_s": target_s, "scale": round(scale, 3), "lines": line_no, "uncast_speakers": sorted(uncast),
              "planner": "rule-based screenplay planner (not AI): dialogue copied exactly"}
    return {"scenes": out_scenes, "characters": chars, "visual_style": default_style}, report
