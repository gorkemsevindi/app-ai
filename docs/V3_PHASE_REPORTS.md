# V3 phase reports

Each phase follows the reporting checklist in spec §24.

## Phase 1: Template V3 (variable person slots, ingestion, remix, ranked feed)

**Feature flags:**
- `template_remix`: consumer remix. Disabled by default; returns 403 `feature_disabled` until enabled.
- `ranking`: optional weight overrides.
- Ingestion works while both flags are off.

### Files changed

| Area | Files |
|---|---|
| API | `app/models.py`, `app/services/templates_v3.py` (new), `app/routers/templates_v3.py` (new), `app/routers/templates.py`, `app/schemas.py`, `app/services/multiperson.py`, `app/services/generation.py`, `app/main.py` |
| Migrations | `alembic/versions/0003_templates_v3.py` |
| Tests | `tests/test_templates_v3.py` |
| Mobile | `src/lib/api.ts`, `src/app/(tabs)/index.tsx`, `src/app/template/[id].tsx`, `src/components/TemplateCard.tsx`, `src/locales/{en,tr}.json`, `.npmrc` |

### Migration 0003 (additive, reversible; upgrade → downgrade → upgrade verified, `alembic check` clean)

**Changes:**
- `templates`: + `creator_id`, `visibility`, `moderation_status`, `commercial_rights`.
  - Server defaults make every existing template `public` + `approved`.
- `template_versions`: + `source_video_id`, `config`, `credit_rule`, `published_at`.
  - A DB trigger makes published recipes immutable.
- New tables: `template_person_slots` and `template_metrics_daily`.

**Drift fix:** `fk_templates_current_version`, declared in 0001, was never created by Alembic. It is now added `NOT VALID`. Validate it in production after a data check.

### APIs

| Endpoint | Type | Notes |
|---|---|---|
| `GET /feed?category=&locale=` | New | Server-ranked. Categories include `new` and `multi_person`. Card fields: `use_count`, `est_credits`, `person_slots`, `est_seconds`, `safety_badge`, `creator`, `mode` |
| `POST /generations/estimate` | New | Credit quote, balance, `confirm_required` |
| `POST /generations/remix` | New | `Idempotency-Key` header; assignments are `{slot_id, profile_id}` |
| `GET /templates/{id}` | Additive | + `mode`, `person_slots`, `est_credits` |
| `GET /templates` | Changed | Now excludes non-public / non-approved templates; legacy templates are unaffected thanks to the defaults |
| `POST /admin/templates/{id}/source-video` (+ `/complete`, GET status) | New, admin | Ingestion: rights evidence first, then upload, then analysis (reuses the multi-person analysis job) |
| `POST /admin/templates/{id}/versions-v3` | New, admin | Define slots on the analysed tracks |
| `POST /admin/templates/{id}/publish` | New, admin | Publish a version |
| `POST /admin/templates/{id}/moderation` | New, admin | Review / takedown |
| `POST /admin/templates/metrics/refresh` | New, admin | Idempotent daily aggregation; run from cron |

### Behaviour

**Remix jobs:**
- A remix is a `multi_replace` job on the template's analysed clip, using the same queue, leases, retry/refund and worker as user-video replacement.
- Each job stores `template_version_id` and the slot → track mapping.
- Output provenance records `template_version_id`, so every output traces back to the exact version.

**Pricing:**
- Formula: `ceil((base + per_extra_slot × (n−1)) × resolution_multiplier)`.
- `base` defaults to the template's `credit_cost`. All parts are overridable per version (`credit_rule`) or globally (`template_remix.pricing`).
- Above `confirm_above_credits`, the client must echo the quoted price (`confirmed_credits`), otherwise it gets 409.

**Ranking:**
- The explainable score from spec §16, with Laplace-smoothed rates, freshness decay, a cold-start boost, a creator-diversity cap and a report-rate safety cut-off.
- Weights live only in the `ranking` flag; responses carry no score or weights.

### Environment variables

None new.

### Tests

| Check | Result |
|---|---|
| API | **41/41 passed** (35 existing + 6 new). Ruff clean |
| Worker | 6/6 passed. Ruff clean |
| Mobile | `npm ci` works again, `tsc` clean, tests 3/3 |

