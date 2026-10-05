# Phase 0 Research: Self-Hosted Identity-Preserving Vertical AI Video (5-10 s)

Date of research: 2026-10-05. Author: research agent (Claude Code). Status: Phase 0, not legal advice.

## 1. Summary

- **Base model family: Wan2.2 (Alibaba), Apache-2.0 code and weights.** Wan2.2 is the most permissive state-of-the-art open video base. Its README says "The models in this repository are licensed under the Apache 2.0 License" and that Alibaba claims no rights over generated content. It ships T2V-A14B, I2V-A14B, TI2V-5B, S2V-14B and Animate-14B, with Diffusers and ComfyUI integrations. The repo is still active (last commit 2026-09-21).
- **Identity approach for "viral template" videos:** swap the user into an existing template clip. Do not generate the template from scratch each time.
  - **DreamID-V** (official repo `bytedance/DreamID-V`, Apache-2.0, built on Wan2.1-1.3B) does video face swap at low VRAM.
  - **Wan2.2-Animate-14B** in "replacement" mode (Apache-2.0, in Diffusers) replaces the whole character. It is the higher-quality fallback, but it needs much more GPU.
- **`davahiatak1/DreamID-V-video-face-swap` is a GitHub fork of `bytedance/DreamID-V`.** It has no commits by the fork owner, 0 stars, and is behind upstream. Do not use it. Use the official repo.
- **Stand-In** (WeChatCV, Apache-2.0, CVPR 2026) is the best prompt-driven identity adapter for Wan2.1/2.2-T2V-14B. But it depends on InsightFace `antelopev2`, which is licensed for **non-commercial research only**. ConsisID, Lynx and Concat-ID have the same InsightFace dependency.
- **Top licence traps:**
  - InsightFace models (inswapper, buffalo_l, antelopev2) are non-commercial.
  - CausVid is CC BY-NC-SA 4.0.
  - The **Wan2GP / WanGP Community License 2.0 forbids paid API/SaaS/hosted use.**
  - HunyuanCustom's licence excludes the EU, UK and South Korea.
  - The LTX-2.x licence requires a paid licence above $10M annual revenue.
  - ComfyUI is GPL-3.0.
  - Remotion needs a paid company licence for for-profit organizations with more than 3 employees, plus automation fees.
- **Composition:** use FFmpeg (an LGPL build) with Python (MoviePy MIT, or raw filtergraphs) for the MVP. Remotion is fine while the company has 3 or fewer people, or with budget for the Automator plan. Vanta is a young aggregator (8 commits) and is useful only as a reference.

## 2. Methodology

- **Date:** 2026-10-05. All "last activity" values are what was observed on that day.
- **Sources:**
  - github.com HTML pages (repo and commits pages) via WebFetch.
  - raw.githubusercontent.com README, LICENSE and requirements.txt files via curl, read directly.
  - Web search for Remotion pricing and Concat-ID.
- **Access limits:**
  - **huggingface.co, remotion.dev and remotion.pro were blocked by the egress proxy.** Hugging Face model-card licence fields could not be read directly. Where a weights licence is stated below, it comes from the GitHub README unless marked "unverified".
  - api.github.com and the GitHub MCP were not available for third-party repos. Commit dates come from the HTML commits pages.
  - Stars were noted only as context. Ranking is based on licence fit, the task (template-driven identity video), VRAM and maintenance.
- **Verdict scale:**
  - OK: permissive for code and weights.
  - OK-with-conditions: usable commercially with obligations or limits.
  - NOT OK: non-commercial, or forbids our use.
  - UNCLEAR: not enough verified information.

## 3. Main table (the 4 requested projects)

