# Spec review: what to add, change and improve (Master Spec v1.0)

Date: 2026-10-08. My review of the Master Specification v1.0, ordered by impact. Items marked
**[DECISION]** need the owner's approval. They are collected in [DECISIONS-NEEDED.md](DECISIONS-NEEDED.md).

## A. Change these, because they will block launch or make it far harder

1. **Use coins, not dynamic per-episode products.** Apple and Google IAP products must be
   pre-registered in App Store Connect and Play Console. You cannot create a product for every episode
   a creator publishes. Every top micro-drama app (ReelShort, DramaBox and others) sells consumable
   **coin packs** through IAP, then spends the coins server-side on episodes, bundles and seasons.
   - Recommendation: a coin wallet in the existing double-entry ledger. Coin packs and subscriptions
     are the only store products. Episode, bundle and season prices are expressed in coins.
   - Revenue share then needs a coin-to-net-money conversion rule, for example the average realised
     net per coin over the trailing 30 days. **[DECISION]**
   - The current code models per-episode products through the sandbox store, which keeps the
     server-side rules testable. Switching to coins is the next commerce slice.
2. **Remove Sora from the video candidate list.** It was reportedly shut down: the app on 2026-04-26
   and the API on 2026-09-24 (secondary sources; see 02-PROVIDERS-AND-COSTS.md).
   - Anchor on **Google Veo 3.1** (official, self-serve, 9:16, 4–8 s clips, extendable), which
     I verified on the vendor page.
   - Use Kling or Runway as the second provider, behind the same adapter.
3. **Separate voice from picture for dialogue shots.** Native-audio video models (Veo, Kling O3) speak
   with a different voice in every clip, so a character's voice drifts between episodes.
   - Recommended dialogue route: silent video, then a **fixed TTS voice id per character**
     (ElevenLabs v3, which supports Turkish), then a lip-sync pass (sync.so or self-hosted
     LatentSync).
   - Native audio stays for ambience and B-roll. The pipeline already has this shape: the voice step
     is separate from the performance step, and visemes drive the mouth.
4. **Launch with fictional characters only. Real-person likeness and face swap come in phase 2.**
   - App Store guideline 1.2 / 5.1, Play's AI-content policy, EU AI Act Art. 50 and KVKK (facial data
     counts as **special-category biometric data** and needs explicit consent and VERBİS registration)
     make this the riskiest feature in the spec.
   - The consent, review and revocation machinery is implemented and tested, but no face-swap provider
     is wired in. Recommendation: keep the feature off at launch. **[DECISION]**
5. **Change who pays production cost.** At about $7 per 60 s episode on the budget route and about
   $30–92 on mid/premium, UGC creators will not pre-pay a 30-episode series ($200–2,700) with no
   proof of demand.
   - Recommendation: a **platform "Originals" programme** for the first 3–6 months. The platform
     funds the generation credits of selected creators and takes a higher share until costs are
     recouped. **[DECISION]**
   - Free-trial credits are already configurable (`DRAMA_SIGNUP_BONUS_CREDITS`).
6. **Payout entity.** Stripe does not onboard Turkish entities.
   - For TRY collection: iyzico or PayTR.
   - For global creator payouts: a US or EU entity with Stripe Connect, or Payoneer/Wise.
   - Entity structure decides the payment rails, the tax forms (W-8/W-9 for the US) and the
     KYC vendor. **[DECISION]**

## B. Add these, because the spec is missing them

| Addition | Why |
|---|---|
| **C2PA Content Credentials** on every published file, alongside the visible "AI-generated" label | EU AI Act Art. 50 transparency obligations (from Aug 2026), YouTube and TikTok disclosure rules, and provenance for takedowns. The code already embeds `comment=AI-generated` metadata and stores full provenance JSON; C2PA signing is the next step. |
| **Dubbing and localisation multiplier** (TR to EN/AR/ES/DE …) | Re-voicing and re-lip-syncing an existing episode costs a fraction of making a new one. It is the cheapest way to grow the catalogue, and the pipeline's per-line TTS cache and alignment make it a small slice. |
| **Character reference packs and LoRA training** as "Character DNA v2" | The spec asks for consistent identity. In practice this needs a locked reference pack (Nano Banana multi-reference) and later a per-character fine-tune. The DNA hash and versioning are already in place. |
| **Coins, daily check-in and rewarded-ad unlocks** | The proven micro-drama retention loop. The spec lists ads only as optional. |
| **Copyright and duplicate detection** on uploads, and an LLM originality check on scripts | Repeat-infringer policy (spec §10) needs signals. |
| **A cost-to-revenue guardrail per series** | Warn creators when projected production spend is more than N× their trailing revenue. |
| **An observability budget** (job p95, cost per finished minute, QC failure rate per provider) | Provider routing by price, quality and latency (spec §3) needs these metrics first. |

## C. Improve these, because they are fine in principle but underspecified

- **Define "qualified view" once, in the policy text.** The code uses: entitled, ≥50 % or ≥30 s
  watched, deduplicated per viewer, episode and day. This number feeds rankings and any future
  subscription pool split. **[DECISION]**
- **Subscription revenue split.** Spec §5 gives 60/40 for series revenue. Subscriptions need a
  pool rule, for example net subscription revenue × 60 % split by qualified watch time. The code
  parks subscription net revenue in `liability:subscription_pool` until that rule is approved.
  **[DECISION]**
- **Hold window.** Earnings stay pending for 30 days by default, to cover refunds and chargebacks.
  Apple refunds can arrive up to about 90 days later; the ledger claws back from the available
  balance if that happens. **[DECISION]**
- **Timeline.** The 14-week plan is realistic for the scope in this repo with mocked providers. With
  real providers, store review and payout KYC, plan **5–6 months to public launch**. Apple review of
  a UGC + AI + IAP app usually takes several rounds.
- **Brand.** "Sahne" is a working name only. It needs a trademark search in TR (TÜRKPATENT), the EU
  (EUIPO) and the US, plus domain and store-name availability. **[DECISION]**

## D. What I would keep exactly as specified

- The modular monolith with separately scaled workers.
- Idempotent, resumable steps.
- The append-only double-entry ledger.
- The manual publish gate.
- A transparent ranking v1.
- Configurable pricing.
- "Never promise earnings from views".
- The explicit rule to label mocks.

These are the right foundations, and the code follows them.
