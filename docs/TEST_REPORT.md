# Test report — 2026-10-05

Environment: Linux x86_64, Python 3.11, PostgreSQL 16, ffmpeg 6.1, Node 22, **no GPU**.

| Suite | Result |
|---|---|
| API unit + integration (`services/api/tests`) | **35 passed**, coverage 86 % of `app/` |
| Worker (`services/worker/tests`) | **6 passed** |
| Mobile (`npm run typecheck`, `npm test`) | tsc clean, 3 passed; Android bundle exports with Metro |
| Migrations | `upgrade head → downgrade base → upgrade head` round-trips |
| Lint | ruff clean (api, worker) |

## What is covered
- Auth: signup/login, consent, refresh rotation + reuse detection, login rate limit.
- Identity: consent required, MIME/size validation, magic-byte rejection, IDOR, deletion removes media.
- Credits: append-only trigger, **10 concurrent debits on 30 credits → exactly 3 succeed**, reconciliation.
- Generation: happy path, idempotent create (and replay not rate limited), insufficient credits, blocked text costs
  nothing, retries + automatic refund, **worker crash → lease expiry → re-queue, stale worker rejected, no double
  charge**, cancel (queued + running), output moderation block → refund, kill switch + fallback routing,
  priority queue, IDOR, worker auth, report → admin moderation, admin template lifecycle without app update.
- E2E (real worker loop, mock model, real ffmpeg): upload → generate → 720×1280 H.264 yuv420p + thumbnail.
- Multi-person: feature flag gating, attestation, upload validation, pricing (persons × seconds × resolution,
  preview), assignment rules (foreign profile 404, non-selectable, duplicates, max persons, unknown track),
  minor flag rejects + moderation log + media deletion, **e2e: synthetic 3-person video → analysis finds 3 people,
  re-entering person keeps one ID → assign 2 → quote = debit → replacement → composite → encode → result**,
  analysis jobs hidden from library, video deletion removes all media.
- Worker multi-person: tracker keeps ID across missed detections, stitcher merges re-entries but never
  co-existing tracks, depth-ordered effective masks, composite leaves outside pixels untouched, occlusion-safe
  2-person replacement with outside-mask MAE < 1.5 and motion-compensated flicker within the QA gate.

## Found and fixed during testing
- Per-frame colour matching made a static replaced person flicker when someone walked past → clip-level transform.
- Idempotent replays of the same request were rate limited → replays exempt.

## Not covered yet
Real GPU models, load tests, mobile E2E (Maestro/Detox), purchase flows (Phase 4).