| # | Project / URL | Purpose | Last activity (observed) | Code license | Weights license | Commercial verdict | GPU / VRAM | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | **Wan2.2** https://github.com/Wan-Video/Wan2.2 | Open video foundation models: T2V-A14B and I2V-A14B (MoE, 480p/720p), TI2V-5B (720p@24fps, T2V+I2V), S2V-14B, Animate-14B (animate and replace) | Last commit 2026-09-21 (community-works PR). Last model news 2025-11-13 (Animate in Diffusers). | Apache-2.0 (LICENSE.txt read) | Apache-2.0 per the README "License Agreement" section. HF card not readable (blocked). | **OK** (Apache-2.0; the README adds use restrictions on unlawful or harmful content) | TI2V-5B: "at least 24GB VRAM (e.g. RTX 4090)" with offload, `--convert_model_dtype` and `--t5_cpu`; a 5 s 720p video in "under 9 minutes" on one consumer GPU. A14B / S2V: "at least 80GB VRAM" on one GPU; multi-GPU through FSDP and Ulysses. | Diffusers pipelines exist for T2V-A14B, I2V-A14B and TI2V-5B (2025-07-28) and for Animate-14B (2025-11-13). ComfyUI is native since 2025-07-28. Kijai's WanVideoWrapper (Apache-2.0) and DiffSynth-Studio (Apache-2.0) add FP8, offload and LoRA. Animate-14B "replacement" mode fits "user replaces person in template" directly. |
| 2 | **Stand-In** https://github.com/WeChatCV/Stand-In | Lightweight identity-preserving adapter (about 1% extra parameters, 153M) for Wan T2V. Supports community LoRAs, VACE pose control and experimental face swap. | Last commit 2026-08-10 ("Stand-In-V2 coming soon"). Wan2.2 version released 2025-12-22. CVPR 2026. | Apache-2.0 (LICENSE read) | Stand-In weights: unverified (HF `BowenXue/Stand-In` blocked). Base Wan weights are Apache-2.0. **Requires InsightFace `antelopev2`** (README) and `insightface==0.7.3` (requirements) = **non-commercial**. | **OK-with-conditions / UNCLEAR.** The code is fine. The antelopev2 dependency is NOT OK commercially unless it is replaced or licensed from insightface.ai. | Not stated. It runs on Wan2.1-T2V-14B or Wan2.2-T2V-A14B, so plan for 80GB, or about 24-48GB with FP8 and offload (unverified). | Works with Wan2.2-T2V-A14B. Kijai's ComfyUI port differs from the official version (README warning). Face swap is "experimental". Before use, check whether antelopev2 is used only for detection and cropping (then swappable, e.g. MediaPipe/YOLO-face) or for embeddings the model was trained on (then retraining is needed). Unverified. |
| 3 | **DreamID-V (reupload)** https://github.com/davahiatak1/DreamID-V-video-face-swap | Video face swap with a Diffusion Transformer | Fork; latest commit shown 2026-01-11 by upstream author Guoxu1233. No commits by davahiatak1. | Apache-2.0 (LICENSE.txt identical to upstream) | Same as official (below) | **Do not use.** Not official, 0 stars, behind upstream (missing the 2026-01-13 and 2026-05 updates). | Same as official | **It is a GitHub fork of `bytedance/DreamID-V`** (the page shows "forked from bytedance/DreamID-V"). The README is a near-identical copy. No malicious files were seen, but always pull from the official repo. |
| 3a | **DreamID-V (official)** https://github.com/bytedance/DreamID-V | ByteDance plus Tsinghua. Swaps a reference face into a driving video, keeping the template's motion, lighting and background. Variants: Wan-1.3B-Faster, -DWPose, -MediaPipe. | Last commit 2026-05-22. Code released 2026-01-05. arXiv 2601.01425. | Apache-2.0 (LICENSE.txt read) | Unverified: HF `XuGuo699/DreamID-V` blocked. The base is Wan2.1-T2V-1.3B (Apache-2.0). Extra preprocessors: DWPose `dw-ll_ucoco_384.onnx` and `yolox_l.onnx` (licences unverified), or MediaPipe (Apache-2.0). | **OK-with-conditions** (confirm the HF weights licence and the DWPose ONNX licences; or use the MediaPipe variant) | 1.3B base. A community "16GB VRAM GPUs" ComfyUI port exists (Goldlionren). The "Faster" variant claims "lower VRAM usage". The official VRAM figure is not stated. | Recommended resolution is 1280x720. The reference should be a cropped 512x512 face. Steps can drop to 20. `requirements.txt` has **no insightface** (mediapipe and onnxruntime instead). This is a good fit for template-driven swaps. ComfyUI nodes: HM-RunningHub, Goldlionren. |
| 4 | **Vanta** https://github.com/itsjwill/vanta | Remotion-based "programmatic AI video engine". TypeScript integrations wrap 40+ OSS tools (Wan 2.2 via ComfyUI, WhisperX captions, TTS, MuseTalk, etc.). | 8 commits, 2026-02-08 to 2026-07-26 (v2.1.0). About 130 stars. | MIT (LICENSE read) | N/A (it orchestrates third-party models with their own licences) | **OK-with-conditions.** Vanta is MIT, but it depends on Remotion (company licence) and ComfyUI (GPL-3.0). | Depends on the backends. Remotion rendering is CPU/Chromium. | Young and essentially single-author. Its marketing claims ("replaces Remotion Pro Store") are unverified. Use as a reference for caption and template patterns, not as a dependency. Its own README notes that Remotion needs a company licence for for-profit teams of 4+. |
| 4a | **Remotion** https://github.com/remotion-dev/remotion | React-based programmatic video rendering | Active (not separately dated) | **Remotion License (source-available, not OSI)** | N/A | **OK-with-conditions** | CPU and headless Chromium | LICENSE.md (read): free for individuals, **for-profit organizations with up to 3 employees**, non-profits and evaluation. Otherwise a Company License is required. You may not resell or relicense Remotion derivatives. Pricing (secondary sources; the official pages were blocked, so unverified): Creators $25/seat/month; **Automators $0.01/render with a $100/month minimum** (this covers automated video products); Enterprise from $500/month. |

