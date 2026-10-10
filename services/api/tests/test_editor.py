# ruff: noqa: S603, S607  (tests run ffmpeg/ffprobe on fixed arguments)
"""V8 editor API: canonical projects, command batches (idempotent, optimistic concurrency + rebase), revisions and
restore, object-level asset ACL, subtitles, and real exports through the worker (ffmpeg) — video import → trim →
text/audio overlay → MP4 export, and photo → layers/text → PNG export."""

import json
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select

from app.models import FeatureFlag, GenerationJob, JobKind, JobStatus, LedgerReason
from app.services import credits

from .conftest import signup

FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "packages/shared/fixtures/engine.json").read_text())


def enable(db, **over):
    f = db.get(FeatureFlag, "editor") or FeatureFlag(key="editor")
    f.enabled, f.value = True, over
    db.add(f)
    db.commit()


def cmds(client, h, pid, base, commands, key=None, client_name="web"):
    return client.post(f"/editor/projects/{pid}/commands", headers={**h, "Idempotency-Key": key or uuid.uuid4().hex,
                                                                     "X-Client": client_name},
                       json={"base_revision": base, "commands": commands})


def upload(client, h, storage, kind, mime, data: bytes, name, meta=None):
    r = client.post("/editor/assets", headers=h, json={"kind": kind, "mime": mime, "size_bytes": len(data),
                                                        "name": name}).json()
    assert r["upload"]["method"] == "PUT" and r["uri"].startswith("asset:")
    storage.put(r["upload"]["url"].split("memory://put/", 1)[1], data, mime)
    done = client.post(f"/editor/assets/{r['asset_id']}/complete", headers=h, json=meta or {})
    assert done.status_code == 200, done.text
    return done.json()


def test_templates_match_shared_engine_and_flag(client, db):
    h, _ = signup(client)
    assert client.post("/editor/projects", headers=h, json={"title": "x"}).status_code == 403
    enable(db)
    cfg = client.get("/editor/config").json()
    assert cfg["enabled"] and {"mp4", "png", "srt"} <= set(cfg["formats"])
    assert cfg["ai_tools"]["lip_sync"] == "not_available"  # never faked
    p = client.post("/editor/projects", headers=h, json={"title": "Poster", "template": "movie_poster"}).json()
    exp = next(f for f in FIXTURES if f["name"] == "template_movie_poster")["expected"]
    doc = {**p["project"], "project_id": "p1", "title": exp["title"]}
    assert doc == exp and p["revision"] == 0  # same JSON as the TS engine (web/mobile) produces
    other, _ = signup(client)
    assert client.get(f"/editor/projects/{p['id']}", headers=other).status_code == 404


