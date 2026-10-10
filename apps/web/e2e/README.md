# Web E2E (Playwright)

Runs against a **real local stack** — no mocks in the request path:

| Service | Port | Notes |
|---|---|---|
| PostgreSQL | 5433 | database `app_e2e`, `alembic upgrade head`, feature flag `editor` enabled |
| Redis | 6379 | rate limits |
| S3-compatible storage | 5000 | e.g. MinIO or `moto_server`; bucket `aivideo-private` with CORS for `http://localhost:3000` |
| FastAPI | 8000 | `APP_S3_ENDPOINT_URL=http://localhost:5000`, `APP_WORKER_TOKENS=[...]` |
| Worker | — | `WORKER_MODELS=editor_renderer` (ffmpeg) |

```bash
STORAGE_ORIGIN=http://localhost:5000 npm run build
npx playwright test          # starts :3000 (live) and :3001 (DEMO, no API) itself
```

Artifacts (screenshots, `export.mp4`, `export.png`, `metrics.json` with LCP and axe results) land in `test-results/`.
Playwright's Chromium has no H.264 decoder, so the in-browser preview is exercised with a VP9 WebM; MP4 upload and
H.264 export are verified separately (upload + ffprobe).