## 4. Alternatives table (2025-2026, active or relevant)

### 4.1 Identity-preserving and subject-to-video generation

| Project / URL | Purpose | Last activity | Code lic. | Weights lic. | Verdict | GPU/VRAM | Notes |
|---|---|---|---|---|---|---|---|
| ConsisID https://github.com/PKU-YuanGroup/ConsisID | ID-preserving T2V (CogVideoX-5B based), CVPR 2025 Highlight, in Diffusers | README news to 2026-03-08 (points to Helios) | Apache-2.0 | Unverified. The CogVideoX-5B base licence is unverified. Uses `insightface==0.7.3` and facexlib. | **NOT OK as-is** (InsightFace) / UNCLEAR | 44GB at 720x480x49f; 25GB with model CPU offload; 22GB with sequential offload (README) | Older base (8 fps, 6 s). Lower quality than Wan-based options. |
| Concat-ID https://github.com/ML-GSAI/Concat-ID | Universal ID-preserving video (CogVideoX-5B, Wan2.1-1.3B) | Last update 2025-05-07; 10 commits | **Unverified** (README/LICENSE not fetched) | Unverified (ModelScope `yongzhong/Concat-ID`). Uses insightface. | **UNCLEAR** | about 24GB max (CogVideoX, tested on H800) | Low activity |
| Phantom https://github.com/Phantom-video/Phantom | Subject-consistent video (Phantom-Wan 1.3B/14B), ICCV 2025 | Last news 2025-09-10 | Apache-2.0 | Unverified (HF blocked). Base Wan2.1 is Apache. No insightface in requirements. | **OK-with-conditions** (confirm the weights licence) | 1.3B consumer GPU; 14B about 80GB (unverified) | ComfyUI via Kijai. Identity fidelity for faces is weaker than in face-specific methods (unverified, anecdotal). |
| MAGREF https://github.com/MAGREF-Video/MAGREF | Multi-reference video (Wan2.1-I2V-14B) | ICLR 2026 news 2026-02-25 | Apache-2.0 | Unverified (HF). No insightface in requirements. | **OK-with-conditions** | "around 70 GB", 80GB recommended. Kijai FP8 build exists. | |
| BindWeave https://github.com/bytedance/BindWeave | Subject-consistent video (Wan-14B plus MLLM) | ICLR 2026 news 2026-01-27 | Apache-2.0 | Unverified (HF `ByteDance/BindWeave`) | **OK-with-conditions** | 14B, about 80GB (unverified); Kijai FP8 build | OpenS2V-Eval 57.61 (self-reported) |
| Lynx https://github.com/bytedance/lynx | Personalized video from one image (Wan2.1-T2V-14B, ID-adapter plus Ref-adapter), CVPR 2026 | 8 commits; exact date unverified | Apache-2.0 | Unverified (HF `ByteDance/lynx`). **Uses insightface 0.7.3** and facexlib. | **NOT OK as-is** (InsightFace) / UNCLEAR | Not stated; 14B base | `lynx_lite` variant for efficiency |
| HuMo https://github.com/Phantom-video/HuMo | Human-centric text, image and audio to video (1.7B/17B, Wan-based) | Last news 2025-12-23 | Apache-2.0 | Unverified | **OK-with-conditions** | 1.7B: 480p in 8 min on 32GB. 17B runs on a 3090 via Kijai. | Useful if templates need lip-sync or audio |
| HunyuanCustom https://github.com/Tencent-Hunyuan/HunyuanCustom | Multimodal subject-customized video | Last news 2025-06-13 | Tencent Hunyuan Community License | Same | **NOT OK** for a global app: **does not apply in the EU, UK or South Korea**; licence needed above 100M MAU | 80GB at 720p (24GB min, very slow; about 8GB via WanGP) | Territory exclusion is a deal-breaker for app-store distribution |
| SkyReels-A2 https://github.com/SkyworkAI/SkyReels-A2 | Elements-to-video composition | Last news 2025-06-01 | LICENSE.txt = Skywork Community License (MIT badge commented out) | Skywork Community License | **UNCLEAR / OK-with-conditions** (says commercial use is allowed; full PDF terms not read) | Unverified | Also asks users not to deploy in internet services without "security reviews and records" |
| VACE https://github.com/ali-vilab/VACE | All-in-one video create and edit (Wan2.1) | Last news 2025-10-17 | Apache-2.0 | Wan2.1-VACE 1.3B/14B: Apache-2.0. VACE-LTX: RAIL-M. | **OK** (Wan variants) | 1.3B consumer; 14B large | Pose and depth control; combines with Stand-In |
| LTX-2 / LTX-Video https://github.com/Lightricks/LTX-2 | Fast video (plus audio) generation | LTX-2.x licence dated 2026-08-11 | LTX Community License | LTX-2.x: free under $10M annual revenue; above that a paid licence is required | **OK-with-conditions** (revenue threshold) | Distilled models for consumer GPUs | Speed-tier alternative. The older LTX-Video repo is Apache-2.0 (code). |

