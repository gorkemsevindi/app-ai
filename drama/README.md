# Sahne (working name): AI short-drama platform

**Watch → Create → Earn.** Viewers watch serialized vertical (9:16) micro-dramas. Creators build
characters and generate episodes in the AI Studio, with:
- natural dialogue
- facial expressions
- lip-sync
- voice, music and captions

They then publish and earn a defined share of qualified net revenue.

This product is **independent** of everything else in this repository. All of its code lives under
`drama/` and shares nothing with `apps/` or `services/`. It should move to its own repository; see
[ADR-0001](docs/adr/README.md).

## Status, slice 1 (2026-10-08)

| Area | Status |
|---|---|
| AI Studio: wizard → story bible → scripts → Character DNA (edit / lock / versions / expression library) → render → QC → preview → submit | ✅ working end to end with **local** providers |
| Performance: TR/EN speech, phoneme visemes, energy-driven jaw, 7 expressions (incl. crying), brows, blinks, gaze, head motion | ✅ local 2D performer (labelled; not a generative video model) |
| Audio: per-character voices with emotion prosody, procedural score, ambience, sidechain ducking, −14 LUFS | ✅ |
| Captions: word-timed karaoke ASS (burned in), speaker colours, safe zone, SRT/VTT, drift QC | ✅ |
| Jobs: idempotent, resumable, leased steps, retry cap + refund, spend caps, cancel, cost report + premium-route shadow estimate | ✅ |
| Viewer: feed (for you / trending / following / new / genre / search), series pages with OG metadata, HLS player, resume, history, follow, comments, reports | ✅ web |
| Money: first-5-free paywall, sandbox store with signed receipts + refund notifications, entitlements, double-entry ledger, 60/40 net allocation, hold → available, KYC-gated payouts | ✅ sandbox |
| Trust & safety: moderation rules, report queue, takedown propagation, consent-gated likeness/face swap, revocation, audit log, AI labels + provenance | ✅ |
| Real AI providers (Claude, Veo, ElevenLabs, sync.so, Lyria …) | Adapters / plan ready; **keys and contracts needed**, see [DECISIONS-NEEDED.md](docs/DECISIONS-NEEDED.md) |
| Mobile app (Expo), Apple/Google IAP, coins | Next slices |

Every local or mock component is marked in episode provenance (`mock_components`) and in the Studio UI.

## Quick start (no API keys, no GPU)

Requirements: Python 3.11+, Node 22, ffmpeg, espeak-ng.

```bash
# backend
cd drama/backend
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,anthropic]"
python -m dramaapp.seed                 # demo users + a 6-episode series rendered by the local pipeline (~6 min)
uvicorn dramaapp.main:app --reload      # API on :8000, OpenAPI at /docs, inline worker in dev
# web
cd ../web && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000
```

Or run everything in Docker: `cd drama && docker compose up --build`.

Demo logins are created by the local seed only, never in staging or production:
- `creator@demo.example.com` / `creator-demo-pass`
- `viewer@demo.example.com` / `viewer-demo-pass`
- `admin@demo.example.com` / `admin-demo-pass`

## Tests

```bash
cd drama/backend && ruff check . && python -m pytest -q        # SQLite
TEST_DATABASE_URL=postgresql+psycopg://… python -m pytest -q   # PostgreSQL (CI)
cd drama/web && npm run typecheck && npm run build
```

The suite includes the spec §11 acceptance tests:
- three ~60 s episodes with consistent characters and timed captions
- five free episodes, then a sandbox purchase of episode 6
- the creator allocation is posted once
- an unauthorised face swap is blocked
- an interrupted job resumes without being billed twice
- spend caps are enforced

Results: [docs/TEST-REPORT.md](docs/TEST-REPORT.md).

## Docs

| | |
|---|---|
| [00-REVIEW-AND-RECOMMENDATIONS](docs/00-REVIEW-AND-RECOMMENDATIONS.md) | What to add, change and improve in the spec |
| [01-FEASIBILITY-AND-SCOPE](docs/01-FEASIBILITY-AND-SCOPE.md) | Feasibility/licence table, MVP-vs-later matrix, unit economics, cloud bill |
| [02-PROVIDERS-AND-COSTS](docs/02-PROVIDERS-AND-COSTS.md) | Provider pricing research (V/S/3P graded, sources), cost per episode |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, pipeline, ERD, ledger, API, events, security |
| [adr/](docs/adr/README.md) | Decision log |
| [DECISIONS-NEEDED](docs/DECISIONS-NEEDED.md) | Commercial decisions + accounts/keys required |
| [IMPLEMENTATION-LEDGER](docs/IMPLEMENTATION-LEDGER.md) | Slices, evidence, known gaps, next steps |
| [TEST-REPORT](docs/TEST-REPORT.md) | Test runs and screenshots |
