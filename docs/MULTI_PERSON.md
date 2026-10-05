# Multi-Person Viral Video Replacement — Design (feature module)

Status: design approved for implementation, behind the `multi_person` remote-config flag (off by default).
Research and licence evidence: [research-multiperson.md](research-multiperson.md).

## 1. User flow

```
Upload own video (rights + consent attestation)
  → analysis job (GPU): detect → track → re-ID stitch → person thumbnails + quality flags
  → app shows Person 1..N (max N from remote config, default 4)
  → user assigns one of *their own consented* Identity Profiles to 1..N persons (others stay untouched)
  → quote (credits) → optional low-cost preview (first 2 s, 480p) → generate (full)
  → result: preview / download / share / report (same as template results)
```

## 2. Pipeline (not frame-by-frame face swap)

| # | Stage | Primary (licence) | Fallback / notes |
|---|---|---|---|
| 0 | Normalize | ffmpeg: constant fps (16/24), max res, 9:16 crop plan, scene-cut detection | — |
| 1 | Person detection | RT-DETRv2 / D-FINE / YOLOX (Apache-2.0) | **Not** Ultralytics / YOLOv10 (AGPL) |
| 2 | Tracking + masks | SAM 2.1 video predictor (Apache-2.0) seeded per detection; OC-SORT / BoT-SORT association (MIT) | SAM 3/3.1 after legal review of SAM License |
| 3 | Re-ID & track stitching | YuNet (MIT) face detect + SFace (Apache-2.0) face embedding + body-box appearance; **our** stitcher merges tracklets across exits/re-entries/occlusions → persistent `track_id` | Licensed/self-trained embedder (SFace is weaker than ArcFace; InsightFace weights are non-commercial and banned) |
| 4 | Assignment | UI: Person k → IdentityProfile; server validates ownership + consent | — |
| 5 | Replacement (per assigned person, back-to-front) | **DreamID-V** (Apache-2.0 code; Wan2.1-1.3B; MediaPipe variant) on the person's crop window with the track mask | **Wan2.2-Animate-14B** replacement mode with per-person masks (official preprocessing is single-person; we supply our own masks and swap its AGPL detector). Evaluate MoCha / SCAIL-2 (licence pending) as native multi-ID adapters |
| 6 | Temporal consistency | Diffusion replacer works on whole clips (temporal attention), overlapping windows with latent cross-fade, single fixed seed per track, per-track identity embedding fixed for the clip | Optional masked deflicker; **no** per-frame restorers (CodeFormer/KEEP are non-commercial and flicker) |
| 7 | Occlusion handling | Mask = track mask − masks of people in front (depth order from mask overlap / bottom edge); during full occlusion the person is not rendered; on re-entry same track_id → same identity + seed | VACE (Apache-2.0) masked inpaint only for QA-flagged seams |
| 8 | Compositing | Feathered alpha (distance-transform) of each replaced crop back into the original frame, only inside its mask; colour/lighting matched to source (mean/std in LAB within mask ring) | BiRefNet (MIT) edge refinement |
| 9 | Output QA | Per-track identity similarity to assigned profile, leakage vs. original person and vs. other assignees, flicker (RAFT warp error, BSD-3), landmark jitter (MediaPipe), **pixel change outside masks ≈ 0** | Fail → retry (new seed / fallback model) → fail + auto refund |
| 10 | Encode + provenance | Existing 9:16 H.264 encoder, visible watermark (forced for real-footage replacement by default), AI-generated metadata, C2PA when available | — |

**Single-person fast path:** one assigned person, clean track (no long occlusions/ID merges), ≤10 s →
one DreamID-V pass on the crop at 480p, skip VACE/inpaint stage. Same code path with stages short-circuited.

**N persons:** sequential masked passes (cost ≈ linear in N). Architecture keeps `max_persons` as config;
nothing in data model, API or worker assumes 4. A native multi-identity adapter can replace the loop later
behind the same `ModelAdapter.capabilities().max_identities`.

