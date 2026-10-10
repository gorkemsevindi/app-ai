"""V7 animatic: storyboard title cards are real clips (Unicode text via ffmpeg drawtext), mixed with rendered
shots, with exact subtitles (slang preserved byte-for-byte)."""

import subprocess

from worker.studio.assemble import assemble, card_clip


def _dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(p)], capture_output=True, text=True).stdout)


def test_card_clip_and_animatic_assembly(tmp_path):
    c = card_clip({"title": "İÇ. MUTFAK · GECE", "text": "Ayşe bağırıyor: 'siktir git!' — üç: nokta",
                   "camera": "close-up"}, tmp_path / "c.mp4", 360, 640, 1.5)
    assert abs(_dur(c) - 1.5) < 0.2
    out, vtt = assemble([(None, {"duration_ms": 2000, "card": {"title": "S1", "text": "Kapı açılır."}}),
                         (None, {"duration_ms": 1000, "card": {}})],
                        {"resolution": "360x640", "burn_in": True,
                         "captions": [{"start_ms": 0, "end_ms": 2000, "text": "AYŞE: Siktir git lan, şerefsiz!"}]},
                        tmp_path, None)
    assert abs(_dur(out) - 3.0) < 0.3
    assert "AYŞE: Siktir git lan, şerefsiz!" in vtt.read_text(encoding="utf-8")
