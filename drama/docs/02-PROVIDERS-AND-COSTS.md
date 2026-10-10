# AI Short-Drama Platform: Provider Feasibility and Pricing (as of 2026-10-08)

**How these were checked:** Most vendor sites (openai.com, elevenlabs.io, fal, runway, kling, etc.) were blocked by this sandbox's egress proxy. **V** = I read the vendor's own page. **S** = taken from a web search that quotes the vendor's page or docs. **3P** = third-party aggregator or reseller only. "unverified" = sources conflict or I found none. Re-check every 3P and unverified row before you sign anything.

## 1. LLM (story bible, scripts, shot lists), USD per 1M tokens (input / output)

| Model | In / Out | Status | Source |
|---|---|---|---|
| Claude Opus 5.5 | $4 / $20 (Batch -50%) | V | https://claude.com/pricing |
| Claude Sonnet 5.5 | $2 / $10 | V | same |
| Claude Haiku 5.5 (prompts up to 100K) | $0.10 / $0.50 | V | same |
| Gemini 3.1 Pro Preview (prompts up to 200K) | $2 / $12 | V | https://cloud.google.com/vertex-ai/generative-ai/pricing |
| Gemini 3.8 Flash (promo until 31 Dec 2026, then $1.50 / $7.50) | $0.75 / $3.75 | V | same |
| OpenAI gpt-6.1-sol / gpt-6-luna / gpt-6-astra | $2/$10, $0.10/$0.50, $10/$50 | S (conflicting) | https://developers.openai.com/api/docs/pricing |

## 2. Character reference images (USD per image)

| Model | Price | Notes | Source |
|---|---|---|---|
| Nano Banana 2 (Gemini 3.1 Flash Image) | $0.067 at 1K, $0.101 at 2K | Takes several reference images, good for keeping characters consistent | V (Vertex) |
| Nano Banana Pro (Gemini 3 Pro Image) | $0.134 at 1K/2K, $0.24 at 4K | Best quality | V |
| Imagen 4 / Fast / Ultra | $0.04 / $0.02 / $0.06 | No multi-reference input | V |
| OpenAI gpt-image-1.5 | $0.009 / $0.034 / $0.133 (low / medium / high, 1024²) | | S, https://developers.openai.com/api/docs/models/gpt-image-1.5 |
| OpenAI gpt-image-2 | about $0.006 to $0.21 | | unverified |
| FLUX.1 Kontext pro / max | $0.04 / $0.08 | | S/3P, https://developer.puter.com/tutorials/flux-api-pricing/ |
| FLUX.2 pro | from $0.03 per megapixel | | S, https://help.bfl.ai |
| Ideogram 3.0 with Character Reference | $0.10 / $0.15 / $0.20 | | S, https://ideogram.ai/api-pricing/ |

Commercial use: the Google, OpenAI and BFL APIs all assign you the output rights under their terms. I did not re-read each ToS clause.

## 3. Video generation

