"""Scene plan -> shot timeline. Pure functions (unit-tested), no I/O."""

from dataclasses import asdict, dataclass, field

LINE_PAD_S = 0.35  # silence after each line
LEAD_IN_S = 0.15  # speech starts slightly after the cut


@dataclass
class DialogueCue:
    line_index: int  # global index across the episode
    speaker: str
    text: str
    emotion: str
    intensity: float
    start: float  # absolute episode time of the speech
    duration: float
    look_at: str | None
    words: list[dict] = field(default_factory=list)  # [{"word","start","end"}] absolute
    visemes: list[dict] = field(default_factory=list)  # [{"t","viseme"}] absolute
    envelope: list[float] = field(default_factory=list)  # 100 Hz, relative to start
    audio: str = ""


@dataclass
class Shot:
    index: int
    scene: int
    kind: str  # establishing|two|single|close|reaction
    start: float
    end: float
    location: str
    time_of_day: str
    mood: str
    camera: str
    characters: list[str]
    focus: str | None
    cue: int | None  # DialogueCue index spoken in this shot


def plan(scenes: list[dict], clip_durations: list[float], target_s: float) -> tuple[list[Shot], list[dict]]:
    """Return shots and per-line absolute (start, duration). clip_durations is indexed by global line."""
    shots: list[Shot] = []
    starts: list[dict] = []
    t = 0.0
    li = 0
    total_speech = sum(clip_durations) + LINE_PAD_S * len(clip_durations)
    n_scenes = max(1, len(scenes))
    # establishing / reaction time is what remains to hit the target, clamped to sensible bounds
    spare = max(0.0, target_s - total_speech)
    est = min(3.5, max(1.2, spare * 0.7 / n_scenes))
    react = min(2.0, max(0.8, spare * 0.3 / n_scenes))
    for si, sc in enumerate(scenes):
        chars = sc["characters"]
        common = dict(scene=si, location=sc["location"], time_of_day=sc.get("time_of_day", "day"),
                      mood=sc.get("mood", "tense"))
        shots.append(Shot(index=len(shots), kind="establishing", start=t, end=t + est, camera="pull_out" if si else "push_in",
                          characters=chars, focus=None, cue=None, **common))
        t += est
        for k, ln in enumerate(sc["lines"]):
            dur = clip_durations[li]
            hot = ln.get("emotion") in ("angry", "fear", "surprise", "sad") and ln.get("intensity", 0.7) >= 0.75
            kind = "close" if hot else ("two" if k == 0 and len(chars) > 1 else "single")
            length = LEAD_IN_S + dur + LINE_PAD_S
            shots.append(Shot(index=len(shots), kind=kind, start=t, end=t + length,
                              camera=sc.get("camera", "push_in") if kind != "two" else "static",
                              characters=chars, focus=ln["speaker"], cue=li, **common))
            starts.append({"start": round(t + LEAD_IN_S, 3), "duration": dur})
            t += length
            li += 1
        last = sc["lines"][-1] if sc["lines"] else None
        listener = (last or {}).get("look_at") or next((c for c in chars if last and c != last["speaker"]), None)
        if listener:
            shots.append(Shot(index=len(shots), kind="reaction", start=t, end=t + react, camera="push_in",
                              characters=chars, focus=listener, cue=None, **common))
            t += react
    for s in shots:
        s.start, s.end = round(s.start, 3), round(s.end, 3)
    return shots, starts


def to_manifest(shots: list[Shot], cues: list[DialogueCue]) -> dict:
    return {"shots": [asdict(s) for s in shots], "cues": [asdict(c) for c in cues],
            "duration": shots[-1].end if shots else 0.0}
