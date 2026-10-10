"""First-line text policy checks (prompts, scripts, comments). Deliberately conservative keyword rules;
a hosted moderation model plugs in behind `check_text` later. Hits do not silently pass: they block or
open a ModerationCase for human review."""

import re

BLOCK = {
    "minor_sexualization": [r"\b(child|minor|underage|kid)\b.*\b(sex|nude|naked)\b", r"\b(çocuk|reşit olmayan)\b.*\b(seks|çıplak)\b"],
    "nonconsensual_intimate": [r"\b(deepfake|face ?swap)\b.*\b(nude|porn|naked)\b", r"\b(çıplak|porno)\b.*\b(yüz değiştir)"],
}
REVIEW = {
    "real_person_reference": [r"\b(president|prime minister|cumhurbaşkanı|başbakan)\b"],
    "financial_fraud": [r"\b(send me (your )?(password|iban|card)|şifreni gönder|iban(ını)? gönder)\b"],
    "violence_extreme": [r"\b(behead|kafasını kes)\b"],
}


def check_text(text: str) -> dict:
    t = text.lower()
    blocked = [k for k, pats in BLOCK.items() if any(re.search(p, t) for p in pats)]
    review = [k for k, pats in REVIEW.items() if any(re.search(p, t) for p in pats)]
    return {"blocked": blocked, "review": review, "ok": not blocked}