| Provider | USD per second of output | Clip length / 9:16 / audio | Access | Source |
|---|---|---|---|---|
| **Google Veo 3.1** | $0.40 with audio, $0.20 without (720p/1080p) | 4, 6 or 8 s clips, can be extended; 9:16 supported (except reference-to-video in preview); native audio | Gemini API or Vertex, self-serve | V Vertex. Length: https://cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate-preview |
| Veo 3.1 Fast | $0.10 to $0.12 with audio, $0.08 to $0.10 without | same | same | V |
| Veo 3.1 Lite | $0.05 to $0.08 with audio, $0.03 to $0.05 without | same | same | V |
| **OpenAI Sora 2 / 2 Pro** | was $0.10 (720p) and $0.30 to $0.50 (Pro) | 4 to 20 s | **Reported shut down: app on 2026-04-26, API on 2026-09-24. Do not build on it.** | S, https://engadget.com/ai/openai-is-shutting-down-its-sora-video-generation-app-211023358.html , https://zilliz.com/ai-faq/what-is-the-sora-shutdown-timeline |
| Kling 3 / O3 (official API) | about $0.10 (V3 Std 720p) to $0.40 (O3 Pro 1080p with audio) | O3 has native audio and lip-sync; prepaid packages from $9.80 | klingai.com developer portal | 3P, https://aireiter.com/blog/kling-3-api-pricing-guide-2026 |
| Runway Gen-4.5 / Aleph / Act-Two | $0.01 per credit; Gen-4.5 12 or 25 credits/s, Aleph 28, Act-Two 5 (so $0.12 to $0.25, $0.28, $0.05 per second) | | dev.runwayml.com, self-serve | 3P, https://eesel.ai/blog/runway-ai-pricing |
| ByteDance Seedance 2.5 (BytePlus ModelArk) | token-billed, about $0.10/s at 480p and $0.23/s at 720p (estimated) | Access may still be "coming soon" | BytePlus console | 3P, https://saascrmreview.com/seedance-pricing/ |
| MiniMax H3 (successor to Hailuo) | $0.08/s at 768p, $0.13/s at 2K | | platform.minimax.io | 3P, https://usagepricing.com/blueprint/activity/minimax-2026-07-30-launch |
| Luma Ray3.x | about $0.06 to $0.24/s | | lumalabs.ai API | 3P, https://wavespeed.ai/blog/cost-and-billing/luma-ai-pricing/ |
| Pika 2.2 (through fal) | about $0.04 to $0.09/s | dev.pika.art membership $10/month | | 3P, https://perkstack.co/blog/cheapest-pika-2-2-api |
| Wan 2.6 (Alibaba Model Studio API) | about $0.07 to $0.15/s | Only Wan 2.2 has open weights (**Apache-2.0**); 2.5, 2.6 and 2.7 are API-only | | 3P, https://howaiworks.ai/blog/alibaba-wan-open-weights-stopped-at-2-2 |

## 4. Lip-sync and talking heads

| Provider | Price | Source |
|---|---|---|
| sync.so lipsync-2-pro | $0.067 to $0.083 per second (25 fps, depends on plan) | S, https://sync.so/docs/billing |
| HeyGen API | $1/min standard avatar, $4/min Avatar IV 1080p | 3P, https://www.arcade.software/post/heygen-pricing |
| Hedra Character-3 | 6 credits/s, about $0.033 to $0.06/s on subscription; separate API price unverified | S, https://www.hedra.com/plans |
| Kling lip-sync | from $0.15 per run (WaveSpeed reseller); official price unverified | 3P |
| Runway Act-Two | 5 credits/s, about $0.05/s | 3P |
| Tavus | pre-rendered video $0.80 to $1.00 per minute | S, https://www.tavus.io/pricing |
| LatentSync (Apache-2.0), MuseTalk (MIT code; weights and dependencies need a separate check), Hallo3 (license unverified, built on CogVideoX) | self-host on GPU | 3P, https://www.creativeainews.com/articles/open-source-lip-sync-licenses-commercial-use-2026/ |

## 5. Text-to-speech and voice cloning (Turkish support is critical)

| Provider | Price | Turkish | Cloning consent | Source |
|---|---|---|---|---|
| ElevenLabs v3 / Multilingual | $0.10 per 1K characters; Flash/Turbo $0.05 per 1K | Yes. v4 / v4 Turbo launched in Sep 2026 with 90+ languages; v4 price unverified | Professional clone requires a captcha read aloud by the voice owner | S, https://elevenlabs.io/docs/eleven-api/guides/how-to/voices/professional-voice-cloning |
| Gemini 3.8 Flash TTS | $0.50 per 1M text tokens + $9 per 1M audio tokens (promo; doubles on 1 Jan 2027) | Turkish support unverified; has multi-speaker voice and voice replication | | V https://cloud.google.com/text-to-speech/pricing |
| Google Chirp 3 HD | $30 per 1M characters; instant custom voice $60 per 1M | Yes | Needs consent recording | V |
| OpenAI gpt-4o-mini-tts | about $0.015 per minute | Yes (multilingual); no custom voices | | S |
| Azure Neural / HD | about $15 to $16 / $30 per 1M characters | Yes (tr-TR); Personal Voice is gated behind an application | | 3P |
| Cartesia Sonic | about $39 per 1M characters (Startup plan) | Turkish voices exist | | 3P |
| MiniMax Speech 2.6 HD / Turbo | $100 / $60 per 1M characters | Turkish unverified | | S, https://replicate.com/minimax/speech-2.6-hd |

