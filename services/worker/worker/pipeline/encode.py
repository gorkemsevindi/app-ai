"""Final encode: normalize any model output to a 9:16 H.264 MP4 (yuv420p, faststart) for
TikTok/Reels/Shorts, burn the plan-dependent visible watermark, embed AI-generated provenance
metadata, and extract a poster thumbnail. Uses the system ffmpeg binary (LGPL build)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EncodedVideo:
    video: Path
    thumbnail: Path
    width: int
    height: int
    duration_ms: int
    size_bytes: int


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-500:]}")


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",  # noqa: S607
                        str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def encode_vertical(src: Path, out_dir: Path, width: int = 720, height: int = 1280, watermark: bool = True,
                    watermark_text: str = "AI generated", job_id: str = "", fps: int = 24) -> EncodedVideo:
    out = out_dir / "final.mp4"
    thumb = out_dir / "thumb.jpg"
    # scale-to-cover then center-crop to exact 9:16, constant frame rate for platform compatibility
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
          f"fps={fps},format=yuv420p")
    if watermark:
        safe = watermark_text.replace(":", r"\:").replace("'", "")
        vf += (f",drawtext=text='{safe}':fontcolor=white@0.75:fontsize={height // 40}:"
               f"x=w-tw-{width // 30}:y=h-th-{height // 24}:box=1:boxcolor=black@0.25:boxborderw=8")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-profile:v", "high",
          "-preset", "medium", "-crf", "20", "-movflags", "+faststart", "-an",
          "-metadata", "comment=AI-generated content",
          "-metadata", f"description=ai_generated=true;job={job_id}", str(out)])
    _run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1", "-q:v", "3",
          str(thumb)])
    info = probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return EncodedVideo(video=out, thumbnail=thumb, width=int(v["width"]), height=int(v["height"]),
                        duration_ms=int(float(info["format"]["duration"]) * 1000), size_bytes=out.stat().st_size)