### 4.2 Face swap (video)

| Project / URL | Purpose | Last activity | Code lic. | Weights lic. | Verdict | Notes |
|---|---|---|---|---|---|---|
| FaceFusion https://github.com/facefusion/facefusion | Face swap, lip-sync and enhancement platform | Active; 414 commits, about 30k stars; LICENSE copyright 2026 | **OpenRAIL-AS** (use-based restrictions) | Per model. Swappers include inswapper_128 (InsightFace, non-commercial), simswap, ghost, blendswap, uniface, hififace_unofficial, hyperswap. Each model licence is unverified. | **NOT OK by default / UNCLEAR.** Models must be cleared individually, and the inswapper default is non-commercial. | Frame-by-frame GAN swap, not diffusion. Fast, but lower temporal quality than DreamID-V (unverified). |
| InsightFace https://github.com/deepinsight/insightface | Face detection, recognition and swap toolkit | Very active (InsightFace 2.1 changelog 2026-10-03) | MIT (code) | **Non-commercial research only** for trained models (inswapper_128, buffalo_l, antelopev2). The 2025-11-24 update says to contact insightface.ai for commercial licences. | **Code OK; weights NOT OK** without a paid licence | Affects Stand-In, ConsisID, Lynx, Concat-ID, FaceFusion and many ComfyUI nodes. A transitive `pip install insightface` auto-downloads the non-commercial packs. |