## 3. GPU estimate (to be benchmarked — see research §5)

| Case (480p) | Model | H100 GPU-time | VRAM |
|---|---|---|---|
| Analysis (detect+SAM2.1+re-ID), 15 s clip | — | ~20–40 s | 8–16 GB |
| 1 person × 5 s | DreamID-V | ~40–90 s | 16–24 GB |
| 4 persons × 15 s | DreamID-V ×4 | ~10–15 min | 16–24 GB |
| 1 person × 5 s | Wan2.2-Animate-14B (fallback) | ~4–8 min | 40–80 GB (FP8/offload ~24–48) |

Pricing therefore scales with persons × seconds (remote-configurable, §5).

## 4. Module changes

| Area | Change |
|---|---|
| DB (migration 0002) | `source_videos`, `video_persons`, `job_assignments`; `generation_jobs` gains `kind`, `source_video_id`, `preferred_model`, `fallback_model`, `spec`; template FKs nullable for non-template jobs |
| API | `/source-videos` (create+attest, sign upload, complete, get, delete), `/source-videos/{id}/quote`, `POST /generations/multi`; worker `complete` handles `analysis` results; claim routes by job-level model snapshot |
| Queue/router | Unchanged semantics (lease, heartbeat, retry, refund). Model snapshot on job → template and non-template jobs share one router + kill switch |
| Safety | Rights/consent attestation; only own consented profiles assignable; analysis flags (`minor_suspected`, `nsfw_source`, `too_small`) block or disable persons; output QA + moderation; forced watermark; reports → moderation queue |
| Remote config | `multi_person` flag value: `max_persons`, `max_duration_s`, `resolutions`, `pricing`, models, `force_watermark`, `preview` |
| Worker | `worker/multiperson/`: tracking + stitching, orchestrator (back-to-front sequential passes), compositing, QA; analysis + replacement adapters |
| Mobile | New "Your video" flow: upload → person picker (Person 1..N chips over thumbnails) → assign profile → quote → preview → generate |
| Admin | Uses existing flags endpoint; dashboard counts by `kind` |

## 5. Remote config (`feature_flags.key = "multi_person"`)

```json
{
  "max_persons": 4,
  "max_duration_s": 15,
  "min_duration_s": 2,
  "resolutions": {"480x832": 1.0, "720x1280": 1.6},
  "pricing": {"base": 10, "per_person_second": 2, "preview": 5},
  "analysis_model": "mp_analyzer",
  "preferred_model": "dreamid_v_mp",
  "fallback_model": "wan22_animate_mp",
  "force_watermark": true,
  "min_face_px": 64
}
```
Cost = `ceil((base + per_person_second × persons × ceil(duration)) × resolution_multiplier)`.

## 6. Safety rules (enforced server-side)

1. Upload requires two attestations: *I own or have the rights to this video* and *everyone whose face
   will be replaced has consented* (versioned, stored, audited).
2. Only Identity Profiles owned by the requester (which already carry the likeness consent) can be assigned.
   No uploading of arbitrary face images for assignment.
3. Analysis rejects videos with suspected minors or sexual content; persons with faces below `min_face_px`
   are not selectable. Moderation actions are recorded.
4. Real public figures / celebrities: not allowed (Terms + report flow + admin removal). Template catalog
   never includes real people.
5. Output passes the existing output moderation + QA; blocked → refund.
6. Visible watermark forced for real-footage replacement (configurable), provenance metadata always.

## 7. Known limitations / next steps
- GPU stage implementations need a GPU node to validate (CLI wiring of DreamID-V/Wan is configurable).
- SFace identity metric is weaker than ArcFace; budget for a licensed or self-trained embedder.
- Weights licences on Hugging Face (DreamID-V, SCAIL-2, MoCha) must be verified before launch.
