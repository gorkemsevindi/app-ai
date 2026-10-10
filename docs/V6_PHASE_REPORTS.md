# V6 — Creator Character Identity Ecosystem: phase reports

Spec: `docs/MASTER_SPEC_V6_CHARACTER_IDENTITY.docx`. Audit and plan: `docs/V6_AUDIT_AND_PLAN.md`.
Everything is built into the existing app (API, worker, mobile). V1–V5 behaviour is preserved: the full
regression suite passes.

Status legend (V6.14):
- **WORKING**: implemented and proven end to end by tests in this repo.
- **PARTIAL**: works, but part of the requirement is missing.
- **MOCK**: runs only with dev/test providers. A real provider must be configured and verified.
- **MISSING**: not built.

---

## Phase A — Audit and design

- **Done:** `docs/V6_AUDIT_AND_PLAN.md`. It covers:
  - the reuse map;
  - the gap matrix;
  - the provider capability and licensing assessment (official docs unreachable from the container, so marked unverified);
  - the data model and migrations;
  - the API contracts;
  - the phases and the acceptance list.
- **Adaptations** (the repository wins over the spec):
  - Royalties are `creator_earnings` rows of kind `character_royalty`, in the one immutable payout ledger.
  - Moderation and audit reuse `reports`, `moderation_actions` and `audit_logs`.
  - Variants and personality live in the versioned CharacterSpec.

## Phase B — Character Creator, multi-angle identity, persistent ID, character lock, @character

### Files
- **New:**
  - `services/api/app/services/characters.py`
  - `services/api/app/services/casting.py`
  - `services/api/app/routers/characters.py`
  - `services/api/alembic/versions/0015_character_identity.py`
  - `services/worker/worker/characters/{images,qc}.py`
  - `apps/mobile/src/app/characters/{index,[id]}.tsx`
  - `apps/mobile/src/components/CastPanel.tsx`
- **Changed:**
  - `models.py`: 10 tables, `job_kind.character_asset`, `reports.character_id`
  - `generation.py`: character asset completion, identity gate, refund hook, royalty order
  - `studio.py`: cast characters, @mentions in the brief, lock pricing, references at dispatch, identity gate, strict retries, identity report, attribution
  - `economics.py` and `learning.py`: feature names `studio`/`character`, lock provenance
  - `account.py`: deleting an account deletes its characters and their images
  - worker `runner.py`, `registry.py`, `studio/shots.py`: the mock pastes every character's reference
  - mobile layout, profile, studio project screen, locales (en/tr)

### Migration
`0015` is additive. Tables:
- `characters`
- `character_identity_versions`
- `character_assets`
- `character_quality_reports`
- `character_rights_claims`
- `character_voice_profiles`
- `project_cast_members`
- `character_listings`
- `character_license_grants`
- `character_usage_events`

Plus `reports.character_id` and the enum value `job_kind.character_asset`. Upgrade, downgrade and re-upgrade were tested. `alembic check` is clean.

### APIs
- **Creator studio:**
  - `POST/GET /characters`, `GET /characters/{id}`
  - `GET /characters/{id}/quote`
  - `POST /characters/{id}/preview`, `approve-master`, `identity-build`, `repair`
  - `GET /characters/{id}/jobs`
  - `POST /characters/{id}/assets/{asset}/review`
  - `POST /characters/{id}/validate`, `lock`, `versions`, `voice`, `revoke-likeness`
  - `GET /characters/{id}/package`
  - `DELETE /characters/{id}`
- **Resolution:** `GET /characters/resolve`, `GET /characters/search`
- **Cast:** `POST/GET /studio/projects/{id}/cast`, `DELETE …/cast/{member}`, `POST /studio/projects/{id}/resolve-script`, `GET /studio/projects/{id}/identity-report`

### Flags and configuration
- `characters` (off by default). Remote config holds:
  - image provider;
  - USD per image;
  - credits per preview and per view;
  - view list;
  - QC thresholds;
  - lock multiplier and strict retry budget;
  - the licensed TTS voice catalogue;
  - blocked names and IP terms.
- **Env (worker, only for a real provider):**
  - `GEMINI_API_KEY`
  - `CHARACTER_IMAGE_MODEL`
  - `MP_YUNET_ONNX` and `MP_SFACE_ONNX` (face-embedding QC)

### Status

