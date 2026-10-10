# Master Spec V5: phase reports

## Phase A: audit, gap report, plan

See `docs/V5_AUDIT_AND_PLAN.md` for:
- the component-by-component status (existing, partial, missing);
- the gap table against every V5 section;
- the data-governance defaults;
- the phased plan B–E;
- risks.

No code was changed in this phase.

## Phase B: instrumentation, learning consent, technical memory, dashboard

Feature flag: `learning` (off). Migration: `0011_learning_foundation` (additive, reversible; `consent_records` is append-only).

### What works

**Content-free job outcome events**
- When the flag is on and the user hasn't opted out, every finished job (completed, failed, cancelled, refused at dispatch) writes exactly one `job_outcome` event:
  - feature and intent category;
  - creative mode and prompt-strategy version (`balanced` / `v0` until Phase C);
  - provider, resolution and duration;
  - success, error code and retries;
  - latency and credits;
  - estimated vs actual cost;
  - numeric QA signals.
- Each event also carries the consent snapshot, purpose, provenance, schema version and a retention deadline (default 400 days).
- The event is captured by a database flush hook, so no code path that ends a job can skip it.
- Benchmark runs are excluded.
- **No prompt, user text, media URL or face data is stored.** A test checks this against the whole table.

**Learning consent (`/me/learning-consent`)**

| Purpose | Default |
|---|---|
| `technical_improvement` | on, can be turned off |
| `content_training` | off (opt-in) |
| `personalization` | off (opt-in) |

- Every change appends a versioned receipt.
- `DELETE /me/learning-data` deletes the user's events and resets the opt-ins.
- Account deletion does the same.
- No content is retained for training in this phase, even with opt-in. The opt-in is only a recorded preference.

**Feedback (`POST /generations/{id}/feedback`)**
- A rating from 1 to 5 plus known reason tags.
- Only the owner can rate, and only completed jobs.
- One rating per job: a repeat replaces the earlier one, so it never adds weight.
- Rate limited.
- Stored apart from popularity signals.

**Technical memory**
- Scheduled task `learning_aggregate` rolls up per day × provider × feature × mode, from consented events only. It is idempotent.
- Each roll-up row holds: jobs, successes, p50/p95 latency, cost, rating count and mean, QA means, error taxonomy.
- No identities are kept in the roll-up.

**Admin learning dashboard (`/admin/learning/overview`)**
- Per provider and feature: success rate, cost per success, mean rating, **rating coverage** (selection-bias monitor).
- **Drift alerts:** success-rate drop and rating drop compared with the previous window, once there are enough jobs.
- Learning-data eligibility counts.

### Tests

**API: 103/103 passed (5 new in `test_learning.py`)**
- Flag off records nothing.
- Completed, failed and cancelled events with the correct fields; no content leakage.
- Consent defaults, opt-out stops recording, append-only receipts, data deletion.
- Feedback: flag gate, owner-only, completed-only, validation, one per job.
- Roll-up (idempotent), overview, coverage, both drift alerts, staff-only access.
- Account deletion removes learning data.

**Other checks**
- `ruff` clean; `alembic check` shows no drift.

### Known limitations

- No mobile UI yet for the rating prompt or the learning-consent settings; the API is ready.
- There is no automated quality metric beyond the existing QA numbers (lip-sync, seam). Identity drift, flicker and adherence scorers are Phase C/D.
- Drift detection uses simple thresholds; statistical tests come in Phase D.

### Next step

Phase C:
- immutable intent and original prompt, with a separately versioned optimized prompt;
- the three creative modes (Faithful, Balanced, Experimental);
- candidate plans;
- variation seeds;
- similarity audits (with template reuse exempt);
- mobile rating and consent UI.

## Phase C: intent, prompt optimizer, creative modes, candidates, variations, similarity audits

Migration: `0012_creative_intelligence` (additive, reversible): `prompt_strategies`, `similarity_audits`, `studio_project_versions.creative`.

