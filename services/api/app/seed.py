"""Seed data: 10 original demo templates + an admin user (dev only) + default feature flags.

Template source clips (`source_clip_key`) must be ORIGINAL footage we shot or licensed with
model releases — never film clips, celebrity footage or unlicensed music (spec §6). Until the clips
are uploaded, `face_swap_v2v` templates stay inactive in production; `identity_i2v` ones work from
the prompt alone. Usage: python -m app.seed"""

import os

from sqlalchemy import select

from .db import session_factory
from .models import FeatureFlag, LedgerReason, Template, TemplateVersion, User, UserRole
from .security import hash_password
from .services import credits

NEG = "blurry, deformed face, extra limbs, watermark, text, nsfw, nudity, child, gore"

TEMPLATES = [
    ("neon-runway", "Neon Runway", "fashion", "Strut down a rain-soaked neon runway.", 12, "face_swap_v2v",
     "a confident person walking a glossy runway at night, neon reflections on wet floor, slow dolly-in, 35mm"),
    ("golden-hour-spin", "Golden Hour Spin", "cinematic", "A slow-motion spin in warm sunset light.", 10,
     "identity_i2v", "a person slowly spinning in a wheat field at golden hour, hair moving, lens flare, 50mm"),
    ("rooftop-groove", "Rooftop Groove", "dance", "Simple, joyful dance moves on a city rooftop.", 12,
     "face_swap_v2v", "a person dancing energetically on a rooftop at dusk, city skyline, handheld camera"),
    ("astronaut-wave", "Hello From Orbit", "fantasy", "Wave at Earth from a space station window.", 15,
     "identity_i2v", "a person in a white spacesuit floating by a space station window waving, earth below"),
    ("coffee-surprise", "Coffee Surprise", "funny", "A latte that does something unexpected.", 8,
     "identity_i2v", "a person at a cafe table reacting with surprise as latte art turns into a tiny dancing cat"),
    ("street-style-turn", "Street Style Turn", "fashion", "Look back over the shoulder, magazine-cover style.", 10,
     "face_swap_v2v", "a stylish person turning to camera on a sunny european street, shallow depth of field"),
    ("rainy-window", "Rainy Window", "cinematic", "Moody close-up behind a rain-streaked window.", 10,
     "identity_i2v", "close-up of a person looking through a rainy window, soft blue light, film grain"),
    ("snow-globe", "Snow Globe", "fantasy", "You, inside a shaking snow globe.", 12, "identity_i2v",
     "a tiny person inside a glass snow globe, snow swirling, warm fairy lights, macro shot"),
    ("victory-jump", "Victory Jump", "trending", "Celebration jump with confetti.", 10, "face_swap_v2v",
     "a person jumping in celebration as colorful confetti falls, slow motion, bright studio"),
    ("beach-sprint", "Beach Sprint", "travel", "Run along the shoreline at sunrise.", 10, "identity_i2v",
     "a person running along the shoreline at sunrise, waves splashing, tracking shot, warm tones"),
]

DEFAULT_FLAGS = [
    ("paywall_enabled", True, {}, "Show paywall when credits are insufficient", True),
    ("free_watermark", True, {}, "Visible watermark for free plan", False),
    ("template_autoplay", True, {"wifi_only_default": True}, "Autoplay previews (respects data saver)", True),
]


def run() -> None:
    db = session_factory()()
    try:
        for i, (slug, title, cat, desc, cost, cap, recipe) in enumerate(TEMPLATES):
            if db.execute(select(Template).where(Template.slug == slug)).scalar_one_or_none():
                continue
            t = Template(slug=slug, title=title, category=cat, description=desc, credit_cost=cost, duration_s=5,
                         est_seconds=90 if cap == "face_swap_v2v" else 150, sort_order=i * 10,
                         accepts_text=cap == "identity_i2v", is_active=False,
                         thumbnail_url=f"/static/templates/{slug}.jpg", preview_url=f"/static/templates/{slug}.mp4")
            db.add(t)
            db.flush()
            preferred, fallback = (("dreamid_v", "wan22_animate_14b") if cap == "face_swap_v2v"
                                   else ("wan22_ti2v_5b", None))
            params = {"resolution": "720x1280", "fps": 16}
            if cap == "face_swap_v2v":
                params["source_clip_key"] = f"templates/{slug}/source.mp4"
            v = TemplateVersion(template_id=t.id, version=1, prompt_recipe=recipe, negative_prompt=NEG,
                                model_capability=cap, preferred_model=preferred, fallback_model=fallback,
                                params=params)
            db.add(v)
            db.flush()
            t.current_version_id = v.id
            t.is_active = True
        for key, enabled, value, desc, public in DEFAULT_FLAGS:
            if db.get(FeatureFlag, key) is None:
                db.add(FeatureFlag(key=key, enabled=enabled, value=value, description=desc, public=public))
        if os.environ.get("APP_ENV", "dev") == "dev":
            email = "admin@example.com"
            if db.execute(select(User).where(User.email == email)).scalar_one_or_none() is None:
                pw = os.environ.get("SEED_ADMIN_PASSWORD", "admin-dev-only")
                u = User(email=email, password_hash=hash_password(pw),
                         role=UserRole.admin, terms_version="seed")
                db.add(u)
                db.flush()
                credits.apply(db, u.id, 1000, LedgerReason.admin_adjust, f"seed:{u.id}")
        db.commit()
        print(f"seeded {len(TEMPLATES)} templates")
    finally:
        db.close()


if __name__ == "__main__":
    run()