### 4.3 Wan optimization and quantization

| Project / URL | Purpose | Last activity | License | Verdict | Notes |
|---|---|---|---|---|---|
| LightX2V https://github.com/ModelTC/LightX2V | Inference framework for Wan and others: step distillation, FP8/NVFP4, offload, sparse attention | News 2026-09-22 | Apache-2.0 | **OK** (code). Distilled weight licences are unverified (HF). | Wan2.2-14B NVFP4 + sparse (Blackwell) 2026-05-29 |
| Wan2.2-Lightning https://github.com/ModelTC/Wan2.2-Lightning | 4-step distilled LoRAs for Wan2.2 (about 20x fewer NFE) | V2.0 released 2025-11-08 | Apache-2.0 (repo) | **OK-with-conditions** (HF weights licence unverified) | Native ComfyUI workflows |
| CausVid https://github.com/tianweiy/CausVid | Few-step autoregressive Wan distillation | Not dated | **CC BY-NC-SA 4.0** | **NOT OK** | Avoid "CausVid LoRA" extractions too: they likely inherit NC (unverified) |
| Self-Forcing https://github.com/guandeh17/Self-Forcing | Autoregressive few-step Wan-1.3B | 2025 | Apache-2.0 | OK-with-conditions (weights unverified) | Alternative to CausVid |
| TeaCache https://github.com/ali-vilab/TeaCache | Training-free timestep caching (Wan2.1 supported) | News 2025-06-08 | Apache-2.0 | **OK** | Less useful when stacked with 4-step LoRAs |
| SageAttention https://github.com/thu-ml/SageAttention | Quantized attention kernels (SA2/2++/SA3 for Blackwell) | SA3 code 2025-09-27 | Apache-2.0 | **OK** | README asks users to fill in a form for SA2++/SA3; no extra terms verified |
| ComfyUI-GGUF https://github.com/city96/ComfyUI-GGUF | GGUF quantized DiT loading in ComfyUI | Unverified date | Apache-2.0 | **OK** (the node runs inside GPL ComfyUI) | Good for prototyping on small GPUs |
| Nunchaku https://github.com/nunchaku-tech/nunchaku | SVDQuant 4-bit engine | v1.2.0 2026-01-12 | Apache-2.0 | OK | **No Wan support found in the README** (FLUX, Qwen-Image, Z-Image only). Not applicable yet. |
| Wan2GP / WanGP https://github.com/deepbeepmeep/Wan2GP | Low-VRAM super-app (6GB+), int8/fp8/gguf/NVFP4/Nunchaku | Active (2026 models listed) | **WanGP Community License 2.0** | **NOT OK for our backend.** It forbids "paid API / SaaS / hosted / OEM access" without a separate commercial licence. | Fine for internal R&D; outputs may be sold |
| Kijai ComfyUI-WanVideoWrapper https://github.com/kijai/ComfyUI-WanVideoWrapper | Leading-edge Wan nodes (Stand-In, MAGREF, BindWeave, HuMo, FP8) | Active (unverified date) | Apache-2.0 | OK (runs inside ComfyUI = GPL-3.0) | |
| ComfyUI https://github.com/comfyanonymous/ComfyUI | Node-graph inference server | Active | **GPL-3.0** | **OK-with-conditions** | GPL (not AGPL): server-side use without distribution has no source obligation. Do not ship it in the mobile client. |
| DiffSynth-Studio https://github.com/modelscope/DiffSynth-Studio | Wan training and inference (offload, FP8, LoRA); used by Stand-In | Active | Apache-2.0 | **OK** | |
| Diffusers https://github.com/huggingface/diffusers | Pipelines for Wan2.2 T2V/I2V/TI2V/Animate | Active | Apache-2.0 | **OK** | Preferred production path |

### 4.4 Composition