## 6. Music (needs royalty-cleared commercial license)

| Provider | Price / license | Source |
|---|---|---|
| **Google Lyria 3** (30 s clip) / Lyria 3 Pro (full song) | $0.04 / $0.08 per generation; Google API terms | V Vertex |
| ElevenLabs Music | about $0.15/min; commercial use on paid plans (film/TV rights need Enterprise) | S, https://elevenlabs.io/blog/elevenlabs-vs-suno |
| Stable Audio 2.5 | $0.20 to $0.68 per generation through resellers; official price unverified | 3P |
| Suno | No public API. Invite-only partner program since July 2026 | S, https://www.digitalmusicnews.com/2026/07/03/suno-is-opening-an-api-partner-program/ |
| Udio | No public API; default output is non-commercial. **Avoid** | 3P |
| Mubert / Beatoven | Mubert: sales quote (about $49 to $499/month reported). Beatoven: $3 per download minute (app pricing) | 3P |

## 7. Speech recognition and word timings for subtitles (all support Turkish)

| Provider | Price | Source |
|---|---|---|
| Deepgram Nova-3 multilingual | about $0.0043 to $0.0052 per minute | 3P |
| AssemblyAI Universal-3.5 Pro | $0.21 per hour | S, https://www.assemblyai.com/pricing.md |
| ElevenLabs Scribe v2 | $0.22 per hour | 3P |
| OpenAI gpt-4o-transcribe | about $0.006 per minute | S |
| WhisperX (BSD-2) | free; self-host | Turkish word alignment needs a wav2vec2 Turkish alignment model |

## 8. Infrastructure

| Item | Price | Source |
|---|---|---|
| Cloudflare Stream | $5 per 1,000 minutes stored, $1 per 1,000 minutes delivered; encoding free | S, https://developers.cloudflare.com/stream/pricing |
| Cloudflare R2 | $0.015 per GB-month; no egress fees | S, https://developers.cloudflare.com/r2/pricing |
| Mux | encoding about $0.025 to $0.0375 per minute; delivery price unverified | S, https://www.mux.com/docs/pricing/video |
| RunPod | H100 $1.99/hr, L40S $0.79/hr | S, https://www.runpod.io/gpu-cloud/pricing |
| Modal | H100 $3.95/hr, L40S $1.95/hr (list price; multipliers apply in production) | 3P |
| Lambda | H100 $2.99 to $4.29/hr | unverified |

## 9. Payments and app-store rules