| Feature | Status | Evidence / note |
|---|---|---|
| Original character creation, CharacterSpec `cs1` with original prompt preserved, rights declaration, adult-only, minor / lookalike / blocked-IP screens, creator-scoped handles | **WORKING** | `test_creation_rights_screens_and_handles` |
| Seed previews, reroll, idempotent paid jobs (reserve → settle; refund on failure) | **WORKING** (mock images) | `test_identity_build_…`, `test_failed_image_is_refunded…` |
| Master approval → identity-conditioned 17-view sheet (±45°, ±90°, rear, 3 expressions, 3 mouth shapes, close-up, full body, walking, 2 lighting variants), per-asset provenance (provider, model, seed, references, job) | **WORKING with MOCK provider** | the mock derives views from the master; real image quality needs a configured provider |
| Identity QC: face-embedding similarity (SFace) where meaningful, proxy metrics otherwise, side/rear/body flagged, repair regenerates only failed/rejected views, creator manual review per view | **PARTIAL** | proxy metrics are implemented and tested; face embedding runs only when the ONNX models are configured (not in this environment); thresholds are not calibrated on a held-out set |
| IdentityValidationReport, never "verified" on proxies or without creator review | **WORKING** | verdict `warn`/`verified: false` asserted |
| Lock, IdentityPackageManifest with SHA-256 checksums, no stored embeddings, minor (cosmetic) vs major (immutable traits) versions | **WORKING** | `test_identity_build_…` |
| Persistent ID: immutable UUID, `@creator/handle`, never-silent resolution (bound / needs_cast / ambiguous / not_found) | **WORKING** | resolve tests, ambiguity test in Phase C |
| Cast snapshot (character + identity version + package checksum + voice + lock mode); creator updates don't change existing scenes | **WORKING** | `test_cast_mentions_strict_lock_and_two_scenes_e2e` (0 new shots after a new version) |
| @mentions in the brief → shots per character, plan blocked until every mention is cast | **WORKING** | same e2e |
| Lock modes: standard (report only), strong/strict (identity multiplier, remote config), strict gate with a bounded retry budget, failed attempts refunded, explicit `identity_failed` | **WORKING** | `test_strict_lock_bounded_retries…`, `test_strict_retry_then_pass…`, `test_standard_lock_never_blocks_delivery` |
| Reference conditioning of shots (canonical portrait + full body + 3/4 views, max 3) | **MOCK** | the worker receives signed URLs; `mock_t2v` uses them; `veo` keeps `CHARACTER_REFERENCE` off until ops verify Veo 3.1 reference images |
| Per-shot identity measurement (frames vs canonical) | **PARTIAL** | proxy template matching works and is tested; face-embedding path needs the models |
| Voice profile (licensed TTS catalogue only), voice cloning blocked | **PARTIAL** | profile and versioning work; **no TTS synthesis** (no provider chosen) |
| Lip-sync per character line | **MISSING** for Studio | V3 multi-person lip-sync exists for templates only; Studio dialogue stays captions plus a speaker key |
| Mobile: character list/create, identity workflow (previews, master, sheet review, repair, lock), cast panel with lock modes and disambiguation | **WORKING** (tsc + locale parity) | not run on a device in this environment |

## Phase C — AI Casting Director, Character Marketplace, Creator Royalty

### APIs
- **Marketplace:**
  - `POST/DELETE /characters/{id}/listing`
  - `POST /admin/characters/{id}/review`
  - `POST /admin/characters/{id}/takedown`
  - `POST /characters/{id}/report`
- **Licences:** `POST /licenses/quote`, `POST /licenses/grant` (Idempotency-Key), `GET /characters/licenses`
- **Casting and analytics:** `POST /studio/projects/{id}/casting`, `GET /creator/characters/analytics`

### Flag
`character_marketplace` stays off unless `legal_review_ref` is set (same gate as V4 actors). The revenue policy gains `character_share_rate` (default 0).

### Status