| Project | License | Verdict | Notes |
|---|---|---|---|
| FFmpeg https://github.com/FFmpeg/FFmpeg | LGPL-2.1+ by default; GPL if built with `--enable-gpl` (e.g. libx264) | **OK-with-conditions** | Server-side use is not distribution. H.264/AAC patent and pool exposure should be checked with counsel. Hardware NVENC is an option. |
| MoviePy https://github.com/Zulko/moviepy | MIT | **OK** | Python wrapper over FFmpeg; fine for overlays, text and audio |
| Remotion | Remotion License | **OK-with-conditions** | Free up to 3 employees; otherwise a Company License (Automator about $0.01/render, $100/month minimum; unverified) |
| Vanta | MIT | Reference only | See main table |

## 5. License risk register

| ID | Risk | Affects | Severity | Mitigation |
|---|---|---|---|---|
| R1 | InsightFace pretrained models (antelopev2, buffalo_l, inswapper) are **non-commercial** | Stand-In, ConsisID, Lynx, Concat-ID, FaceFusion, many ComfyUI nodes | **High** | Block `insightface` in the production lockfile. Use MediaPipe (Apache-2.0) or a self-trained or commercially licensed detector and embedder. Or buy an InsightFace commercial licence. Check whether each adapter needs ArcFace embeddings at inference time (if so, retraining is needed). |
| R2 | Wan2GP licence forbids paid SaaS/API use | Any backend built on WanGP | High | Use Diffusers, LightX2V or DiffSynth instead; WanGP only for internal experiments |
| R3 | CausVid CC BY-NC-SA (and derived LoRAs) | Speed-up LoRAs | High | Use Wan2.2-Lightning or LightX2V distills (Apache repos; verify the HF cards) |
| R4 | Hunyuan territory exclusion (EU/UK/KR) | HunyuanCustom | High | Exclude |
| R5 | Unverified weights licences on Hugging Face (DreamID-V, Stand-In, Phantom, MAGREF, BindWeave, Lynx, Lightning) | All HF-hosted checkpoints | Medium | Read each HF model card licence field before Phase 1 (HF was blocked here). Record the licence plus a commit hash in a model bill of materials. |
| R6 | Preprocessor ONNX models (DWPose `dw-ll_ucoco_384`, `yolox_l`) | DreamID-V-DWPose | Medium | Verify the licences, or use the DreamID-V-MediaPipe variant |
| R7 | Remotion company licence (more than 3 employees) plus per-render fees | Composition | Medium | MVP on FFmpeg/MoviePy; budget for Remotion only if a React template DSL is needed |
| R8 | GPL-3.0 (ComfyUI) and GPL FFmpeg builds | Inference and composition servers | Low-Medium | Keep them server-side and do not distribute binaries; prefer Diffusers in production; use an LGPL FFmpeg build if feasible |
| R9 | LTX-2.x revenue threshold ($10M) | LTX alternative | Low (now) | Re-check before scaling |
| R10 | OpenRAIL-AS use restrictions (FaceFusion), plus Wan README use restrictions | Face swap | Medium | Put the use-restriction clauses into the ToS and content policy |
| R11 | **Non-licence:** deepfake and likeness law (consent, EU AI Act transparency labelling for synthetic content, app-store policies), plus copyright of "viral templates" | Whole product | **High** | Liveness check plus consent that the face is the uploader's own; visible and invisible watermark plus C2PA metadata; use only owned or licensed template clips; NSFW and celebrity filters. Validate with counsel. |
| R12 | Fork or reupload supply-chain risk | `davahiatak1/DreamID-V-video-face-swap` | Medium | Pin to the official `bytedance/DreamID-V`; download weights only from the official HF org |

## 6. Recommended decision

**Product framing.** "Viral templates" means a fixed motion, scene and timing with the user's identity inserted. A video-to-video identity swap into a pre-made, licensed template clip is cheaper, faster and more consistent than regenerating the whole video from a prompt each time. Templates can themselves be produced offline with Wan2.2 T2V/I2V.