### What works

**Immutable intent**
- Stored on every planned version (`creative.intent`):
  - a hash of the original brief;
  - genre;
  - **quoted phrases as hard requirements**;
  - constraints (duration, aspect ratio, characters);
  - the intent-precedence order.
- The brief itself is stored unchanged.

**Original vs optimized prompt**
- Every shot keeps the user's or director's `prompt`. The `optimized_prompt` is *derived* from (prompt, mode, strategy `pv1`, composition, seed) and re-derived on every validation, so it is never stale and never overwrites the original.
- The strategy library contains production technique only: composition, camera, lighting and pacing. It is registered with a config hash in `prompt_strategies`.
- Storyboards without a creative mode (legacy and manual) are untouched, and their render hashes are unchanged. This is tested.
- The worker sends the optimized prompt to the provider.

**Creative modes**
- **Faithful:** one candidate; the optimized prompt equals the original.
- **Balanced** (default): two candidates, technique hints added.
- **Experimental:** three candidates, wider visual variation.
- The user picks the mode in the Studio tab.

**Candidate plans**
- Each candidate gets a distinct composition and seed.
- Each is scored on: adherence (required phrases, target duration), feasibility (routed provider and capabilities), safety, cost against the budget, and novelty (1 − maximum similarity).
- The best one is kept. All candidate scores are stored with the version.

**Variations: `POST /studio/projects/{id}/variations`**
- Same intent, new seeds **and** compositions not used before (randomness alone doesn't count as variation).
- The current version is not changed; the variations are offered as new versions.

**Similarity audits**
- Method: character-trigram Jaccard.
- Compared against **only** the same user's other projects and the public licensed template catalog. No other user's content is ever read.
- Thresholds are category-aware and configurable (`creative.thresholds`).
- A near-duplicate produces a **warning plus a suggestion** (a variation or Experimental mode), never a rejection.
- Every check is recorded in `similarity_audits`.
- Template remixes are intentional reuse: they are never optimized or audited.

**Diversity dashboard:** `GET /admin/learning/overview` now includes, per genre, the composition distribution and its entropy, plus the near-duplicate rate.

**Mobile**
- Creative mode chips in the Studio tab.
- Variations button in the project screen.
- Optional 1–5 star rating on finished videos.
- Learning settings in the profile: three consents and "delete my learning data".

### V5 acceptance tests covered (`test_creative.py`, 6 tests)

- **Same broad prompt planned 100 times** (balanced and experimental):
  - ≥ 4 different compositions, none above 50 %, entropy ≥ 1.5 bits;
  - ≥ 4 distinct camera sequences;
  - the intent is preserved every time (same sentences, required phrase, exact duration).
- **A viral template never feeds original plans:** a popular template's text never appears in an unrelated plan.
- **Faithful mode adds nothing; template reuse is exempt from novelty penalties.**
- **No cross-user comparison:** another user's identical plan is "ok"; the same user's repeat and a catalog match are warned.
- Original vs optimized prompt, re-derivation after an edit, legacy hashes stable, variations distinct, dashboard, worker prompt.

### Tests

- **API: 109/109 passed.**
- **Worker:** 20/20.
- **Mobile:** `tsc` passes, 6/6.
- `alembic check` shows no drift.

### Known limitations

- Similarity is a lexical method (character trigrams). Semantic (embedding-based) and perceptual (output frame) similarity need an embedding provider and calibration on labelled examples; the thresholds are configurable for that.
- Candidate scoring uses heuristic weights. There is no blind human study of creativity yet; the benchmark harness supports blind review.
- The Gemini director produces a single candidate per call. Several candidates would cost several calls; this stays configurable for later.

### Next step

Phase D: constrained bandit for technical routing, experiment assignments, and versioned learning policies (shadow → A/B → monitored expansion, with automatic rollback).
