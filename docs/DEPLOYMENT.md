# Deployment

## Environments
`dev` (docker compose), `staging`, `production` — separate cloud projects/accounts, separate buckets, separate
store/billing sandboxes. Production changes only via CI (reviewed PR → image build → migrate → rollout).

## Release steps
1. CI green on `main` (lint, migrations reversible, tests, image scan, mobile typecheck).
2. `alembic upgrade head` as a one-off job (never on replica start). Migrations must be backward compatible
   with the previous API version (expand → migrate → contract).
3. Roll API replicas (≥2, across 2 AZs). Health: `/healthz` (liveness), `/readyz` (DB).
4. Roll GPU workers gradually; they finish the current job on SIGTERM.

## Secrets
All `APP_*` secrets come from the cloud secrets manager (KMS-encrypted) and are injected as env vars;
never in the repo, CI logs or the mobile bundle. `APP_ENV=production` refuses to start without
`APP_JWT_SECRET` (≥32 chars) and `APP_WORKER_TOKENS`.

## GPU workers
See [MODEL_SETUP.md](MODEL_SETUP.md). Each worker: `API_URL`, `WORKER_TOKEN`, `WORKER_MODELS`,
`GPU_PROVIDER`, `GPU_TYPE`, `GPU_PRICE_PER_HOUR`, `OUTPUT_SCORER`, `MP_SAFETY_CLASSIFIER`, model paths.
Production workers refuse mock adapters and fail closed if the safety classifier is not configured.

## Periodic jobs
- `POST /internal/worker/reap` every minute (lease reaper; also runs on every claim).
- Daily ledger/store reconciliation (Phase 4), orphan storage cleanup, backup restore test (Phase 6).
