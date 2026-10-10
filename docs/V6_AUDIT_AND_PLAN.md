# V6 — Creator Character Identity Ecosystem: audit, gap matrix, plan

Source spec: `docs/MASTER_SPEC_V6_CHARACTER_IDENTITY.docx` (V1–V5 retained, V6 §V6.1–V6.16 added).
V6 is implemented **inside the existing app** (FastAPI API + Postgres queue worker + Expo mobile). No new app,
nothing copied from unrelated projects.

## 1. What already exists and is reused

| Area | Existing module | Reuse in V6 |
|---|---|---|
| Async jobs | `generation_jobs` + SKIP LOCKED claim, leases, retries, cancel (`services/generation.py`) | New job kind `character_asset` (one image per job → per-asset retry/refund, regenerate only failed assets) |
| Credits | Buckets, reserve → settle → release (`services/credits.py`) | Character previews/views are reserved before dispatch, settled once, refunded on failure |
| Creator money | Append-only `creator_earnings`, versioned `revenue_policies`, settlements, risk holds, clawbacks (`services/creators.py`) | Character royalties are a new earning kind `character_royalty` in the same immutable ledger (one payout pipeline) |
| Consent | `consent_receipts`, identity profiles (own likeness only), V4 licensed actors | Real-person reference characters only via the owner's own ready identity profile + consent receipt |
| Studio | Projects, immutable versions, storyboard shots, content-hash selective re-render, typed edits, worker shots + assembly | Cast snapshots, `@handle` resolution, lock modes, identity references and identity QC per shot |
| Routing | Adaptive router, kill switches, capabilities (`CHARACTER_REFERENCE`), model registry | Character-reference capability is required for locked cast shots |
| Learning | Content-free `learning_events`, aggregates, policies | Character consistency outcomes are logged without content |
| Moderation | Text screen, output moderation, reports, moderation actions, audit log | Character text screen, reports on characters, takedown, audit |
| Worker QC | YuNet + SFace face embedder (`multiperson/components.py`, env model paths) | Identity similarity across views/shots when the models are configured; otherwise proxy metrics, labelled as such |

## 2. Gap matrix (V6 vs repository before this work)

| V6 requirement | Before | Plan |
|---|---|---|
| V6.2 Creator wizard (description → spec → rights → previews → master → multiview → QC → voice → licence → publish) | Only project-scoped `studio_characters` (name, description) | `characters` + `character_identity_versions` (CharacterSpec JSON, original prompt kept) + wizard endpoints + mobile screen |
| V6.3 Master identity + multi-angle sheet, provenance, QC, package with checksums | Missing | Seed previews → approve master → identity-conditioned views (front/±45/±90/rear, expressions, mouth shapes, poses, lighting); per-asset provenance; validation report; package manifest + SHA-256 |
| V6.4 Immutable UUID + creator-scoped handle, registry, cast snapshots | Missing | `characters.id` immutable, unique `(creator_id, handle)`, `@creator/handle`; `project_cast_members` freezes identity + voice version |
| V6.5 Lock modes Standard/Strong/Strict + gates | Missing | Lock mode per cast member; strict = threshold gate + bounded retry budget + explicit failure, failed attempts refunded |
| V6.6 Script casting `@studio/mert`, disambiguation, AI Casting agent | Missing | Mention resolver (never silent), casting director (rule-based, user approval, licence-aware) |
| V6.7 Personality / voice | Missing | Character bible in spec; voice profile = licensed TTS voice id only; voice likeness cloning stays disabled |
| V6.8 Marketplace + licences | V4 actor marketplace (real likeness only) | Character listings (after rights review + QC certificate), explicit licence objects, grants |
| V6.9 Royalties | Actor licence earnings only | Per-usage licence credits on shot jobs, usage events, royalty on settle exactly once, waterfall capped by collected net revenue |
| V6.10 Tables | — | Additive migration `0015` (see §4) |
| V6.12 Learning without copying | V5 learning | Technical outcomes only; diversity/lookalike check for new characters |
| V6.13 Privacy/security | Tenant checks exist | Rendering access ≠ asset download: package assets never exposed to licensees; signed short URLs only for owners |
| V6.14 Tests | — | API e2e with the real worker (mock image + mock video providers), security tests, worker QC tests |

