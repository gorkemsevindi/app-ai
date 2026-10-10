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
