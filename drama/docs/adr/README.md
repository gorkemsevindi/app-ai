# Architecture decision records

| # | Decision | Status |
|---|---|---|
| 0001 | **Independent product.** All code lives under `drama/`: no imports from `apps/` or `services/` (the earlier AI Video App). It should move to its own repository and cloud accounts once the owner creates them. | Accepted (repo move pending owner) |
| 0002 | **Modular monolith (FastAPI) plus separately scaled workers.** Postgres is the queue (leases + `SKIP LOCKED`) until load needs Redis or SQS. | Accepted |
| 0003 | **Scripts are versioned JSON documents per episode (`script_versions`); scenes and shots are derived.** This avoids a row-per-shot schema while the editor is still script-centric. Normalise when the timeline editor needs shot-level locking. | Accepted |
| 0004 | **Providers sit behind adapters, with a price table and a status registry.** An unconfigured provider raises `provider.unavailable`, so nothing silently falls back to a mock. Local providers are labelled (`is_mock` / `mock_components` in provenance). | Accepted |
| 0005 | **The local preview engine is a deterministic 2D performer, not a video model.** It exercises lip-sync, expressions, captions, QC, packaging and monetisation end to end without keys or GPUs. Premium routes replace only the performance and voice steps. | Accepted |
| 0006 | **Append-only double-entry ledger in the main database.** Credits are a currency (`CRD`), and revenue share is applied to net, never gross. | Accepted |
| 0007 | **Web is Next.js (App Router) with an API-token client; mobile is Expo (React Native).** Mobile reuses the API and uses native IAP. | Accepted (mobile slice next) |
| 0008 | **Coins model for store purchases.** | Proposed: needs owner approval (see review §A1) |
| 0009 | **Real-person likeness / face swap is off at launch.** The consent machinery is kept and tested. | Proposed: needs owner approval |