1. **Primary MVP model: DreamID-V (official, Wan2.1-1.3B based, Apache-2.0 code).**
   - It takes a template clip plus the user's face crop and produces the swapped clip. The template keeps its motion, camera, lighting and background.
   - The 1.3B base makes it the cheapest GPU option (16GB community port; 24GB L4/4090-class planned). It has no InsightFace in its requirements.
   - Use the MediaPipe variant first (it avoids the unverified DWPose ONNX licence).
   - Gate: before Phase 1, confirm the HF weights licence for `XuGuo699/DreamID-V`.
2. **Identity approach: template-driven face swap (DreamID-V).** It is backed by a MediaPipe-based face detection and crop step on the device or server (no InsightFace), plus a liveness and consent check. Measure identity similarity in an offline evaluation with a commercially safe embedder. Do not ship InsightFace for that purpose either.
3. **Fallback / premium tier: Wan2.2-Animate-14B, "replacement" mode (Apache-2.0, Diffusers `WanAnimatePipeline`).**
   - It replaces the whole character (body, hair, clothing) in the template video with the user from one photo.
   - It needs about 80GB (H100/A100) without optimization. Reduce this with LightX2V / Wan2.2-Lightning step distillation, FP8, and SageAttention.
   - For prompt-only templates, use Wan2.2-TI2V-5B (24GB) I2V from a user-identity first frame. The first frame comes from an Apache-licensed image identity model, to be selected in Phase 1.
   - Stand-In plus Wan2.2-T2V-A14B is the R&D candidate only after R1 is resolved.
4. **Inference stack:** Diffusers (Apache-2.0) or the native Wan/LightX2V repos, with Wan2.2-Lightning 4-step LoRAs, SageAttention and FP8. Not Wan2GP (licence). Not CausVid (NC). ComfyUI plus Kijai only for internal prototyping (GPL-3.0, server-side).
5. **Composition stack:** FFmpeg (LGPL build where possible; NVENC) plus MoviePy (MIT). These handle 9:16 crop and pad, captions, audio track, watermark and C2PA. Revisit Remotion (Company License, Automator plan) only if designers need a React template DSL and the team has more than 3 employees. Treat Vanta as a reference, not a dependency.
6. **Output spec:** 720p vertical (720x1280), 5-8 s, 16-24 fps, then interpolate or upscale in post if needed.

**Reasoning.**
- Every component in the primary path is Apache, MIT or LGPL, with no non-commercial models.
- Swapping into a fixed template gives the best quality per GPU-second at 5-10 s and keeps the template's look.
- Wan2.2 is the most active permissive base, so it is the common denominator for the fallback, template authoring and later upgrades (Stand-In-V2, Lynx and others once face-model licensing is solved).

## 7. Unverified items (must close before Phase 1)

1. Hugging Face weights licences: DreamID-V (`XuGuo699/DreamID-V`), Stand-In (`BowenXue/Stand-In`), Phantom, MAGREF, BindWeave, Lynx, HuMo, Wan2.2-Lightning / LightX2V distills. Wan2.2's own HF card was also not read; the README states Apache-2.0.
2. Official DreamID-V and Stand-In VRAM figures, and real wall-clock times on an L4, 4090 or H100.
3. Whether Stand-In uses antelopev2 only for detection and cropping, or for identity embeddings.
4. DWPose `dw-ll_ucoco_384.onnx` and `yolox_l.onnx` licences.
5. Remotion current pricing: the official pages were blocked; figures come from secondary sources.
6. Concat-ID licence; SkyReels-A2 full Skywork Community License terms; CogVideoX-5B licence (for ConsisID).
7. Exact last-commit dates for Lynx, Phantom, MAGREF, BindWeave, Kijai's WanVideoWrapper and ComfyUI-GGUF (only README news dates were observed).
8. FaceFusion per-model licences (hyperswap, simswap and others).
9. Legal: EU AI Act Art. 50 timing and scope, US state likeness and deepfake laws, app-store rules for face-swap apps. Requires counsel.