The new tests cover:
- Legacy templates are unchanged.
- Rights and evidence gates.
- Non-selectable tracks and duplicate slots are rejected.
- Invisible until published; the immutability trigger works.
- The flag gate.
- Price per slot count.
- IDOR: a foreign profile returns 404.
- Idempotent replay with a single debit.
- The confirmation threshold.
- Required slots.
- Takedown removes the template from feed, detail and remix.
- Metrics upsert idempotency.
- Ranking order and the safety cut-off; weights are not exposed.
- **Real worker e2e:** ingest → mock analysis → publish → 2-slot remix → completed output traceable to its version.

**Bug found and fixed:** `apply_analysis` overwrote `source_videos.analysis`, which dropped ingestion metadata. It now merges.

### Security and privacy

- Template source clips are owned by the ingesting admin account and keep the existing private storage plus signed URLs.
- Rights attestation is audited (`template.rights_attested`).
- Remix assignments only accept the requester's own consented, ready profiles; foreign ids get the same 404.
- The minor / NSFW / violence analysis flags still block whole clips and make tracks non-selectable.

### Estimated compute cost

Same as multi-person replacement: per-person sequential passes, so cost is linear in slots × seconds. See `docs/V3_AUDIT_AND_PLAN.md` §8.

### Known limitations

- The admin console has no UI yet; it is API only.
- Metrics refresh must be scheduled (cron).
- `paid_conversion` uses queue class (Pro) as a proxy until store verification (Phase 5).
- Template thumbnail and preview media are still URLs set by admins.

### Next step

Phase 2: audio-driven lip-sync and expression preservation.

---

## Phase 2: Music and speech lip-sync, expression preservation, audio modes

Feature flag: `lip_sync` (off by default). While the flag is off, nothing changes for lip-sync. The audio-mode fix below applies either way.

### Behaviour change and fix

Multi-person and remix outputs used to be delivered **without sound**, because `encode_vertical` always used `-an`. Jobs now default to `audio_mode=original`, which keeps the source soundtrack (spec §2.3). Two other modes exist:

- `none` reproduces the old silent output.
- `custom` uses a user-uploaded audio file that the user has attested rights to.

### Files

**Worker**
- `worker/audio/core.py`: ffmpeg 16 kHz extraction, RMS envelope, energy VAD with hysteresis, mouth-band motion per tracked box, articulation signal, correlation and lag search.
- `worker/audio/providers.py`:
  - Separation: `none`, or `demucs` via `AUDIO_SEPARATION`.
  - Lip-sync: `mock_lipsync` (dev only, refuses `WORKER_ENV=production`); `latentsync` and `musetalk` run as subprocesses and are licence-gated; `sync_so` always fails closed.
  - Expression: `passthrough`; LivePortrait is gated.
- `worker/audio/stage.py`:
  - Speaker analysis: a suggested visible speaker per voice segment, with confidence and per-track scores. Confidence is lowered when vocals are not separated.
  - Per-person lip-sync with a crop window and feathered paste.
  - QC per track: sync offset (ms), lip-sync score, motion preservation.
- Other worker changes:
  - `multiperson/adapters.py`: analysis adds the audio timeline; replacement runs lip-sync, QC and the QA gate.
  - `runner.py`: downloads custom audio.
  - `pipeline/encode.py`: optional audio mux (AAC 128k).

**API**
- `models.py` + migration `0004_audio_lipsync` (additive, reversible): new `audio_assets` table.
- `services/lipsync.py`:
  - Options, speaker timeline in API track ids, auto/manual/template mapping.
  - Pricing: `credits_per_second` × lip-synced seconds × premium multiplier.
  - Cost ceiling and margin floor are checked before any debit.
  - Custom audio: attestation, magic-byte sniffing, IDOR-safe access, delete.
- `services/multiperson.py`, `services/templates_v3.py`: audio plan inside the single debit; signed `audio_url` in the worker payload.
- `routers/multiperson.py`:
  - `GET /source-videos/{id}/speakers`
  - `POST /audio-assets`
  - `POST /audio-assets/{id}/complete`
  - `DELETE /audio-assets/{id}`
  - The quote endpoint now returns a `breakdown` with `lip_sync`.
- `routers/templates_v3.py`:
  - `estimate` accepts `lip_sync`.
  - `remix` accepts `audio`; a lip-sync price change returns 409 `confirmation_required` and rolls back before any debit.
  - The admin source status now includes the speaker timeline.
  - `version.config.speaker_mapping` (a curated timeline by `slot_id`) is validated.
