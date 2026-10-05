# Architecture

```
Mobile (Expo) ──HTTPS──> CDN/WAF ──> API pool (FastAPI, stateless) ──> PostgreSQL (source of truth)
     │                                   │  │                          ├─ credit ledger (append-only)
     │  presigned POST/GET               │  └──> Redis (rate limits only; loss-tolerant)
     └──────────────> Object storage <───┘
                         ▲  presigned PUT/GET per job
                         │
   GPU workers (any provider) ──HTTPS (worker token)──> /internal/worker/{claim,heartbeat,complete,fail}
```

## Key decisions (details in `docs/adr/`)

| # | Decision | Why |
|---|---|---|
| 0001 | Monorepo, FastAPI + SQLAlchemy 2 + Alembic, Expo/expo-router | spec §3; typed OpenAPI; one CI |
| 0002 | **Durable job queue in PostgreSQL** (`FOR UPDATE SKIP LOCKED`, leases, heartbeats) instead of Celery/Redis | jobs + credits commit atomically; Redis loss can't lose a paid job (spec §22/§25); weighted classes + leases are ~100 lines |
| 0003 | Workers talk to the API over HTTPS, never to the DB | rented GPUs (RunPod/Lambda) never hold DB or long-lived storage credentials; provider-agnostic |
| 0004 | Model routing snapshot on each job (`preferred_model`, `fallback_model`) + kill-switch flags `model_disabled:<name>` | template and multi-person jobs share one router; fallback after 120 s wait or when preferred is disabled |
| 0005 | Primary models: DreamID-V (face swap on template clips), Wan2.2 TI2V-5B (prompt templates), Wan2.2-Animate-14B (fallback) | licence + cost (docs/research.md §6); InsightFace weights banned (non-commercial) |
| 0006 | Upstream model repos run as subprocesses in their own venv | licence + dependency isolation, hard kill on cancel/timeout, VRAM freed between jobs |
| 0007 | Multi-person = track → per-person sequential masked passes → occlusion-aware composite → QA | no permissive model does 4-person replacement in one pass; cost linear in persons (docs/MULTI_PERSON.md) |
| 0008 | Credits: append-only ledger, DB trigger forbids UPDATE/DELETE, per-user row lock, idempotency keys | no double spend; auditable (spec §27) |

## Job state machine

```
queued → preprocessing → generating → postprocessing → moderation → completed
   │            │              │              │              └→ failed (output blocked → refund)
   └→ cancelled └──────────────┴──────────────┴→ queued (retry: lease lost / retryable error, same debit)
                                                └→ failed (attempts exhausted → automatic refund)
```

- One debit per job (`gen:{job_id}`), one refund (`refund:{job_id}`); retries are free.
- A worker whose lease expired gets `409 lease_lost` on heartbeat/complete and must drop the work.
- Cancel: queued → immediate refund; running → `cancel_requested`, seen on the next heartbeat.

## Job kinds

| kind | created by | credits | model source |
|---|---|---|---|
| `template` | `POST /generations` | template cost | template version |
| `analysis` | `POST /source-videos/{id}/complete` | 0 | `multi_person.analysis_model` |
| `multi_replace` | `POST /generations/multi` | quote (persons × seconds × resolution) | `multi_person.preferred/fallback_model` |

## Queue classes

`paid_high` (Pro, and analysis jobs), `paid_normal`, `free`; weighted random order 6:3:1 per claim, so
free jobs are never starved. Cost guard: above the daily GPU budget only paid classes are served; above
1.5× nothing is claimed (jobs wait visibly as queued).

## Trend Engine (V2)

`services/trend/` will implement `TrendSource.fetch() -> [TrendSignal]` and `rank(signals) -> [TemplateSuggestion]`.
The MVP does not depend on it: templates are curated in the admin API.