## 3. Provider capability / licensing assessment

Facts below come from Google documentation, as reported by web searches in October 2026. The container
cannot reach ai.google.dev (DNS blocked), so they are **not verified** against the live docs. Every real
provider therefore ships disabled and is configured by ops with a model id they have verified.

| Need | Candidate | Reported capability | Status in code |
|---|---|---|---|
| Master + multi-view images | Gemini image models via `google-genai` (`generate_content` with image parts) | Multiple reference images per request for character consistency | Adapter `gemini_image`: off until `CHARACTER_IMAGE_MODEL` + `GEMINI_API_KEY` are set and the provider is enabled in config. Output carries SynthID per Google. |
| Locked-cast video | Veo 3.1 `reference_images` (up to 3) | "Ingredients to video" for character consistency | Existing `veo` adapter stays **without** `CHARACTER_REFERENCE` until ops verify it on their account (config switch, no code change) |
| Identity QC | OpenCV YuNet (MIT) + SFace (Apache-2.0) ONNX | Face detection + 128-d embedding | Used when the model files are configured; otherwise proxy metrics, explicitly labelled `proxy` |
| Voice | Licensed stock TTS | — | Not integrated (no provider chosen). Voice profiles store provider/voice id/territory; synthesis stays disabled. Voice cloning disabled. |
| Dev/test | `mock_image` (synthetic portraits/figures), `mock_t2v` | Deterministic, labelled MOCK | Refuses to run in production |

Pricing: no vendor price is hard-coded. Credits per image and USD per image are remote config, priced by ops
from current price sheets.

## 4. Data model (migration 0015, additive)

| Table | Purpose |
|---|---|
| `characters` | Immutable UUID, creator, handle (unique per creator), display name, status, origin (synthetic/real_person), current/locked version |
| `character_identity_versions` | Major.minor versions, CharacterSpec (schema `cs1`), original prompt, master asset, package manifest + checksum, lock state |
| `character_assets` | Previews, master portrait/full body, views; storage key, SHA-256, provider/model/seed/params, reference ids, QC, creator approval |
| `character_quality_reports` | IdentityValidationReport per build and per shot (metrics, method, thresholds, verdict) |
| `character_rights_claims` | Append-only originality/rights declarations, consent receipt link |
| `character_voice_profiles` | Versioned voice profile (licensed TTS id; consented likeness disabled) |
| `project_cast_members` | Cast snapshot: project + character + identity version + voice version + lock mode + allowed variants + grant |
| `character_listings` | Marketplace listing: visibility, versioned licence terms, usage price, certification, review status |
| `character_license_grants` | Licence grant ids with frozen terms, idempotency key |
| `character_usage_events` | Per job × character usage (seconds, credits), reserved → settled/released, unique per job |
| `reports.character_id` | Reports/takedowns on characters (existing reports table extended) |

Adaptations (repository wins over the spec):
- Royalty ledger entries = `creator_earnings` rows of kind `character_royalty`. This keeps one immutable
  payout ledger, the settlement and risk machinery, and clawbacks.
- Moderation cases and audit events reuse `reports`, `moderation_actions` and `audit_logs`.
- Personality and variants live inside the versioned CharacterSpec. A wardrobe variant is a minor identity
  version, so no extra tables are needed.
- `scene_character_tracks` is kept in the shot job spec: shot → cast members → identity version → references.

## 5. API contracts (adapted to existing conventions)

