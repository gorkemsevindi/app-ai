# Feasibility, scope matrix and unit economics

## 1. Feasibility, API and licence verification (spec §12 item 1)

Status legend:
- **Works**: running and tested in this repo.
- **Adapter ready**: the code path exists but needs a key or contract.
- **Planned**: not started.

Pricing sources and verification grades (V/S/3P) are in [02-PROVIDERS-AND-COSTS.md](02-PROVIDERS-AND-COSTS.md).

| Capability | Local / dev route (works today) | Production candidates | Official commercial API? | Licence / terms notes | Status |
|---|---|---|---|---|---|
| Story bible + scripts | `local_template` beat-bank writer (not an LLM; labelled mock) | Claude Opus 5.5 / Sonnet 5.5 / Haiku 5.5, Gemini 3.x | Yes (V) | Output owned by the customer under API terms | Local: works. Anthropic: adapter ready, needs `ANTHROPIC_API_KEY` |
| Character reference images | Vector portrait from Character DNA, 7-expression reference sheet | Nano Banana 2 / Pro (multi-reference), FLUX Kontext, gpt-image | Yes (V/S) | Commercial use granted under API terms | Local: works. Providers: planned |
| Video (t2v / i2v / reference) | `studio_preview_local` 2D performance renderer | Veo 3.1 (V), Kling 3/O3, Runway Gen-4.5, MiniMax H3 (3P) | Veo yes. Sora reportedly shut down | Wan ≥2.5 is API-only; Wan 2.2 Apache-2.0 for self-hosting | Local: works (labelled). Providers: planned |
| Lip-sync / facial performance | Phoneme visemes + audio-energy jaw, blended expressions (smile, cry, anger, fear, surprise, tender), brows, blink, gaze, head motion | sync.so lipsync-2-pro, Runway Act-Two, LatentSync self-host | Yes (S) | LatentSync Apache-2.0. MuseTalk weights and Hallo3 need a licence check | Local: works. Providers: planned |
| TTS (TR/EN) | eSpeak NG (GPL-3.0 binary, run as a separate process) | ElevenLabs v3/v4, Google Chirp 3 HD, Azure | Yes | Voice cloning needs a verified consent captcha (ElevenLabs PVC) | Local: works. ElevenLabs: needs key + adapter |
| Word timing / forced alignment | Energy-segmented phrase alignment from TTS audio; subtitle drift checked in QC | Deepgram, AssemblyAI, WhisperX (+ Turkish wav2vec2) | Yes | WhisperX BSD-2 | Works |
| Music | Procedural score engine (original by construction) | Lyria 3 (V), ElevenLabs Music | Yes. Suno/Udio have no public API, avoid | Lyria under Google API terms | Works |
| Mix / loudness | ffmpeg sidechain ducking, EBU R128 −14 LUFS | same | n/a | ffmpeg LGPL/GPL build: check the distribution build flags | Works |
| Subtitles | SRT, VTT, ASS karaoke, speaker colours, safe zone, burn-in | same + translation via LLM | n/a | Uses the DejaVu font (free licence) | Works (translation planned) |
| Streaming | ffmpeg → 2-rendition HLS, HMAC-signed playlists and segments | Cloudflare Stream / Mux + R2 | Yes | — | Works locally. CDN planned |
| Payments | Signed sandbox store (receipt + server notifications) | Apple StoreKit 2 / App Store Server API, Play Billing + RTDN, Stripe (web), iyzico/PayTR (TRY) | Yes | Guideline 3.1.1 requires IAP for digital unlocks; creator tips effectively need IAP | Sandbox: works. Stores: need accounts |
| Payouts | Ledger + sandbox rail with admin settle | Stripe Connect (US/EU entity), Payoneer/Wise | Yes | Stripe does not onboard TR entities | Sandbox: works |
| KYC | Admin manual flag | Stripe Identity / Sumsub / Veriff | Yes | KVKK/GDPR processor agreement | Planned |
| Face swap / real likeness | Consent grant + human review + revocation propagation; provider call blocked | (none chosen) | — | KVKK special-category biometric data | Consent flow: works. Provider: deliberately not wired |

## 2. Scope matrix: MVP vs later (spec §12 item 2)

