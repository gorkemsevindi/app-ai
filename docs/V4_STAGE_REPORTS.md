# Master Spec V4: stage reports

Decisions confirmed by the owner on 2026-10-10:

- Use the V4 rollout order. The creator marketplace moves to Stage D.
- YourStars (`drama/`) stays a separate product. AI Studio is written fresh inside this app; no code is copied from YourStars.
- Work starts with Stage A.

Earlier V3 work (template remix, multi-person replacement, lip-sync) is described in `docs/V3_PHASE_REPORTS.md`.

## Stage A: production-hardening of the V3 flow (credits, store billing, sharing, economics)

Every part is behind a feature flag and uses additive migrations. The `alembic` downgrade to base and upgrade back to head runs at the start of every test session.

| Part | Feature flag | Migration | Status |
|---|---|---|---|
| A1: credit buckets, reserve / settle / release | always on (balances unchanged) | `0005_credit_buckets` | production-ready (tested) |
| A2: App Store / Play verification, store notifications | `billing` (off) | none (tables already existed) | implemented and tested against the official library and a mocked Play API; needs store credentials |
| A3: signed share links, deep links, attribution, template reports | `sharing`, `referrals` (off), `moderation` | `0006_share_links` | implemented and tested; universal-link domain still needed |
| A4: per-job telemetry, economics, job lineage, manual refund | `economics` (config only) | none | implemented and tested |

### A1: credit buckets

**How it works**
- Every positive ledger entry opens an immutable `credit_lot` in one bucket: promo, subscription, purchased, reward, adjustment or legacy. Each lot has its own optional expiry.
- Every debit records which lots it used, in `credit_allocations`.
- A lot's remaining amount is computed from append-only rows. No mutable counter is ever stored. A database trigger blocks UPDATE and DELETE.
- Spending order:
  1. lots that expire soonest first;
  2. then the bucket order from `credits.consume_order`, default promo → reward → subscription → adjustment → legacy → purchased.
- Expired lots are swept with an `expire` entry on the user's next credit write. Expired credits are never spendable, even before the sweep runs.

**How generation jobs map to the ledger**
- `generation_debit` is the reservation, taken before dispatch.
- `generation_settle` (0 delta) confirms a billable completion.
- `refund` is the release, and returns the credits to the exact lots they came from.
- `generation_jobs.billing_state` tracks this as `reserved`, `settled` or `released`.

**Migration and API**
- Existing balances become one `legacy` lot per user, so no balance changes.
- `GET /credits` gains `buckets` (remaining amount and next expiry). `balance` is now the spendable balance.
- Also fixed: retrying a clamped purchase reversal used to return 409 by mistake. It is now idempotent.

### A2: store billing

**Purchase verification: `POST /purchases/verify`**
- iOS: StoreKit 2 signed transactions are verified with **Apple's official App Store Server Library** (MIT licence):
  - the x5c chain to the configured Apple root certificates;
  - Apple's leaf and intermediate certificate OIDs;
  - ES256 signature, bundle id and environment;
  - OCSP revocation checks in staging and production.
