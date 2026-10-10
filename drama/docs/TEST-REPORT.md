# Test report: slice 1 (2026-10-08)

Environment: Linux container, 4 vCPU, Python 3.13, ffmpeg 6.x with libass, eSpeak NG 1.51,
PostgreSQL 16 and SQLite, Node 22, Chromium (Playwright).

## Automated

| Suite | Result |
|---|---|
| `ruff check .` | clean |
| `pytest` on SQLite | **23 passed** (includes the feed regression test and 5 realistic-route contract tests) |
| `pytest` on PostgreSQL 16 (`TEST_DATABASE_URL`) | **17 passed** (353 s, including three real 60 s renders; run before the feed test was added) |
| `alembic upgrade head` + `alembic check` on PostgreSQL | 29 tables, no drift |
| `tsc --noEmit`, `next build` | pass |

### Acceptance criteria (spec §11), mapped to tests in `tests/test_acceptance.py`

| Criterion | Test | Evidence asserted |
|---|---|---|
| A creator makes 3 coherent ~60 s episodes with consistent characters, speech and timed captions | `test_creator_makes_three_coherent_episodes` | 3 real renders. Every QC check passes: duration, 9:16, black frames, audio, loudness −14 ±2.5 LUFS, subtitle drift ≤ 0.15 s, identity hash, language, consent. One DNA hash per character across all episodes. VTT cue count ≥ line count. Spent credits equal the estimate |
| Another user watches five free episodes in a seeded series and buys episode 6 in the sandbox | `test_viewer_five_free_then_buys_episode_six_and_allocation_posted_once` | Episodes 1–5 unlocked and episode 6 returns 402. Signed HLS master, variant and segment chain works. Purchase verified, then episode 6 plays |
| The verified creator allocation is posted once | same test | A replayed receipt returns the same purchase. Exactly 1 RevenueAllocation. net = gross − tax − fee, and creator = 60 % of net. A tampered price is rejected. Apple returns `store.not_configured`. A duplicate refund notification is deduplicated. Trial balance is 0 |
| An unauthorised face swap is blocked | `test_unauthorized_face_swap_and_likeness_blocked_then_revocation_propagates` | 403 `rights.consent_required` without a grant and while the grant is pending review. A licensed actor without evidence gets 400. After approval: `provider.unavailable`, an honest error because no provider is contracted. Revocation blocks the character and later renders |
| An interrupted job resumes | `test_interrupted_job_resumes_without_double_billing` | The worker "crashes" after `performance`; the lease blocks other workers. When the lease expires, a second worker resumes. `performance` ran once, `spent_credits` is unchanged, and there are exactly 2 billing transactions |
| The spend cap is enforced | `test_spend_caps_enforced` (+ `test_failed_job_refunds_and_retry_cap`) | `credits.max_spend_too_low`, `credits.daily_cap` (429), viewer gets 403 in the studio. Repeated provider failure ends in `failed` with a full credit refund |
| Plus | `test_creator_payout_flow`, `test_publish_gate_and_takedown` | Hold leads to release, then available, then KYC gate, then payout, then settle, and the trial balance stays balanced. Manual publish gate. An urgent report gets priority ≤ 5. Takedown removes playback |

## Manual and visual (Playwright, Chromium, live API + `next start`, seeded demo data)

Seed: 6 Turkish episodes rendered by the real local pipeline. Durations were 63–64 s, all QC checks
passed, and each episode took about 80 s to render on 4 vCPU.

| Flow | Result | Screenshot |
|---|---|---|
| Expression library: neutral, happy, sad/crying, angry, fear, surprise, tender + visemes | ok | `screenshots/00-expressions.jpg` |
| Vertical feed: tabs, genres, HLS autoplay, AI label | ok | `screenshots/01-feed.jpg` |
| Series page: free/locked badges | ok | `screenshots/02-series.jpg` |
| Episode 1 playback with burned karaoke captions and next-episode button | ok | `screenshots/03-watch.jpg` |
| Episode 6 → paywall (episode / season) → sandbox purchase → plays | ok | `screenshots/04-paywall.jpg`, `05-unlocked.jpg` |
| Studio list and project: Character DNA editor, lock, reference sheet, episode jobs with step status | ok | `screenshots/06-studio.jpg`, `07-project.jpg` |
| Creator earnings / analytics | ok | `screenshots/08-earnings.jpg` |
| Admin: review queue, moderation, payouts, ledger trial balance, provider key status | ok | `screenshots/09-admin.jpg` |

Browser console showed only the expected 402 from the locked episode, and one 404.

### Bugs found by this manual pass and fixed

- **SQLite returned naive datetimes, which crashed `/feed` ranking.**
  - Fix: a `UTCDateTime` column type that is timezone-aware on every backend.
  - Regression test: `test_feed_tabs_and_search`.
- **Render caches peaked at about 2.8 GB of RSS.**
  - Fix: bounded caches, cleared after each render.
  - Now about 0.8 GB peak, with render time unchanged.
- **Feed video was cropped by `object-fit: cover`, which clipped burned captions.** Fix: switched to `contain`.
- **Published episodes could be re-rendered and disappear from the catalogue.** Fix: the API now returns 409 `episode.immutable`.
