"""Local seed data (demo credentials exist ONLY here, never in staging/production).

    python -m dramaapp.seed            # renders real episodes with the local pipeline (a few minutes)
    python -m dramaapp.seed --fixture  # fast: placeholder media clearly labelled SEED FIXTURE (tests)
"""

import argparse
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import create_all, session_factory
from .models import Episode, User, now
from .modules.admin import publish_episode
from .modules.auth import make_creator
from .pipeline import orchestrator as orch
from .security import hash_password
from .storage import put_file

DEMO = {
    "admin": ("admin@demo.example.com", "admin-demo-pass", "Admin"),
    "creator": ("creator@demo.example.com", "creator-demo-pass", "Demo Studio"),
    "viewer": ("viewer@demo.example.com", "viewer-demo-pass", "Demo Viewer"),
}


def _user(db: Session, role: str) -> User:
    email, pw, name = DEMO[role]
    u = db.scalar(select(User).where(User.email == email))
    if not u:
        u = User(email=email, password_hash=hash_password(pw), display_name=name, role=role, birth_year=1990)
        db.add(u)
        db.flush()
    if role == "creator":
        make_creator(db, u)
    return u


def _fixture_media(db: Session, ep: Episode, owner: str) -> None:
    """Labelled placeholder media (colour bars + tone + 'SEED FIXTURE'), used only by fast tests/seed."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        mp4 = d / "final.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:d=12:r=24",
                        "-f", "lavfi", "-i", "sine=f=330:d=12", "-vf",
                        "drawtext=text='SEED FIXTURE - not generated':fontcolor=white:fontsize=18:x=20:y=40",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(mp4)], check=True)
        from .pipeline.render import package_hls
        package_hls(mp4, d / "hls", 640)
        (d / "c.vtt").write_text("WEBVTT\n\n00:00:00.500 --> 00:00:03.000\nSEED FIXTURE\n")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-frames:v", "1", str(d / "t.jpg")], check=True)
        meta = {"provenance": {"seed_fixture": True}}
        base = f"episodes/{ep.id}/seed"
        ep.video_asset_id = put_file(db, mp4, f"{base}/final.mp4", "video", owner, meta).id
        ep.hls_asset_id = put_file(db, d / "hls", f"{base}/hls", "hls", owner, meta).id
        ep.captions_asset_id = put_file(db, d / "c.vtt", f"{base}/c.vtt", "captions", owner).id
        ep.thumbnail_asset_id = put_file(db, d / "t.jpg", f"{base}/t.jpg", "image", owner).id
    ep.duration_s, ep.status = 12.0, "rendered"
    ep.provenance = {"seed_fixture": True, "label": "AI-generated", "models": [], "mock_components": ["seed_fixture"]}
    ep.qc_report = {"passed": True, "checks": [], "seed_fixture": True}


def seed(fixture: bool = False, episodes: int = 6, language: str = "tr") -> dict:
    from .modules.studio import create_project
    from .story import WizardInput

    create_all()
    db = session_factory()()
    admin, creator = _user(db, "admin"), _user(db, "creator")
    _user(db, "viewer")
    db.commit()
    project = create_project(WizardInput(genre="mystery", language=language, episode_count=episodes,
                                         character_count=3, seed=7), creator, db)
    for e in project["episodes"]:
        ep = db.get(Episode, e["id"])
        if fixture:
            _fixture_media(db, ep, creator.id)
        else:
            job = orch.create_job(db, owner_id=creator.id, ep=ep, quality="preview", max_spend=None)
            db.commit()
            orch.run_job(db, job)
            db.refresh(ep)
            if ep.status != "rendered":
                raise RuntimeError(f"render failed for episode {ep.number}: {job.error_code} {job.error_detail}")
        ep.status = "in_review"
        publish_episode(db, ep, admin.id)
        db.commit()
    db.close()
    return {"series_id": project["id"], "credentials": {k: v[:2] for k, v in DEMO.items()}, "at": now().isoformat()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--episodes", type=int, default=6)
    ap.add_argument("--language", default="tr")
    print(seed(fixture=ap.parse_args().fixture, episodes=ap.parse_args().episodes, language=ap.parse_args().language))
