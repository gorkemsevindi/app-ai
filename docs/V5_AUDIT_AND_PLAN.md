# Master Spec V5 (Self-Learning & Creative Intelligence Engine): Phase A audit, gap report and plan

Date: 2026-10-10. Scope: AI Video App only (YourStars `drama/` excluded). V5 extends V4; nothing in V4 is replaced.

## 1. What already exists that V5 builds on

| V5 component | Existing code | Status |
|---|---|---|
| Intent parser | Studio `Brief` (structured input), template remix request schemas | **Partial**: structured fields only; no free-text intent parsing, no immutable intent record |
| Prompt optimizer | `compile_prompt` (templates), worker `compose_prompt` (studio) | **Missing**: no versioned prompt strategies; the original prompt is kept in the version, but no separate optimized prompt |
| Creative planner | Studio director (rule-based, labelled; Gemini behind key) | **Partial**: a single plan, no candidate plans, no creative modes |
| Technical memory | `model_runs` (cost, GPU seconds, QA metrics, errors), router health, `economics` | **Partial**: raw per-attempt data, no aggregates, no error taxonomy, no parameter outcomes |
| Consent-aware preference memory | Likeness consents (studio, actors), account deletion | **Missing** for learning: no opt-in/opt-out for telemetry, training or personalization |
| Model performance registry | `router.registry` + benchmark `provider_quality` | **Partial**: quality from blind review only, no segment breakdown |
| Adaptive router | `router.choose` (rule-based eligibility, cheapest / best) | **Partial**: no exploration (bandits), no shadow mode, no rollout limits |
| Novelty evaluator | none | **Missing** |
| Quality evaluator | lip-sync QC, seam score, output moderation, blind benchmark review | **Partial**: no identity drift, flicker or instruction-adherence scoring |
| Feedback collector | reports (safety), analytics events (product) | **Missing**: no quality rating, no regeneration reasons |
| Offline trainer/evaluator | benchmark harness (sets, runs, blind review) | **Partial**: evaluation only; no policy training / candidate artifacts |
| Experiment registry | `experiments` table (initial schema) | **Unused**: no assignment, no analysis |
| Policy gate | feature flags, kill switches, admin RBAC | **Partial**: no versioned learning policies, no shadow → A/B → rollback pipeline |

Existing guarantees that stay binding:
- Ledger/money invariants.
- Consent checks at use, dispatch and publication.
- Template rights.
- Feature flags.
- No unverified provider claims.

## 2. Gap analysis against V5 sections

| V5 section | Gap |
|---|---|
| §1 Non-memorization contract | No intent-precedence rules in code; no near-duplicate detection; viral templates can influence nothing yet. Nothing copies content today, but nothing measures it either. |
| §2 Separation of concerns | Services listed above are missing or partial; there is no learning event schema. |
| §3 Modes / original vs optimized prompt | No modes; no optimized-prompt versioning; no multi-candidate plans; variations reuse the same seed path. |
| §4 Anti-memorization / diversity | No similarity or diversity metrics; no bandit routing. |
| §5 Feedback & governance | No per-job learning record, no rating, no learning consent, no retention deadline on telemetry, no training-eligibility flag. |
| §6 Lifecycle | No offline loop, no candidate registry, no shadow/A-B/rollback. |
| §7 Quality gates | Benchmarks exist; missing automated metrics, segment regression and launch thresholds. |
| §8 Data model / APIs / admin | None of the 11 proposed tables exist; no learning dashboard. |
| §9 Acceptance tests | None of the V5 acceptance tests exist. |

## 3. Data-governance decisions (defaults, changeable by config/legal review)

| Purpose | Default | Rationale |
|---|---|---|
| `technical_improvement`: aggregate, content-free job telemetry (model, latency, cost, success, QA numbers, ratings) | **On, user can opt out** | V5 §5 "aggregate/limited telemetry by default"; no prompts, media or face data |
| `content_training`: retaining prompts, uploads or outputs for training | **Off (opt-in)**, and also blocked unless provider terms allow | V5 §5; also needs legal review |
| `personalization`: per-user creative preference memory | **Off (opt-in)** | Never shared across users |

Further rules:
- Every learning record carries: schema version, provenance, purpose/consent scope, retention deadline, and a job link.
- Account deletion or "delete my learning data" removes the user's learning records and preferences.
- Aggregates keep only counts, never identities.

## 4. Phased plan (each phase behind the `learning` flag, additive migrations, tested and reported)

**Phase B: instrumentation, consent, technical memory, evaluation, dashboard** (no behaviour change for users)
- Migration `0011_learning_foundation`: `consent_records` (append-only), `learning_events`, `model_performance_aggregates`.
- One content-free outcome event per finished job (completed, failed, cancelled), written in the same transaction as the job's terminal state. Contents: feature/intent category, creative mode, provider/model, prompt-strategy version, duration/resolution, success, retries, latency, estimated vs actual cost, QA signals and consent snapshot.
- User quality feedback: rating 1–5 plus reason tags, owner-only, completed jobs only, one per job (duplicates update the same record), rate limited. Feedback is stored separately from popularity signals.
- Consent API: read/update learning consent (versioned receipts), "delete my learning data", account-deletion hook.
- Scheduled technical-memory roll-up (`learning_aggregate`): daily per provider × feature × mode, only from consented events. Contents: success rate, latency p50/p95, cost per success, mean rating, rating coverage (selection-bias monitor), QA means, error taxonomy.
- Admin learning dashboard: quality by cohort, eligibility counts, drift alerts (success-rate or rating drop vs the previous window), feedback coverage.

**Phase C: prompt optimizer, creative modes, novelty, variations**
- Immutable intent + original prompt; separately versioned optimized prompt (`prompt_strategies`).
- Modes: Faithful / Balanced (default) / Experimental, user-switchable.
- Candidate plans scored on adherence, feasibility, safety, cost and novelty.
- Distinct seeds and composition strategies for variations.
- Similarity audits (`similarity_audits`): category-aware, calibrated thresholds; never reject on one score.
- Template reuse exempted.

**Phase D: adaptive routing and experiments**
- Constrained bandit for *technical* routing only, with a budget, a safe baseline, quality gates and rollout limits. Never explore by raising user prices.
- Experiment assignments (server-side).
- Learning policy versions: shadow → limited A/B → monitored expansion, with automatic rollback on regression (quality, novelty, privacy, cost, reliability).

**Phase E (optional): fine-tuning**
- Only after a demonstrated benefit and governance review, on licensed open-weight models with rights-cleared data and model cards.

## 5. Risks and assumptions

- **Content-training consent needs legal review.** Until then the code keeps it off and blocks it.
- **Cold start:** real signals only appear with real users and providers. Until then aggregates are computed from tests and benchmarks.
- **Feedback poisoning:** mitigated by owner-only, completed-only, one-per-job feedback, rate limits and distinct-user counting. More advanced anomaly detection is a Phase D item.
- **Model training:** no foundation-model weights are retrained per request (V5 §6). Any learned artifact is a versioned policy that goes through the gate.
