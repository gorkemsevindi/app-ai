# Known limitations & backlog

## Not yet done (by phase)
- **GPU validation (Phase 2)**: no GPU in the build environment. Adapters, CLI wiring and VRAM numbers for
  DreamID-V / Wan2.2 must be validated on a GPU node; the end-to-end path is proven with mock models + real
  tracking/compositing/encode.
- **Production analysis components**: ONNX detector, YuNet/SFace, SAM 2.1 mask propagation and the
  minor/NSFW classifier are interfaces with production classes but untested on real footage; masks currently
  come from boxes (ellipses), SAM 2.1 masks are the next step.
- **Output moderation scorer**: interface only (`OUTPUT_SCORER`); production must configure one (API fails
  closed when `required` and no scores).
- **Social sign-in UI**: API verifies Apple/Google ID tokens; the native buttons are not wired yet.
- **Phase 3**: Next.js admin UI (admin API exists), push notifications, template preview media.
- **Phase 4**: StoreKit 2 / Play Billing (RevenueCat optional), `/purchases/verify`, webhooks inbox processing,
  subscription credit grants, reconciliation. Tables exist (`purchases`, `subscriptions`, `webhook_events`).
- **Phase 5/6**: retention lifecycle jobs, load tests (normal / 10× spike / GPU exhaustion), backup restore
  automation, IaC (Terraform), INFRASTRUCTURE/DR/RUNBOOKS/CAPACITY docs.
- **Phase 7**: STORE_RELEASE.md, privacy policy/terms drafts, listing copy, screenshots.
- Docker images were not built in this environment (no Docker daemon); CI builds and scans them.
- Brand name is a placeholder ("AI Video (working title)"); bundle ids are `com.example.aivideo`.

## V2 backlog
Trend Engine; multi-person native multi-ID model adapter (SCAIL-2 / MoCha after licence checks); voice/lip-sync;
creator marketplace; GPU scheduler (spot + owned); LoRA fine-tuning; referral + share attribution; web app.