| Area | In this repo now | MVP (public beta) | Later |
|---|---|---|---|
| Studio wizard → bible → scripts | ✅ (template writer; Claude adapter) | Claude writer live, per-scene regeneration | Beat-sheet editor, storyboard frames |
| Character DNA, lock, versioning, expression library | ✅ | Reference packs via Nano Banana | LoRA per character, wardrobe continuity checks |
| Voice per character, emotion prosody | ✅ (eSpeak) | ElevenLabs voice ids | Speech-to-speech, singing |
| Lip-sync, facial expressions | ✅ (2D performer) | sync.so / LatentSync on generated video | Full-body gesture |
| Music, ambience, ducking, loudness | ✅ | Lyria for premium | Beat-aligned music lip-sync |
| Captions: karaoke, speaker colours, SRT/VTT | ✅ | Translation + RTL | Caption style presets |
| Timeline editor | Script versioning + re-render + TTS cache | Shot reorder, trim, B-roll | Full multitrack NLE |
| Job system: idempotent, resumable, spend caps, refunds | ✅ | Redis/queue scaling, GPU pool | Priority tiers |
| QC gate + manual publish review | ✅ | Identity-similarity model (face embeddings) | Automated uncanny/flicker detection |
| Viewer feed, series, player, resume, history, follows, comments | ✅ web | Mobile app (Expo) | ML ranking, push notifications |
| Paywall (first 5 free), unlocks, entitlements, refunds | ✅ sandbox | **Coins** + Apple/Google IAP + Stripe web | Rewarded ads, sponsorships |
| Ledger, 60/40 net allocation, hold, payouts | ✅ | Real payout rail + KYC vendor | Multi-currency FX, tax engine |
| Rights / consent / takedown / reports | ✅ | DMCA-style notice form, appeals UI | C2PA signing, content-ID |
| Distribution (OG pages, deep links) | ✅ OG/canonical | Referral links | YouTube/TikTok export via official APIs |
| Admin | ✅ web admin | Role scopes (moderator, finance) | Case SLAs |

## 3. Unit economics (spec §12 item 8). Assumptions are labelled and every number is configurable.

### Production cost per 60 s episode (from 02-PROVIDERS-AND-COSTS.md)

| Route | Cost per episode | 30-episode season |
|---|---|---|
| Local preview (this repo) | ≈ $0.01 CPU | ≈ $0.30 |
| Budget (Veo 3.1 Lite + ElevenLabs Flash + LatentSync) | ≈ $7.20 | ≈ $216 |
| Mid (Veo 3.1 Fast + audio) | ≈ $30 | ≈ $900 |
| Premium (Veo 3.1 1080p + Opus + sync pro) | ≈ $92 | ≈ $2,770 |

### Revenue per unlock (example: $0.99 episode, Apple Small Business 15 %, tax-inclusive price, 0 % indirect tax set for the example)

| Item | Amount |
|---|---|
| Gross | $0.99 |
| Store fee 15 % | $0.15 |
| **Net distributable** | **$0.84** |
| Creator 60 % | $0.50 |
| Platform 40 % | $0.34 |

**Break-even on the budget route:** $7.20 ÷ $0.50 ≈ **15 paid unlocks per episode** for the creator.
With 5 free episodes and a typical 5–10 % paywall conversion, a creator needs about 150–300 viewers
reaching episode 6 per paid episode.

On the mid route, about 60 unlocks per episode. This is why we recommend the Originals programme
(review §A5) and credit pricing with a margin: the platform should sell credits at about 1.3–1.5×
provider list cost. **[DECISION]**

### Cloud bill estimate, MVP (monthly, before generation costs, which are passed through as credits)

| Item | Estimate |
|---|---|
| API + web (2 small containers, e.g. Fly.io/Render/ECS) | $40–100 |
| Managed Postgres (small HA) + backups | $50–120 |
| Redis (rate limits, queue) | $15–30 |
| R2 storage 1 TB | $15 |
| Cloudflare Stream: 100k min delivered + 5k min stored | $100 + $25 |
| CPU render workers (local route / ffmpeg assembly), 2 × 4 vCPU | $80–150 |
| GPU (LatentSync self-host, on demand) | usage-based, ~$0.40 per episode |
| Sentry + PostHog | free tier to $50 |
| **Total** | **≈ $350–600 / month** + generation pass-through |