def test_commands_idempotency_rebase_conflict_restore(client, db, storage):
    enable(db)
    h, _ = signup(client)
    v = upload(client, h, storage, "video", "video/mp4", b"x" * 100, "clip.mp4", {"duration_ms": 20000})
    p = client.post("/editor/projects", headers=h, json={"title": "Cut", "preset": "landscape_1080"}).json()
    pid = p["id"]
    setup = [{"type": "add_asset", "asset": {"id": "a1", "kind": "video", "uri": v["uri"], "name": "clip",
                                             "duration_ms": 20000}},
             {"type": "add_track", "track_id": "v1", "kind": "video"},
             {"type": "add_track", "track_id": "tx", "kind": "text"},
             {"type": "add_clip", "clip": {"id": "c1", "track_id": "v1", "asset_id": "a1", "start_ms": 0,
                                           "duration_ms": 8000}}]
    key = uuid.uuid4().hex
    r1 = cmds(client, h, pid, 0, setup, key).json()
    assert r1["revision"] == 1 and r1["applied"] == 4
    again = cmds(client, h, pid, 0, setup, key).json()
    assert again["replay"] is True and again["revision"] == 1  # retried autosave: applied once
    # web edits the clip, mobile (stale base) adds a title on another item -> rebased, no conflict
    assert cmds(client, h, pid, 1, [{"type": "trim_clip", "clip_id": "c1", "side": "end", "delta_ms": -2000}]
                ).json()["revision"] == 2
    r = cmds(client, h, pid, 1, [{"type": "add_clip", "clip": {"id": "t1", "track_id": "tx", "start_ms": 0,
                                                                "duration_ms": 2000, "text": {"content": "Selam"}}}],
             client_name="android").json()
    assert r["revision"] == 3 and r["rebased"] == 1
    # an iOS edit based on revision 1 touching the same clip -> conflict with the current document
    c = cmds(client, h, pid, 1, [{"type": "set_clip", "clip_id": "c1", "transform": {"x": 10}}], client_name="ios")
    assert c.status_code == 409 and c.json()["detail"]["code"] == "revision_conflict"
    assert c.json()["detail"]["project"]["clips"]["c1"]["duration_ms"] == 6000
    bad = cmds(client, h, pid, 3, [{"type": "set_title", "title": "ok"}, {"type": "remove_clip", "clip_id": "zz"}])
    assert bad.status_code == 422 and bad.json()["detail"]["engine_code"] == "not_found" and \
        bad.json()["detail"]["index"] == 1
    assert client.get(f"/editor/projects/{pid}", headers=h).json()["title"] == "Cut"  # all-or-nothing batch
    revs = client.get(f"/editor/projects/{pid}/revisions", headers=h).json()["items"]
    assert [x["revision"] for x in revs] == [3, 2, 1, 0] and revs[0]["client"] == "android"
    res = client.post(f"/editor/projects/{pid}/restore", headers=h, json={"revision": 1}).json()
    assert res["revision"] == 4 and res["project"]["clips"]["c1"]["duration_ms"] == 8000 and "t1" not in \
        res["project"]["clips"]
    srt = client.get(f"/editor/projects/{pid}/subtitles.srt", headers=h).text
    assert srt == ""  # restored to before the title
    # object-level ACL: another user's asset can't be referenced
    o, _ = signup(client)
    foreign = upload(client, o, storage, "image", "image/png", b"p" * 10, "x.png")
    r = cmds(client, h, pid, 4, [{"type": "add_asset", "asset": {"id": "f", "kind": "image", "uri": foreign["uri"],
                                                                   "name": "f"}}])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "asset_not_allowed"
    r = cmds(client, h, pid, 4, [{"type": "add_asset", "asset": {"id": "f", "kind": "image", "uri": v["uri"],
                                                                   "name": "f"}}])
    assert r.json()["detail"]["code"] == "asset_kind_mismatch"
    assert client.post("/editor/assets", headers=h, json={"kind": "video", "mime": "application/x-sh",
                                                          "size_bytes": 10, "name": "x"}).json()["detail"][
        "code"] == "bad_mime"


def _media(tmp: Path) -> tuple[bytes, bytes]:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24:d=6", "-f",
                    "lavfi", "-i", "sine=f=440:d=6", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(tmp / "v.mp4")],
                   check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=6", str(tmp / "m.mp3")],
                   check=True)
    return (tmp / "v.mp4").read_bytes(), (tmp / "m.mp3").read_bytes()