| Feature | Status | Evidence |
|---|---|---|
| Listing only for locked, original (synthetic) characters with a non-failing validation; every publication or terms change is human-reviewed; certification badge ("consistency tested", `verified` only with face embedding) | **WORKING** | `test_listing_gate_…` |
| Explicit licence object: personal/commercial, categories, prohibited contexts (adult and political always), modification, no sublicensing, non-exclusive, territories, duration, revocation policy, rendered projects retained, attribution, price per scene and/or per second, versioned terms | **WORKING** | same |
| Grants: idempotent, frozen terms snapshot, territory check, own-character refused; search ≠ licence; casting another creator's character needs an active grant; licensees use the listed identity version | **WORKING** | same |
| Licence credits in the render quote (compute + licence), usage events reserved/settled/released, royalty once per job (repeated settle is a no-op), only the paid share earns, promo credits earn nothing | **WORKING** | `test_royalties_…`, `test_waterfall_…` |
| Waterfall: character royalties first, then template/referral capped by collected net revenue | **WORKING** | `test_waterfall_…` (sum ≤ gross) |
| Clawback of royalties on a support refund (and store refunds via the existing proportional clawback) | **WORKING** | `test_royalties_…` |
| Multi-character split by each character's own contract price | **WORKING** (by design: one usage row per character per shot) | covered by code path; e2e uses one character |
| Unpublish: no new grants, existing grants run to term end. Takedown: grants revoked, queued shots refused at dispatch with refund, usage released | **WORKING** | `test_unpublish_…takedown…` |
| Reports: the third open report pauses the listing and flags the character; reports feed creator payout risk holds | **WORKING** | `test_reports_…` |
| Prohibited-context screen on storyboards for licensed characters | **WORKING** (keyword screen) | `test_reports_…` |
| Cross-tenant: cast members of another user's project are rejected; public card exposes no reference assets or package | **WORKING** | `test_listing_…`, `test_reports_…` |
| Lookalike screen (perceptual hash vs other creators' public masters) + near-duplicate spec → flagged → listing blocked until review | **WORKING** (heuristic) | `test_lookalike_…` |
| AI Casting Director | **PARTIAL** | rule-based ranking (role fit, age, style, language, cost, licence status, certification), labelled "not AI"; suggestions only. No LLM casting yet |
| Payout of royalties | **WORKING** via existing settlements | real payouts need the KYC/payout provider (V4 backlog) |

## Phase D — learning and abuse controls (V6.12 subset)

| Feature | Status |
|---|---|
| Content-free learning events for character jobs (`feature=character`, lock mode/attempt provenance, identity QC numbers) | **WORKING** (automatic through the V5 listener) |
| Diversity check for new characters (lexical spec similarity) | **PARTIAL** (lexical only, warning) |
| Rate limits: creation, image jobs, licences, reports | **WORKING** |
| Learned routing for identity conditioning, character variants at scale, internationalization beyond en/tr, load tests for character jobs | **MISSING** |

## Tests run (this milestone)

- **API: 129/129 passed.** That is 115 existing tests plus 14 new V6 tests:
  - `test_characters.py` (7)
  - `test_character_market.py` (7)
  - One existing V4 contract was kept: a licence-term violation during planning still returns 502 with `reason=license_terms_violation`.
- **Worker:** 24/24. This includes the new `test_characters.py` (4): mock views conditioned on the master, proxy QC, shot identity, Gemini adapter fails closed and uses reference parts.
- **Mobile:** `tsc` clean, 6/6 (locale parity covers the new en/tr keys).
- **Migrations:** `alembic check` clean; downgrade to 0014 and upgrade again tested.

## Security and privacy

- Reference images are private storage objects. Only the owner gets 10-minute signed URLs, and the worker gets 1-hour signed URLs per job. Licensees and the public never see assets or the package (rendering access ≠ asset access).
- No biometric embeddings are stored. Face embeddings, when enabled, exist only in worker memory during QC.
- Real-person characters require the owner's own ready identity profile plus a consent receipt. They are re-checked at every dispatch, revocable, and not listable.
- Every money event is idempotent and append-only:
  - usage events are unique per (job, character);
  - royalty idempotency key is `croyalty:{job}:{character}`;
  - grant key is `cgrant:{user}:{Idempotency-Key}`.
- Account deletion revokes grants, deletes the character images and keeps the ledger rows (accounting retention).

## Estimated compute cost (configurable; no vendor price is hard-coded)

- **Character build:** (previews N + 17 views) × `provider_usd_per_image`.
  - Default credits: 2 per image, so 4 previews + 17 views = 42 credits.
  - Each repair costs 1 image per failed or rejected view.
- **Locked shot:** studio compute × `strong_credit_multiplier` (default 1.5) for strong or strict mode, plus licence credits (per scene/second, set by the character's creator).
- **Strict retries:** at most `strict_retry_budget` (default 2) extra provider calls per shot. They are refunded to the user and are the platform's cost.

## Known limitations

- Real identity quality is unproven. It needs a verified reference-conditioned image model and a video provider with character references, plus threshold calibration on a held-out synthetic set (with confidence intervals). The mock providers only prove the pipeline.
- The proxy metrics are weak:
  - a rear view scores high on colour;
  - lighting changes lower colour similarity.
  - They are labelled `proxy` and can never make a result "verified".
- TTS, voice synthesis and per-character lip-sync in Studio are not integrated.
- The casting director is rule-based; the diversity check is lexical.
- The licence terms need legal review before the marketplace flag can be enabled.

## Next step

Configure and verify real providers:
- an image model with reference conditioning;
- Veo 3.1 reference images;
- the SFace/YuNet models.

Then calibrate the QC thresholds on a rights-cleared synthetic set and run a closed beta of the marketplace behind `character_marketplace` after legal review.
