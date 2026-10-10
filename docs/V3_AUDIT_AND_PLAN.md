# Master Spec V3: audit, gap analysis and phased plan

Date: 2026-10-10. Scope: the existing **AI Video App**, i.e. `apps/mobile`, `services/api`, `services/worker`,
`services/trend`, `infra`, `docs`.

The `drama/` directory is an unrelated product (YourStars). It is not touched by this work and shares no code
with it. Recommendation: move `drama/` to its own repository.

This document is the "required output before coding" from spec §22 and §30.

## 1. Repository architecture: what exists and what is reused

| Area | Today (verified by reading the code and running the tests) | Reused for V3 as |
|---|---|---|
| API | FastAPI modular app. Routers: auth (email + Apple/Google token verify, rotating refresh), identity profiles, templates, generations, multi-person, me, account deletion, admin, internal worker | Base for every new endpoint. Existing paths stay as they are (no `/v1` duplication; spec §12 allows adapting to conventions) |
| Jobs | PostgreSQL queue: `FOR UPDATE SKIP LOCKED`, leases, heartbeats, retries, preferred → fallback model routing, `model_disabled:*` kill switches, daily GPU budget guard, weighted queue classes | **Generation Service + router core.** Template remix reuses the `multi_replace` job kind; no new queue |
| Credits | Append-only `credit_ledger` (DB trigger blocks UPDATE/DELETE), per-user row lock, idempotency keys; debit at job creation, refund on terminal failure | Ledger stays. Buckets and reserve/settle semantics are added in Phase 5 (additive) |
| Templates | `templates` + immutable `template_versions` (prompt recipe, model routing) | Extended **additively** with slots, visibility, moderation status, creator, rights and a source video |
| Multi-person | `source_videos`, `video_persons` (stable tracks), `job_assignments`; analysis job → tracks + thumbnails + flags (minor/NSFW/violence block); replace job with per-person masked passes, occlusion-aware composite, QA gate; pricing from `multi_person` remote config | **Template ingestion** (detect/track persons in a template's source clip) and the remix pipeline |
| Worker | Provider-agnostic adapter protocol; mock adapters; command adapters for DreamID-V / Wan2.2 (subprocess, own venv); 9:16 encoder; QA | Lip-sync / audio / expression stages are added as new adapter protocols in the same registry |
| Moderation | Text rules, output scorer interface (fails closed when required), reports, moderation actions, admin decisions | Template moderation status, report categories for templates/creators |
| Flags / config | `feature_flags` (+ public remote config), `experiments`, `analytics_events`, `audit_logs`, `webhook_events` inbox, `purchases` / `subscriptions` tables (not yet wired) | Every V3 feature sits behind its own flag |
| Mobile | Expo (expo-router), tabs (templates, video, library, profile), identity onboarding, template detail, job progress, multi-person flow, paywall screen, i18n en/tr | Template detail gains multi-slot assignment |

**Baseline test run (before any change):**

| Check | Result |
|---|---|
| API | 35/35 passed |
| Worker | 6/6 passed |
| Ruff | clean |
| Mobile | `tsc` clean, `node --test` passed |

**Pre-existing defect:** in mobile, `npm ci` fails with ERESOLVE. The cause is react-dom 19.3.0 requiring react ^19.3.0 while react is pinned at 19.2.3, so CI's `npm ci` step breaks. It is fixed in Phase 1 without changing app code.

## 2. Gap analysis against spec V3

| Spec item | Status | Gap |
|---|---|---|
| §26 Template-first feed, variable person slots (1..N), ingestion console | Partial | Templates are single-identity. No slots, no source-clip ingestion, no visibility/moderation states |
| §2.1 Template card fields (use count, est. credits, persons, time, safety badge), server ranking | Partial | `sort_order` only. No metrics, no ranking score |
| §2.3 / §8 Multi-person replace | **Mostly done** | Missing: speaker/audio handling, provenance of the template version, restoration stage interface |
| §27 Audio-driven lip-sync, singing, expression transfer, diarization, active speaker | **Missing** | No audio pipeline at all |
| §3 Template lifecycle + versioning + traceability | Partial | Versions exist, but jobs are not linked to slot definitions; no publish/moderation flow |
| §4 Credit buckets, reserve/settle/release, expiration | Partial | Single bucket. Debit at creation acts as the reservation; refund exists |
| §4 Store verify / subscriptions | Missing (tables only) | — |
| §5 Creator marketplace, RevenuePolicy, earnings ledger, settlements, anti-fraud | Missing | — |
| §6 Referral / attribution (signed links, windows, precedence) | Missing | — |
| §7 Model router estimate / submit / status / cancel / fetch, cost-aware routing | Partial | Preferred / fallback + kill switch only; no cost ceiling, no margin floor |
| §15 Per-generation telemetry, business dashboard | Partial | `model_runs` holds GPU seconds and cost; no dashboard |
| §17 Admin console (UI) | Missing | Admin API exists; no UI |
| §18 Mobile: resumable uploads, push, deep links | Missing | — |
| §20 Security tests (IDOR, forged referral, webhook replay) | Partial | IDOR tests exist for multi-person |

## 3. Proposed migrations (all additive and reversible; CI runs upgrade → downgrade → upgrade)

| Migration | Changes | Backward compatibility |
|---|---|---|
| 0003_templates_v3 | `templates`: + `creator_id`, `visibility` (default `public`), `moderation_status` (default `approved`), `commercial_rights` JSONB, `use_count` cache. `template_versions`: + `source_video_id`, `config` JSONB, `credit_rule` JSONB, `published_at`. New `template_person_slots`. New `template_metrics` (daily aggregates). `generation_jobs.spec` carries `template_slots` (no column change) | Existing templates keep working: defaults make them public + approved, and single-identity jobs are unchanged |
| 0004_audio_lipsync | `source_videos.audio` JSONB (segments, speakers, mapping suggestions); new `speaker_mappings` (manual corrections, versioned); `generation_outputs.qc` JSONB | Optional fields only |
| 0005_creator_marketplace | `creator_profiles` (KYC/payout data split into a restricted table), `revenue_policies` (versioned), `creator_earnings_ledger` (append-only trigger), `settlements` (frozen snapshot), `risk_holds` | New tables only |
| 0006_referrals | `referral_links`, `referral_clicks`, `attributions` | New tables only |
| 0007_credit_buckets | `credit_ledger.bucket` (default `general`), `expires_at`; reserve/settle/release reasons | Existing balance = `general` bucket |

## 4. API contract changes (additive) and mobile impact

| API | Purpose | Mobile impact |
|---|---|---|
| `GET /feed?category=` | Server-ranked templates, card fields | Home tab switches from `/templates` to `/feed` (old endpoint kept) |
| `GET /templates/{id}` | + `person_slots[]`, `est_credits`, `use_count` | Detail screen renders N slot pickers |
| `POST /generations/estimate` | Credit quote for template + slots | Price shown before confirm |
| `POST /generations/remix` | Idempotent multi-slot template job | New create path |
| Admin: `POST /admin/templates/{id}/source-video`, `PUT .../versions/{v}/slots`, `POST .../publish` | Ingestion console | — |
| Phase 2: `GET /source-videos/{id}/speakers`, `PUT /generations/{id}/speaker-mapping` | Lip-sync speaker assignment | Speaker-assignment step |
| Phase 3: `/creator/*` | Marketplace | Creator tab |
| Phase 4: `POST /referrals/link`, `GET /r/{token}` | Signed deep links | Share sheet |

## 5. Provider / model matrix. Licences were checked on 2026-10-10 from the official repositories

Code licence ≠ weight licence: every row lists the blocker that must be cleared before commercial use.

| Capability | Candidate | Code licence | Weights / dependency caveat (verified) | Status in plan |
|---|---|---|---|---|
| FACE_REPLACEMENT / MULTI_FACE | DreamID-V, Wan2.2-Animate (existing ADR-0005) | see docs/research.md | InsightFace weights banned (existing decision) | Existing adapters, GPU validation pending |
| SPEECH_LIP_SYNC | **LatentSync** (ByteDance) | Apache-2.0 | README: face alignment uses **InsightFace** landmarks → non-commercial weights; must swap the landmark model | Adapter behind a disabled flag |
| SPEECH / SINGING_LIP_SYNC | **MuseTalk** | MIT (code) | README: dependent models (whisper, ft-mse-vae, dwpose, S3FD) carry their own licences; test data non-commercial | Adapter behind a disabled flag |
| SPEECH_LIP_SYNC (API) | sync.so lipsync-2(-pro) | commercial API | ≈ $0.067–0.083/s (vendor docs, 2026-10-08); data retention to review | Adapter after contract |
| FACIAL_ANIMATION / EXPRESSION_TRANSFER | **LivePortrait** | MIT (code) | Weight licence and detector dependency **not yet verified** | Interface + disabled flag |
| FACIAL_ANIMATION | SadTalker | Apache-2.0 (non-commercial clause removed per README) | Older quality | Fallback candidate |
| FACIAL_ANIMATION | Hallo3 | MIT (code) | Built on CogVideoX-5B (own licence) + InsightFace | Not recommended |
| AUDIO_SEPARATION | **Demucs** (Meta) | MIT | Pretrained weights distributed with the repo; verify per model | Recommended |
| DIARIZATION | **pyannote.audio** | MIT | Pretrained pipelines on Hugging Face are gated (accept terms) | Recommended |
| ACTIVE_SPEAKER_DETECTION | TalkNet-ASD | MIT (code) | README: models trained on its datasets are **non-commercial** | Rejected for production. Built-in AV-correlation heuristic used instead |
| FACE_RESTORATION | GFPGAN | Apache-2.0 | Check facexlib / detector weights | Candidate |
| FACE_RESTORATION | CodeFormer | **S-Lab licence, non-commercial** | — | Rejected |

**Built in-house (no licence risk, implemented in Phase 2):**
- Voice-activity segmentation.
- Audio–visual active-speaker scoring: correlation between mouth-region motion per track and the audio envelope.
- Sync-offset QC: cross-correlation lag in ms.

These give real confidence scores and the manual-correction UX now. Model-based components plug into the same interfaces later.

## 6. Build vs buy

| Component | Decision |
|---|---|
| Face tracking | **Build.** Already built: detector + tracker + re-ID, swappable |
| Identity replacement | **Self-host open weights.** Commercial licences only; API fallback behind the router |
| Lip-sync | **Buy first, then self-host.** API (sync.so) at launch for quality; self-hosted MuseTalk/LatentSync after the licence fixes and the benchmark (§28) |
| Audio separation / diarization | **Self-host.** Demucs + pyannote; cheap on CPU/GPU |
| Enhancement | GFPGAN (Apache-2.0), after a dependency check |
| Full generative video (premium) | **Buy.** Veo/Kling-class APIs behind the router, premium only |

## 7. Threat model and moderation plan (summary)

**Impersonation and deceptive real-world claims:**
- Already in place: rights attestation for source videos, per-profile consent, and reports.
- Added:
  - Template rights evidence and a moderation gate before publish.
  - A creator suspension path.
  - Synthetic-media provenance on every output.

**Minors and NCII:**
- The analysis step already blocks the whole video on `minor_suspected` / `nsfw_source`.
- Template ingestion reuses this.
- The output scorer stays fail-closed in production.

**Marketplace fraud:**
- Self-use is excluded from earnings.
- Device/account farm signals feed a risk score; holds and a settlement delay apply.
- Clawback on refunds.

**Referral forgery:** server-issued HMAC tokens only; client-supplied creator ids are ignored for payouts.

**IDOR:**
- Every resource is checked against its owner, and the same 404 is returned for foreign ids. This exists today.
- New endpoints get dedicated tests.

**Webhook replay:** `webhook_events` unique `(provider, event_id)` already exists; store verification will use it.

## 8. Unit economics: illustrative, **not** verified store or provider contracts

**Assumptions** (all configurable):
- 1 credit ≈ $0.01 of retail value. Spec example: Pro is $14.99 for ~1000 credits.
- Self-hosted GPU at $1.99/h (H100, RunPod list price, 2026-10-08) or $0.79/h (L40S).

| Case | Remix job (2 persons, 8 s, 720p) | Credits | GPU cost | Lip-sync (API) | Margin per job |
|---|---|---|---|---|---|
| Best | L40S, 1 attempt, 90 s of GPU | 30 + 2×2×8 = 62 → ≈ $0.62 | $0.02 | $0 (no audio) | ≈ $0.60 |
| Base | H100, 1.3 attempts, 120 s | 62 → $0.62 | $0.09 | $0.53 (8 s × $0.067) | ≈ $0.00 |
| Worst | H100, 3 attempts, 180 s | 62 → $0.62 | $0.30 | $0.66 | **−$0.34** → needs the per-job cost ceiling + lip-sync surcharge |

**Conclusion:** lip-sync must be priced separately (a credit rule per second when `lip_sync.enabled`). The cost ceiling and margin floor in §28 are required before enabling lip-sync publicly.

## 9. Implementation plan: small mergeable PRs

| Phase | PR | Content | Flag |
|---|---|---|---|
| 1 | 1a | Mobile CI fix (`.npmrc legacy-peer-deps`); migration 0003; template slots + ingestion admin API | `template_v3` |
| 1 | 1b | `/feed` ranking (explainable score, weights in config), card fields, `/generations/estimate`, `/generations/remix` | `template_remix` |
| 1 | 1c | Mobile: multi-slot template remix screen | same |
| 2 | 2a | Worker audio stage: extraction, VAD segments, AV active-speaker scores, speaker → slot suggestions | `lip_sync` |
| 2 | 2b | Lip-sync / expression adapter protocols, mock + disabled real adapters, per-track lip-sync stage, sync-offset QC gate, credit rule | `lip_sync` |
| 2 | 2c | API: speaker mapping (manual correction), job config (`audio_mode`, `lip_sync`, `preserve`, `quality`) | `lip_sync` |
| 3 | 3a–c | Creator profiles, template submission, RevenuePolicy, earnings ledger, settlements, risk holds, analytics | `creator_marketplace` |
| 4 | 4a–b | Signed referral links, click/attribution, precedence rules | `referrals` |
| 5 | 5a–b | Credit buckets, reserve/settle/release, store verify (Apple/Google), subscription grants | `billing_v2` |
| 6 | 6a–c | Router cost ceiling / margin floor, benchmark harness, admin economics dashboard, push, deep links, load tests | — |

## 10. Assumptions and open questions (not invented; defaults are placeholders)

1. **Revenue-share rate:**
   - Creator share of eligible net revenue, and whether free/promo credits create earnings.
   - Placeholder: 0 % until set.
2. **Referral commission** and attribution window; whether it may stack with the template share.
3. **Lip-sync pricing** (credits per second) and the per-job USD cost ceiling / margin floor.
4. **Launch provider for lip-sync:** sync.so API, or self-hosted after the licence fixes.
5. **Template rights evidence:** what counts as acceptable proof (licence contract, own production, release forms).
6. **Payout rails / KYC vendor and jurisdictions.**
7. **Whether user-created templates may contain real people other than the creator,** and the consent evidence required.