def test_video_import_trim_text_audio_export_and_photo_png(client, db, storage, drain, tmp_path):
    enable(db, credits_per_second={"720p": 1})
    h, _ = signup(client)
    uid = uuid.UUID(client.get("/me", headers=h).json()["id"])
    credits.apply(db, uid, 100, LedgerReason.purchase, f"p:{uuid.uuid4()}", bucket="purchased")
    db.commit()
    vb, mb = _media(tmp_path)
    v = upload(client, h, storage, "video", "video/mp4", vb, "v.mp4", {"duration_ms": 6000})
    m = upload(client, h, storage, "audio", "audio/mpeg", mb, "m.mp3", {"duration_ms": 6000})
    p = client.post("/editor/projects", headers=h, json={"title": "Klip", "preset": "vertical_1080"}).json()
    r = cmds(client, h, p["id"], 0, [
        {"type": "add_asset", "asset": {"id": "v", "kind": "video", "uri": v["uri"], "name": "v", "duration_ms": 6000}},
        {"type": "add_asset", "asset": {"id": "m", "kind": "audio", "uri": m["uri"], "name": "m", "duration_ms": 6000}},
        {"type": "add_track", "track_id": "v1", "kind": "video"},
        {"type": "add_track", "track_id": "au", "kind": "audio"},
        {"type": "add_track", "track_id": "tx", "kind": "text"},
        {"type": "add_clip", "clip": {"id": "c1", "track_id": "v1", "asset_id": "v", "start_ms": 0,
                                      "duration_ms": 6000}},
        {"type": "trim_clip", "clip_id": "c1", "side": "start", "delta_ms": 1000},
        {"type": "move_clip", "clip_id": "c1", "start_ms": 0},
        {"type": "add_clip", "clip": {"id": "t", "track_id": "tx", "start_ms": 500, "duration_ms": 3000,
                                      "text": {"content": "Şimdi başlıyoruz!", "size": 80}}},
        {"type": "set_keyframe", "clip_id": "t", "prop": "opacity", "t_ms": 0, "value": 0},
        {"type": "set_keyframe", "clip_id": "t", "prop": "opacity", "t_ms": 400, "value": 1},
        {"type": "add_clip", "clip": {"id": "mu", "track_id": "au", "asset_id": "m", "start_ms": 0,
                                      "duration_ms": 5000}},
        {"type": "set_clip", "clip_id": "mu", "audio": {"volume": 0.5, "fade_out_ms": 800}}])
    assert r.status_code == 200, r.text
    q = client.post(f"/editor/projects/{p['id']}/render/quote", headers=h, json={"format": "mp4"}).json()
    assert q["credits"] == 5 and q["duration_ms"] == 5000
    key = uuid.uuid4().hex
    rr = client.post(f"/editor/projects/{p['id']}/render", headers={**h, "Idempotency-Key": key},
                     json={"format": "mp4", "confirmed_credits": 5})
    assert rr.status_code == 202, rr.text
    assert client.post(f"/editor/projects/{p['id']}/render", headers={**h, "Idempotency-Key": key},
                       json={"format": "mp4", "confirmed_credits": 5}).json()["job_id"] == rr.json()["job_id"]
    assert drain() == 1
    ex = client.get(f"/editor/projects/{p['id']}/exports", headers=h).json()["items"][0]
    assert ex["status"] == "completed" and ex["url"]
    data = storage.objects[ex["url"].split("memory://get/", 1)[1].split("?", 1)[0]][0]
    out = tmp_path / "out.mp4"
    out.write_bytes(data)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
                            "-of", "json", str(out)], capture_output=True, text=True).stdout
    info = json.loads(probe)
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    assert next(s for s in info["streams"] if s["codec_name"] == "h264")["width"] == 720  # 720p of 1080x1920
    assert abs(float(info["format"]["duration"]) - 5.0) < 0.2
    job = db.execute(select(GenerationJob).where(GenerationJob.kind == JobKind.editor_render)).scalar_one()
    assert job.status == JobStatus.completed and job.billing_state == "settled"
    vtt = client.get(f"/editor/projects/{p['id']}/subtitles.vtt", headers=h).text
    assert "00:00:00.500 --> 00:00:03.500\nŞimdi başlıyoruz!" in vtt
    # photo/design: template -> edit text -> PNG
    d = client.post("/editor/projects", headers=h, json={"title": "Afiş", "template": "movie_poster"}).json()
    assert cmds(client, h, d["id"], 0, [{"type": "set_clip", "clip_id": "t1", "text": {"content": "BENİM FİLMİM"}}]
                ).status_code == 200
    q = client.post(f"/editor/projects/{d['id']}/render/quote", headers=h, json={"format": "png"}).json()
    client.post(f"/editor/projects/{d['id']}/render", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                json={"format": "png", "confirmed_credits": q["credits"]})
    assert drain() == 1
    ex = client.get(f"/editor/projects/{d['id']}/exports", headers=h).json()["items"][0]
    png = storage.objects[ex["url"].split("memory://get/", 1)[1].split("?", 1)[0]][0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.post(f"/editor/projects/{d['id']}/render/quote", headers=h,
                       json={"format": "mp4", "quality": "2160p"}).json()["detail"]["code"] in ("empty_timeline",
                                                                                                 "plan_required")