- `routers/templates.py`: template detail adds `lip_sync_available`.
- `routers/account.py`: account deletion also marks audio assets as deleted.

**Mobile**
- The remix screen has a "Müzikle dudak senkronu" toggle, shown only when the template has a curated timeline. The live estimate includes lip-sync.
- New error texts in TR and EN.

### Speaker → person mapping policy (no guessing)

- Only replaced persons are lip-synced; people left original already match the soundtrack.
- A segment is auto-mapped only if the suggested speaker is replaced **and** confidence ≥ `min_mapping_confidence` (0.5).
- A low-confidence segment where a replaced person shows mouth activity returns 409 `speaker_mapping_required` with the evidence. The client then asks the user, or the user turns lip-sync off.
- Custom audio always needs a manual mapping.
- Template remixes use the curated timeline and skip slots the user kept original.

### Environment variables (worker)

| Variable | Purpose |
|---|---|
| `AUDIO_SEPARATION` (`none`/`demucs`), `DEMUCS_CMD`, `DEMUCS_TIMEOUT_S` | Vocal separation |
| `LATENTSYNC_LICENSE_CLEARED=1`, `LATENTSYNC_REPO/_CKPT/_PYTHON/_CMD` | LatentSync (the InsightFace weights are non-commercial, so legal sign-off is needed) |
| `MUSETALK_LICENSE_CLEARED=1`, `MUSETALK_REPO/_CKPT/_PYTHON/_CMD` | MuseTalk (licences of the dependent models must be checked) |
| `LIVEPORTRAIT_LICENSE_CLEARED=1` | Expression transfer (weights licence not verified) |
| `LIPSYNC_TIMEOUT_S` | Provider subprocess timeout |

Remote config `lip_sync` sets: provider, credits_per_second, premium_multiplier, max_offset_ms, min_sync_score, min_mapping_confidence, provider_usd_per_second, gpu_usd_per_person_second, credit_usd, max_job_cost_usd, margin_floor_usd, custom_audio_max_mb. There are no hard-coded prices or providers in clients.

### Tests

- API: **47/47** (6 new in `test_lipsync.py`):
  - Defaults, flag gate and timeline privacy.
  - Ambiguity returns 409, manual fix, exact pricing and debit.
  - Cost ceiling and margin floor block before any debit.
  - Custom audio: attestation, rejecting fake audio, IDOR, signed worker URL.
  - Curated template timeline by slot, with estimate equal to the debit.
  - **Real worker e2e:** analysis on a real A/V clip → mapping → mock lip-sync → QC metrics → delivered MP4 contains the audio stream.
- Worker: **13/13** (7 new in `test_audio.py`):
  - VAD and lag.
  - Correct speaker per segment.
  - Instrumental music gets low confidence.
  - Clip without an audio track.
  - **A 167 ms late mouth is detected and fails QC.**
  - Mock lip-sync stays within 50 ms and keeps motion.
  - Gated providers fail closed.
- Checks: `ruff` clean; `alembic check` shows no drift; mobile `tsc` and tests pass.

### Security and privacy

- Custom audio requires a rights attestation (own or licensed) and is owner-scoped. Non-audio files are rejected and deleted.
- Unlicensed models can't be enabled by accident: each needs an explicit `*_LICENSE_CLEARED` flag plus configuration.
- The mock provider refuses to run in production.

### Estimated cost

Lip-sync is priced separately (default 3 credits per lip-synced second; premium ×2). Job cost is estimated as GPU (person × seconds) + provider $/s. With the defaults, a job is refused above $5 or below the margin floor, if one is set. Real provider costs must be measured on a GPU before launch.

### Known limitations

- No real lip-sync provider has been validated on a GPU, so quality is unproven. sync.so is not integrated: the network policy blocks it and there is no contract.
- Speaker detection is a signal heuristic, not an ASD model. TalkNet-ASD is non-commercial.
- The mobile app has no manual speaker-mapping UI; it currently offers turning lip-sync off. The API is ready for that UI.
- The UI for uploading custom audio is not built yet; the endpoints are.

### Next step

Phase 3: creator marketplace. This covers creator profiles, template submission and moderation, a versioned RevenuePolicy, an append-only creator earnings ledger, settlement snapshots, risk holds and creator analytics.