- **Apple guideline 3.1.1** (V, https://developer.apple.com/app-store/review/guidelines/, last updated 2026-06-08): unlocking episodes or coins requires in-app purchase. Tips to "digital content providers" may use IAP currencies. Coins bought with IAP may not expire. Under 3.2.1(vii), person-to-person monetary gifts can skip IAP only if 100% goes to the receiver and the gift is never tied to digital content. **Creator tips therefore effectively need IAP.**
- **US:** External purchase links and buttons are allowed on the US storefront (3.1.1(a)/3.1.3). After the Ninth Circuit ruling of Dec 2025, Apple cannot charge commission on link-outs until the district court sets a rate, and that rate is still pending (S, https://appleinsider.com/articles/26/03/30/the-epic-vs-apple-case-wont-get-a-rehearing-for-app-store-fees).
- **Apple commission:** 30% standard, 15% for the Small Business Program and for subscriptions after year 1 (standard terms; not re-checked).
- **EU (DMA), new terms from 2026-10-01:** IAP 26%, alternative payment processor 20%, link-out 15%, 5% Core Technology Commission (S, https://aa.com.tr/en/science-technology/apple-revamps-eu-app-store-fees-to-resolve-regulatory-dispute/4031035).
- **Google Play (Epic settlement, rolling out since 2026-06-30 in US/EEA/UK):** 20% on in-app purchases (15% in some programs), 10% on subscriptions and on the first $1M, plus 5% if you use Google's own billing. External offers are allowed but a service fee still applies (S, https://techcrunch.com/2026/03/04/google-settles-with-epic-games-drops-its-Play-Store-commissions-to-20/). Final court approval is unverified.
- **Stripe US:** 2.9% + 30¢ per transaction; +1.5% for international cards; +1% for currency conversion. **Connect:** $2 per monthly active account plus 0.25% + 25¢ per payout (S, https://stripe.com/connect/pricing).
- **Turkey:** Stripe does not onboard Turkish entities (3P; Stripe docs cover only VAT collection in Türkiye). Use **iyzico** (about 1.79% to 2.29% + 0.25 TL, 3P) or **PayTR** (fees unverified) for TRY payments. For global payouts, the common route is a US LLC or EU entity with Stripe.

## 10. Cost of one 60-second, 9:16 episode

Assumptions: 3 characters, 8 shots of about 7.5 s each, about 120 words (about 700 characters) of dialogue. Over-generation for retakes is 2.5x on the budget route and 3x on the premium route. All prices are list prices.

| Line item | Budget route | USD | Premium route | USD |
|---|---|---|---|---|
| Script / bible / shot list | Gemini 3.8 Flash or Haiku 5.5 (60K in, 20K out) | 0.05 | Opus 5.5 (100K in, 40K out) | 1.20 |
| Character sheets + keyframes | Nano Banana 2 at 1K, 28 images | 1.90 | Nano Banana Pro, 42 images, + 10 Kontext max edits | 6.43 |
| Video | Veo 3.1 Lite 720p without audio, 150 s | 4.50 | Veo 3.1 1080p with audio, 192 s (Kling O3 Pro costs about the same) | 76.80 |
| Text-to-speech | ElevenLabs Flash, 2.1K characters | 0.11 | ElevenLabs v3, 3.5K characters | 0.35 |
| Lip-sync | LatentSync on RunPod L40S, about 0.5 hr | 0.40 | sync lipsync-2-pro, 80 s | 6.64 |
| Music | Lyria 3, 2 clips | 0.08 | ElevenLabs Music, 3 min | 0.45 |
| Subtitles | Deepgram / WhisperX | 0.01 | Scribe v2 | 0.01 |
| Assembly / upscaling compute | ffmpeg on a CPU or small GPU | 0.10 | upscale + grade on GPU | 0.50 |
| Storage + delivery | CF Stream: $0.005/month stored, $0.001 per view | ~0.01 | same | ~0.01 |
| **Total per episode** | | **≈ $7.20** | | **≈ $92.40** |

The mid-tier option is Veo 3.1 Fast 1080p with audio (about $0.12/s × 180 s ≈ $22), which puts an episode at about $30. Native-audio Veo lets you drop the separate TTS and lip-sync steps. That route was not tested for Turkish voice consistency across episodes, so treat it as unverified.

## Accounts and API keys the founder must open

1. Anthropic Console (Claude API)
2. Google Cloud project with Vertex AI and Gemini API billing (Veo, Nano Banana, Imagen, Lyria, TTS)
3. OpenAI Platform (gpt-image and transcription; **not Sora**)
4. ElevenLabs (API plan; Professional Voice Clone verification for each voice actor)
5. sync.so
6. Kling developer platform (klingai.com) and/or Runway API (dev.runwayml.com); optionally BytePlus ModelArk and MiniMax
7. Black Forest Labs (api.bfl.ai) or fal.ai as an aggregator fallback
8. Deepgram or AssemblyAI
9. Cloudflare (Stream + R2)
10. RunPod or Modal (open-model GPUs)
11. Apple Developer Program + App Store Connect (IAP, Small Business Program)
12. Google Play Console
13. iyzico or PayTR (Turkish entity, TRY payments) and Stripe + Connect through a US or EU entity
