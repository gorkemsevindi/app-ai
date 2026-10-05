# AI Video App (working title)

“Put yourself in what's trending.” Users create a reusable **face profile** once, then:

- **Templates**: tap a trending template and get a 5–10 s vertical (9:16) AI video starring them.
- **Your video (multi-person)**: upload a clip they own, pick Person 1..4, assign face profiles, and the
  people are replaced while motion, camera, scene and lighting are preserved (see `docs/MULTI_PERSON.md`).

Self-hosted AI first (DreamID-V, Wan2.2 family), provider-agnostic adapters, credits + subscriptions.

> Status: **Phase 0–1 complete, Phase 2 vertical slice + multi-person feature implemented and tested
> without a GPU** (mock models, real tracking/compositing/encode). Real-model validation requires a GPU
> node — see [Known limitations](docs/KNOWN_LIMITATIONS.md).

## Repository layout

```
apps/mobile/          Expo (React Native) iOS/Android app — expo-router, i18n (en, tr), dark/light
apps/admin/           (Phase 3) Next.js admin panel — admin API already available under /admin
services/api/         FastAPI public + admin + internal-worker API, PostgreSQL, Alembic migrations
services/worker/      GPU worker: model adapters, multi-person tracking/compositing, encode, QA
services/trend/       (V2) Trend Engine — interface only
infra/docker/         Dockerfiles (api, worker-cpu, worker-gpu)
docs/                 Architecture, research, security, multi-person design, ADRs
```

## Quick start (local, no GPU)

```bash
make up        # postgres, redis, minio, api (migrations run automatically), CPU worker with mock models
make seed      # 10 original demo templates, default feature flags, dev admin user
open http://localhost:8000/docs
cd apps/mobile && npm install && npx expo start
```

Without Docker (what CI does):

```bash
python -m venv .venv && . .venv/bin/activate
pip install fastapi "uvicorn[standard]" "sqlalchemy>=2" "psycopg[binary]" alembic pydantic-settings \
  "pyjwt[crypto]" boto3 redis httpx python-multipart email-validator pytest ruff numpy opencv-python-headless
cd services/api && APP_DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app alembic upgrade head
uvicorn app.main:app --reload
```

Enable the multi-person feature for local testing (admin token required):

```bash
curl -X PUT localhost:8000/admin/flags/multi_person -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
  -d '{"enabled": true, "public": true, "value": {"analysis_model": "mock_mp_analyzer", "preferred_model": "mock_mp"}}'
```

## Tests

```bash
make lint && make test                 # API (35) + worker (6) tests, incl. e2e with real ffmpeg encode
cd apps/mobile && npm run typecheck && npm test
```

See [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Documentation

| Doc | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, data flow, job state machine, decisions |
| [MULTI_PERSON.md](docs/MULTI_PERSON.md) | multi-person replacement pipeline, GPU estimates, safety |
| [research.md](docs/research.md), [research-multiperson.md](docs/research-multiperson.md) | OSS + licence research |
| [SECURITY.md](docs/SECURITY.md) | threat model, consent, moderation, retention, deletion |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | environments, secrets, releases, GPU workers |
| [LICENSES.md](docs/LICENSES.md) | dependency/model licence inventory |
| [adr/](docs/adr) | decision log |
| [KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | what's not done yet + V2 backlog |

Secrets are never committed: copy `.env.example` → `.env` for local dev only.
