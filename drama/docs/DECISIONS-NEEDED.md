# Owner decisions and missing credentials

Nothing below has been assumed as approved. Defaults in code are placeholders taken from the spec, and
each one is an environment variable or a per-series setting.

## Commercial decisions

| # | Decision | Current default | My recommendation |
|---|---|---|---|
| 1 | Store product model | Per-episode / bundle / season products (sandbox) | **Coins** (IAP consumables) + subscription. Apple/Google need pre-registered products |
| 2 | Creator revenue share | 60 % of net (`DRAMA_CREATOR_SHARE_BPS=6000`) | Keep 60/40. Higher platform share for funded Originals until costs are recouped |
| 3 | Price points | $0.99 episode, $3.99 five-pack, $9.99 season, $7.99/mo subscription, 5 free episodes | A/B test (experiments table exists). Localise TRY prices |
| 4 | Subscription pool split | Parked in `liability:subscription_pool` | 60 % of net subscription revenue, split by qualified watch time |
| 5 | Earnings hold | 30 days | 30 days, plus clawback (already implemented) |
| 6 | Minimum payout | $50 | $50 (TRY equivalent) |
| 7 | Credit pricing | 1 credit = $0.01 of provider cost, plus a platform render fee | Sell credits at 1.3–1.5× provider list cost. Credit packs: 500 / 1200 / 3000 |
| 8 | Free trial credits | 1,000 credits | 1,000 credits (about 10 preview episodes locally, about 1 budget-route episode) |
| 9 | Originals programme | — | Fund 10–20 creators for the first 3–6 months |
| 10 | Real-likeness / face swap at launch | Consent flow on, provider not wired | Keep it off at launch |
| 11 | Legal entity for payments and payouts | — | TR company for iyzico/PayTR, plus a US/EU entity for Stripe Connect payouts |
| 12 | Brand | "Sahne" (working name) | Trademark search in TR/EU/US first |
| 13 | Default video route | Local preview | Budget route (Veo 3.1 Lite + ElevenLabs + LatentSync) for previews; mid route (Veo 3.1 Fast) for finals |

## Accounts and API keys to open

Nothing is mocked silently. Each item below changes a specific status in `/admin/providers`.

| Service | Unlocks | Env / config |
|---|---|---|
| Anthropic Console | Real script writing (Claude Opus 5.5) | `ANTHROPIC_API_KEY`, `DRAMA_LLM_PROVIDER=anthropic` |
| Google Cloud + Vertex AI billing | Veo 3.1 video, Nano Banana reference images, Lyria music, Chirp TTS | Service account (adapter to build) |
| ElevenLabs (API plan) | Turkish/English character voices, PVC voice cloning with consent | `ELEVENLABS_API_KEY` (adapter to build) |
| sync.so | Lip-sync on generated video | `SYNC_API_KEY` |
| RunPod or Modal | Self-hosted LatentSync / WhisperX | API key |
| Deepgram or AssemblyAI | ASR for uploaded or dubbed audio | API key |
| Cloudflare (R2 + Stream) | Production storage and CDN/HLS | Account + tokens |
| Apple Developer + App Store Connect | iOS app, IAP, Server Notifications v2 | Issuer ID, Key ID, .p8 |
| Google Play Console | Android, Play Billing, RTDN | Service account + Pub/Sub |
| Stripe (+ Connect) via a US/EU entity | Web payments, creator payouts | `STRIPE_SECRET_KEY`, webhook secret |
| iyzico or PayTR | TRY web payments | Merchant keys |
| KYC vendor (Stripe Identity / Sumsub) | Creator verification | API key |
| Sentry, PostHog | Observability, product analytics | DSN / key |
| Separate GitHub repo + cloud org for this product | Independence (ADR-0001) | Owner action |
