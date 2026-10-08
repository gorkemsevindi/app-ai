# Implementation ledger

## Slice 1: Studio vertical slice + viewer + monetisation core (2026-10-08)

**Files:** everything under `drama/`, plus `.github/workflows/drama-ci.yml`.

### Evidence

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `pytest`: 18 tests on SQLite | pass |
| `pytest` on PostgreSQL 16 | 17/17 pass (run before the 18th test was added) |
| `alembic upgrade head` + `alembic check` on PostgreSQL | applied, no drift |
| `tsc --noEmit` + `next build` | pass |
| Real renders (TR) | 6-episode seed, frames and QC reports in [TEST-REPORT.md](TEST-REPORT.md) |

### Measured performance

Local preview render: 360×640, about 0.7× real time on 4 vCPU. Final (720×1280): about 2.5× real time.

### What is real and what is mocked

| Component | Status |
|---|---|
| Script writer `local_template` | **Mock** (beat-bank templates, not an LLM). Flagged `writer_is_mock`, and the UI shows a badge |
| Anthropic writer | Implemented with structured output (`messages.parse` → `StoryBible`). **Not executed**: no key in this environment |
| TTS `espeak_local` | Real synthesis, robotic quality |
| Alignment and visemes | Real, derived from the audio |
| Video `studio_preview_local` | Deterministic 2D performer. **Not** generative video; labelled in the UI and in provenance |
| Music and ambience | Real procedural audio |
| Mix, captions, HLS, QC | Real (ffmpeg / libass) |
| Store | Sandbox emulator with signed receipts and notifications. Apple, Google and Stripe return `store.not_configured` |
| Payout rail | Sandbox (admin settle). KYC is a manual admin flag |

### Known gaps, to do next in this order

1. **Coins + Apple/Google IAP + Stripe web** (needs the decision on the store product model and the accounts).
2. **Premium provider adapters:**
   - ElevenLabs TTS (voice id per character)
   - Veo 3.1 image→video per shot
   - sync.so / LatentSync lip-sync
   - Nano Banana reference packs
   - each needs a key; they share the existing adapter contracts and pricing table.
3. **Mobile app (Expo)** reusing the API: feed, player, paywall with StoreKit 2 / Play Billing.
4. **Timeline editor:** shot reorder, trim, B-roll, per-shot re-render (TTS cache already avoids re-voicing unchanged lines).
5. **Subtitle translation, dubbing pipeline, RTL caption styles.**
6. **Production hardening:**
   - S3/R2 storage adapter and CDN
   - Redis rate limiter
   - OpenTelemetry + Sentry
   - load tests
   - backups/DR runbook
   - C2PA signing
7. **Distribution:** referral links, official YouTube/TikTok export flows.
8. **Recommendation experiments** (experiments table exists), push notifications.

### Risks

- The local engine's visual quality is far from commercial AI video. Real quality depends on provider
  contracts and per-episode cost (see 01-FEASIBILITY-AND-SCOPE.md §3).
- App review risk for UGC + AI + IAP. The moderation, reporting and blocking features required by
  guideline 1.2 exist server-side, but blocking users is not yet in the UI.
- Payout legal structure (TR entity versus Stripe availability).