- Production is tried first, then sandbox, which App Review and TestFlight need. Sandbox purchases are labelled as such.
- Android: the Play Developer API (endpoints checked against Google's official discovery document) is called with service-account OAuth:
  - `products.get` for consumables, `subscriptionsv2.get` for subscriptions;
  - after the grant is committed, consumables are consumed and subscriptions acknowledged.
- Credits come only from the server-side catalog (`billing.value.products`). Store prices are never stored in our code.

**Idempotency and account binding**
- Grants are idempotent per store transaction.
- A transaction belongs to exactly one account, checked by:
  - the unique provider + transaction id;
  - `appAccountToken` (iOS) or `obfuscatedExternalAccountId` (Android).

**Subscriptions**
- Each period grants into a `subscription` lot that expires with that period.
- The user's plan is recalculated from the subscription state.
- A late notification can never move the period end backwards.

**Store notifications**
- `POST /webhooks/apple` takes App Store Server Notifications V2. The payload is verified before it is stored.
- `POST /webhooks/google` takes Play RTDN through a Pub/Sub push with a URL token. The notification only says what changed; the current state is always re-read from the Play API.
- Both go through the durable `webhook_events` inbox, which drops duplicates and replays. A notification that failed can be replayed with `POST /admin/webhooks/{id}/replay`.
- Refunds and revocations reverse the matching grant, starting from its own lot. The reversal is clamped so the balance never goes negative, and any shortfall is recorded.

### A3: sharing and attribution

**Share links**
- `POST /share-links` creates an HMAC-signed `code.sig` token. Links point at a template, never at a user's private output video.
- Links can be made for a template, or for the user's own completed job (which links to its template).
- `GET /share-links/{token}` resolves a link and records a click. Repeated clicks from the same IP hash within an hour count once. The raw IP is never stored.

**Attribution: `POST /share-links/{token}/attribute`**
- First touch wins, at most one per user.
- New users only (default: account created within 72 hours).
- The click must be within a window (default 7 days).
- Self-referral is ignored.
- The rules in force are copied onto the attribution row. No money moves here; earnings come in Stage D.

**Web landing and app links**
- `/t/{token}` serves an escaped page with OG tags and a strict CSP. Its Play Store link carries the install referrer.
- `/.well-known/apple-app-site-association` and `/.well-known/assetlinks.json` are served from configuration.

**Template reports: `POST /templates/{id}/report`**
- A template moves to `review` (out of the feed and the app) after N distinct reporters (default 5).
- A `minor_safety` report moves it to review immediately.

**Mobile**
- New `t/[token]` deep-link screen.
- A link opened before sign-up is remembered in SecureStore and attributed once after sign-in.
- "Share template link" buttons on the template and video screens; "Report template" on the template screen.
- Turkish and English texts.
- Account deletion revokes the user's share links.

### A4: unit economics and support tools

- `GET /admin/economics?days=&group=feature|model|template` reports per group:
  - success rate;
  - credits settled and released;
  - paid credits;
  - cost (all attempts, including failed ones);
  - cost per success;
  - p50 and p95 time to result;
  - retries;
  - revenue and contribution margin.
- Revenue counts only settled credits that came from paid buckets. It is computed only when `economics.usd_per_paid_credit` (net of store fees) is set; otherwise revenue is reported as `null`.
- Alerts: low success rate, negative margin, cost per success above a cap, and GPU daily budget burn.
- `GET /admin/jobs` searches jobs; `GET /admin/jobs/{id}` shows lineage: attempts, costs, quality data, ledger events and output provenance.
- `POST /admin/jobs/{id}/refund` is a support refund of a delivered job. It returns credits to their original lots, exactly once, and is audited.

### Environment variables (new)

| Variable | Purpose |
|---|---|
| `APP_APPLE_ROOT_CERT_PATHS` | Apple root certificates (DER), downloaded from apple.com/certificateauthority |
| `APP_APPLE_APP_APPLE_ID` | Required to accept production App Store notifications |
| `APP_APPLE_ONLINE_CHECKS` | OCSP checks; on by default in staging and production |
| `APP_GOOGLE_SERVICE_ACCOUNT_FILE` | Play Developer API service account (path to a file from the secrets manager) |
| `APP_GOOGLE_RTDN_TOKEN` | Shared secret in the Pub/Sub push URL |
| `APP_SHARE_LINK_SECRET` | Optional; otherwise a key derived from `APP_JWT_SECRET` |
| `APP_SHARE_BASE_URL`, `APP_APP_SCHEME` | Public link origin; custom URL scheme |
| `APP_IOS_APP_IDS`, `APP_ANDROID_SHA256_FINGERPRINTS` | Universal links and App Links |
| `APP_APP_STORE_URL`, `APP_PLAY_STORE_URL` | Store buttons on the landing page |

Remote config (no code change needed):
- `billing.products`
- `credits.consume_order`
- `referrals` (`click_window_days`, `attribution_days`, `new_user_max_age_hours`, `click_dedupe_minutes`)
- `moderation.template_review_after_reporters`
- `economics` (`usd_per_paid_credit`, alert thresholds)

New dependency: `app-store-server-library` (MIT licence), added to CI and the API image.

### Tests

**API: 73/73 passed (26 new)**
- `test_credits_v4.py` (7):
  - buckets and expiry order;
  - release returns credits to the same lots;
  - settle is written once;
  - expired credits are never spent;
  - consume order comes from config;
  - purchase reversal, including retry of a clamped reversal;
  - append-only triggers;
  - legacy migration of balances and of in-flight jobs.
- `test_billing.py` (10):
  - real ES256 JWS and x5c test certificate chain (with Apple's OIDs) through the official library;
  - forged signature, unknown root and wrong bundle id are all rejected;
  - sandbox purchases are accepted and labelled;
  - idempotent grants and account binding;
  - full subscription lifecycle with duplicate and out-of-order notifications, refund and expiry;
  - replay of a notification that arrived before verify;
  - Play API flow: OAuth assertion, consume/acknowledge only after grant, pending purchases, voided purchases;
  - RTDN token check and duplicates;
  - fail-closed when billing is not configured.
- `test_sharing.py` (7): signature and forgery, idempotent links, job-share IDOR, click dedupe and IP hashing, takedown, attribution rules, landing-page escaping and CSP, AASA, report thresholds.
- `test_economics.py` (2): paid vs promo revenue, margin is `null` without a price, alerts, job lineage, manual refund exactly once and audited.

**Other checks**
- Worker 13/13 passed.
- Mobile `tsc` passes and node tests 5/5.
- `ruff` clean; `alembic check` shows no drift.

### Security and privacy

- The client can never choose how many credits it gets: grants come from the server catalog plus a store-verified transaction.
- A forged notification cannot change state:
  - Apple notifications must carry a valid signature chain;
  - Google notifications are only pointers, and the state is re-read from Play.
- Share tokens are HMAC-signed, so forged or guessed links fail without a database lookup.
- Referrers are never named by the client. IPs are stored only as keyed hashes. Users' generated videos are never exposed through links.
- Every admin money action (refund, webhook replay) is audited with the admin's identity.

### Cost

No GPU cost changes. A4 makes cost per success and margin visible for each feature, model and template.

### Known limitations

- **No real store integration yet.** The native purchase UI (StoreKit 2 / Play Billing module) is not in the mobile app; the paywall still says "coming soon". Verification has not been tested against real Apple or Google accounts, because no credentials exist yet.
- **Universal links / App Links need a domain.** A real domain, plus the `associatedDomains` and `intentFilters` settings in `app.json`, are required. Until then the custom `aivideo://` scheme works.
- **Install attribution is partial.** On iOS a share link survives a fresh install only if the user opens the link again after installing; no device fingerprinting is used, by design. On Android the Play Install Referrer carries the token through install, but the app still needs a native module to read it.
- **Single template jobs have no pre-dispatch USD estimate** (`est_cost_usd` is null). Multi-person and remix jobs have one.
- **Expired credits are swept lazily**, on the user's next credit write. There is no batch sweep job yet; expired credits are never spendable in the meantime.

### Next step

Stage B: AI Studio foundations.
- Project, version, storyboard, scene and shot models.
- Brief → storyboard → estimate → user confirmation → shot rendering, on the existing job queue and ledger.
- Subtitle and audio tracks.
- Character references with consent receipts.

Real video generation needs the `GEMINI_API_KEY` secret. Until then a mock provider is used and labelled as a mock.