- `POST /characters` creates a draft, `GET /characters`, `GET /characters/{id}`.
- `POST /characters/{id}/rights` records the declaration.
- `POST /characters/{id}/preview` starts seed previews (reroll = call again); estimate first, confirm the credits.
- `POST /characters/{id}/approve-master` approves a master asset.
- `POST /characters/{id}/identity-build` starts the multi-view build.
- `GET /characters/{id}/jobs` returns progress.
- `POST /characters/{id}/assets/{asset_id}/review` records creator approval or rejection; rejected assets regenerate only themselves via `POST /characters/{id}/repair`.
- `POST /characters/{id}/validate` returns an IdentityValidationReport.
- `POST /characters/{id}/lock` locks the identity version.
- `POST /characters/{id}/versions` creates a major or minor version.
- `POST /characters/{id}/voice` sets the voice profile.
- `GET /characters/{id}/package` returns the IdentityPackageManifest (owner only).
- `GET /characters/search` lists public, live characters plus your own.
- `GET /characters/resolve?mention=` resolves a mention.
- `POST /studio/projects/{id}/cast`, `GET /studio/projects/{id}/cast`, `DELETE …/cast/{member}` manage the CastBinding.
- `POST /studio/projects/{id}/resolve-script` resolves mentions in a script.
- `POST /studio/projects/{id}/casting` asks the casting director for suggestions.
- `POST /characters/{id}/listing` publishes, `POST /admin/characters/{id}/review` reviews, `POST /admin/characters/{id}/takedown` takes down, `POST /characters/{id}/report` reports.
- `POST /licenses/quote` and `POST /licenses/grant` issue character licences.
- `GET /characters/licenses` lists licences. `GET /creator/characters/analytics` (usage and royalty) is listed in §8 as not built.

## 6. Phases (V6.15, adapted to the order the user asked for)

1. **Phase A (this doc):** audit, gap matrix, provider assessment, data model, API.
2. **Phase B:** Character Creator, multi-angle identity, persistent ID, character lock, `@character`.
   - Characters, spec, rights, previews, master, multi-view build, QC report, package, lock, versions.
   - Cast snapshots, `@handle` resolution, lock modes in Studio (reference conditioning + identity gate + strict retry budget).
   - Real worker e2e.
3. **Phase C:** AI Casting Director, Character Marketplace, Creator Royalty.
   - Casting suggestions, listings with rights review, licences and grants, usage pricing in the estimate.
   - Usage events, royalties settled exactly once, refunds, takedown and revocation, reports.
4. **Phase D:** learning integration and abuse controls.
   - Content-free character consistency outcomes, diversity and lookalike checks for new characters.
   - Rate limits on character creation, mobile screens.

## 7. Acceptance tests to run (V6.14)

- **E2E:** creator creates an original actor → approves the master → multi-view assets → validation report → lock → publish with rights/licence → another user casts it by `@handle` → two scenes render → identity continuity is measured → credits and royalties settle exactly once.
- **Disambiguation:** the same display name owned by two creators requires disambiguation.
- **Strict lock:** an identity failure leads to a bounded retry, then an explicit failure with a refund (never "verified").
- **Edits:** scene edits keep the cast snapshot; a creator's new version does not change existing projects.
- **Takedowns:** suspension, takedown and rights revocation block future renders.
- **Security:** cross-tenant access, a forged licence grant, asset URL leaks, voice cloning blocked, concurrent royalty settlement.

## 8. Explicitly out of reach in this environment (reported honestly)

- Real image and video identity quality. It needs a configured provider (`GEMINI_API_KEY`, a verified image model id, prices) and calibrated thresholds on a held-out synthetic set.
- The SFace/YuNet model files are not shipped. Without them, identity QC uses proxy metrics (colour/structure), which are labelled `proxy` and can never yield "verified".
- TTS and lip-sync providers are not chosen. Dialogue stays captions plus speaker mapping.
- Legal review of the character licence terms is required before the marketplace flag can be enabled (same gate as V4 actors: `legal_review_ref`).
