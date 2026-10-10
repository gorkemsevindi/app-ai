# ruff: noqa: S603, S607  (ffmpeg/ffprobe on fixed arguments)
"""V8 final render from the shared fixture manifests: multi-track video with trim/speed/split, keyframed text,
music bed → MP4/WebM/HEVC with the right duration and codecs; photo template → PNG/JPEG/WebP; keyframe
expressions match the shared interpolation."""

import json
import subprocess
from pathlib import Path

import pytest

from worker.editor.render import kf_expr, render

FIX = json.loads((Path(__file__).resolve().parents[3] / "packages/shared/fixtures/engine.json").read_text())


def probe(p: Path) -> dict:
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                      "stream=codec_name,width,height:format=duration", "-of", "json", str(p)],
                                     capture_output=True, text=True).stdout)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=14", "-f",
                    "lavfi", "-i", "sine=f=440:d=14", "-shortest", str(d / "v.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=13", str(d / "m.mp3")],
                   check=True)
    return {"a1": d / "v.mp4", "m1": d / "m.mp3"}


@pytest.mark.parametrize("fmt,codec", [("mp4", "h264"), ("webm", "vp9")])
def test_video_exports(media, tmp_path, fmt, codec):
    m = next(f for f in FIX if f["name"] == "text_audio_keyframes")["manifest"]
    out = render(m, media, fmt, "720p", tmp_path, watermark=True, metadata={"comment": "p1 r3"})
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_name"] == codec)
    assert (v["width"], v["height"]) == (1280, 720)
    assert abs(float(info["format"]["duration"]) - m["duration_ms"] / 1000) < 0.15
    assert len(info["streams"]) == 2  # video + mixed audio


@pytest.mark.parametrize("fmt,magic", [("png", b"\x89PNG"), ("jpeg", b"\xff\xd8"), ("webp", b"RIFF")])
def test_photo_exports(tmp_path, fmt, magic):
    m = next(f for f in FIX if f["name"] == "template_movie_poster")["manifest"]
    out = render(m, {}, fmt, "1080p", tmp_path)
    assert out.read_bytes()[:len(magic)] == magic
    if fmt == "png":
        info = probe(out)
        assert info["streams"][0]["width"] == 1080  # poster 1240x1754 -> short side 1080


def test_keyframe_expression_matches_shared_interpolation():
    ks = [{"t_ms": 0, "value": 0, "easing": "linear"}, {"t_ms": 500, "value": 1, "easing": "ease_out"}]
    e = kf_expr(ks, 1, "t")
    for t, want in ((0, 0), (0.25, 0.75), (1.0, 1)):
        got = eval(e.replace("if(", "_if(").replace("lt(", "_lt("),  # noqa: S307 - our own expression
                   {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "t": t})
        assert abs(got - want) < 1e-9
