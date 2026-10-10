import pytest

from dramaapp.errors import AppError
from dramaapp.modules import ledger
from dramaapp.modules.moderation_rules import check_text
from dramaapp.pipeline.render import caption_chunks
from dramaapp.pipeline.timeline import plan
from dramaapp.providers.llm_local import generate
from dramaapp.providers.pricing import PREMIUM_ROUTE, route_estimate
from dramaapp.providers.tts_espeak import align_words, phonemes_to_visemes, voiced_segments
from dramaapp.story import WizardInput


def test_local_writer_is_deterministic_and_bilingual():
    for lang in ("tr", "en"):
        a = generate(WizardInput(language=lang, episode_count=4, seed=3))
        b = generate(WizardInput(language=lang, episode_count=4, seed=3))
        assert a == b
        keys = {c.key for c in a.characters}
        for ep in a.episodes:
            words = sum(len(ln.text.split()) for sc in ep.scenes for ln in sc.lines)
            assert 80 <= words <= 130
            assert all(ln.speaker in keys for sc in ep.scenes for ln in sc.lines)


def test_viseme_mapping():
    assert phonemes_to_visemes("m'ERhaba")[:2] == ["MBP", "EE"]
    assert "OO" in phonemes_to_visemes("bOl")


def test_alignment_uses_phrase_segments():
    words = ["Hayır.", "Üzerinde", "benim", "adım."]
    segs = [(0.1, 0.5), (0.7, 2.0)]
    t = align_words(words, [3, 6, 4, 3], segs, 2.2)
    assert t[0].start == pytest.approx(0.1) and t[0].end == pytest.approx(0.5)
    assert t[1].start == pytest.approx(0.7) and t[-1].end == pytest.approx(2.0)


def test_voiced_segments_detects_gap():
    import numpy as np
    env = np.array([0] * 10 + [1] * 30 + [0] * 20 + [1] * 30 + [0] * 5, dtype=float)
    segs = voiced_segments(env)
    assert len(segs) == 2 and segs[0][0] == pytest.approx(0.10)


def test_timeline_plan_hits_target_and_orders_shots():
    scenes = [{"location": "l", "characters": ["a", "b"], "lines": [
        {"speaker": "a", "text": "x", "emotion": "angry", "intensity": 0.9, "look_at": "b"},
        {"speaker": "b", "text": "y", "emotion": "neutral", "intensity": 0.5, "look_at": "a"}]}]
    shots, starts = plan(scenes, [3.0, 2.0], target_s=12)
    assert shots[0].kind == "establishing" and shots[1].kind == "close" and shots[-1].kind == "reaction"
    assert all(a.end == pytest.approx(b.start) for a, b in zip(shots, shots[1:], strict=False))
    assert starts[0]["start"] > shots[1].start


def test_caption_chunks_respect_order_and_length():
    cues = [{"speaker": "a", "words": [{"word": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(30)]}]
    ch = caption_chunks(cues, max_chars=10)
    assert len(ch) > 1
    assert all(a["end"] < b["start"] for a, b in zip(ch, ch[1:], strict=False))


def test_moderation_blocks_ncii_and_minor():
    assert not check_text("make a deepfake nude of her")["ok"]
    assert check_text("Elif eski mektubu buldu.")["ok"]


def test_premium_estimate_shape():
    est = route_estimate(PREMIUM_ROUTE, seconds=60, chars=700, images=21)
    assert est["total_usd"] > 10 and set(est["line_items_usd"]) == {"video", "lipsync", "tts", "image", "music"}


def test_ledger_balanced_and_idempotent(db):
    t1, c1 = ledger.post(db, idempotency_key="t:1", kind="x", entries=[("a", "USD", 100), ("b", "USD", -100)])
    t2, c2 = ledger.post(db, idempotency_key="t:1", kind="x", entries=[("a", "USD", 100), ("b", "USD", -100)])
    assert c1 and not c2 and t1.id == t2.id
    db.commit()
    with pytest.raises(AppError):
        ledger.post(db, idempotency_key="t:2", kind="x", entries=[("a", "USD", 100), ("b", "USD", -90)])
    db.rollback()
    assert ledger.balance(db, "a", "USD") == 100
