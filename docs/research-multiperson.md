# Multi-Person Viral Video Replacement: Open-Source Stack and License Research

*Research date: 2026-10-05. Scope: self-hosted, commercial SaaS, 5–20 s clips, up to 4 people, replacing the identity of selected people with temporal consistency (no per-frame face swap).*

## 1. Summary

- **No permissively licensed open model was found that does joint replacement of up to 4 people in one pass with tracking built in.** The workable design is to **track and mask each person, then run one masked diffusion replacement pass per selected person, in sequence, on the output of the previous pass**. After each pass, composite only inside that person's mask and keep everyone else's pixels unchanged.
- **Tracking backbone:** SAM 2.1 video predictor (Apache-2.0) seeded by an Apache/MIT detector (RT-DETRv2 / D-FINE / YOLOX). Use SAM 3 / SAM 3.1 (custom "SAM License", which allows commercial use but has trade-control and military restrictions) when "track every person" concept prompting and its multiplex tracking help. Add a BoT-SORT/OC-SORT-style association layer (MIT) and an appearance re-ID check to recover IDs after exit and re-entry.
- **Replacement engines:**
  - **DreamID-V** (Apache-2.0, Wan2.1-1.3B; MediaPipe variant has no InsightFace) handles face-level swaps, one pass per person.
  - **Wan2.2-Animate-14B** replacement mode (Apache-2.0) is the fallback for whole-character replacement. Its official preprocessing is **single-person only**, and its detector `yolov10m.onnx` is from **THU-MIG/yolov10 (AGPL-3.0)**. Replace that detector.
  - **New since the prior research:** SCAIL-2 (zai-org, Apache-2.0 code, 14B, replacement mode, mask-based multi-character, 2026) and MoCha (CVPR 2026, Wan2.1-14B, first-frame-mask replacement). Both are candidate adapters. Their weights licenses are **UNVERIFIED** because Hugging Face was blocked.
- **The biggest legal gap is face embeddings.** Every common face-recognition weight (InsightFace, AdaFace, facenet-pytorch VGGFace2/CASIA, EdgeFace) is trained on datasets with research-only or unclear terms. The **only clearly permissive option found is OpenCV Zoo SFace (Apache-2.0 per its directory license; training data unstated)**. YuNet (MIT) is permissive for face detection. Use SFace for ID matching and QA, treat it as "OK-with-conditions", and plan to obtain a licensed or self-trained embedder.
- **Several popular helpers are NOT OK for commercial use:**
  - S-Lab non-commercial license: MatAnyone (v1 and v2), CodeFormer, KEEP, ProPainter. DiffuEraser also inherits it, because it needs ProPainter weights as a prior.
  - GPL-3.0: RobustVideoMatting and StrongSORT.
  - AGPL-3.0: Ultralytics, YOLOv10, BoxMOT, Deep-Live-Cam and roop.
  - CC BY-NC-SA: DEVA.
  - Non-commercial weights license (believed, not fetched): FLUX.1-Kontext-dev, which the Wan-Animate preprocessing uses optionally.
  - Restricted: CanonSwap (ResearchRAIL-M) and HunyuanCustom (Tencent community license that excludes the EU, UK and South Korea).

## 2. Methodology

- Read each LICENSE file directly from `raw.githubusercontent.com` (main or master branch) for about 70 repos, and read READMEs for capabilities, dependencies and VRAM. Used web search for 2025–2026 releases.
- **Blocked:** `huggingface.co` (EGRESS_BLOCKED), `arxiv.org` (EGRESS_BLOCKED) and `api.github.com` (403 through gh). As a result:
  - Model-card weights licenses could not be read directly. Weights licenses are taken from GitHub READMEs where those state them; otherwise they are marked UNVERIFIED.
  - Exact last-commit dates were not available. "Last activity" comes from README news entries.
- Verdicts are an engineering reading, not legal advice. Any "OK-with-conditions" or "UNCLEAR" item needs counsel review.

Legend: **OK**, **OK-c** (OK with conditions), **NOT OK**, **UNCLEAR**.

## 3. Per-stage candidate tables

### 3.1 Person / face detection

| Candidate | URL | Purpose | Last activity | Code lic. | Weights lic. | Verdict | VRAM | Notes |
|---|---|---|---|---|---|---|---|---|
| Ultralytics YOLOv8/11 | github.com/ultralytics/ultralytics | person det | active | AGPL-3.0 (verified) | AGPL / paid Enterprise | **NOT OK** (without paid license) | <2 GB | AGPL network clause applies to SaaS |
| YOLOv10 (THU) | github.com/THU-MIG/yolov10 | det (used by Wan-Animate preprocessing) | 2024 | AGPL-3.0 (verified) | AGPL | **NOT OK** | <2 GB | **Must be swapped out of Wan-Animate preprocessing** |
| RT-DETR / RT-DETRv2 | github.com/lyuwenyu/RT-DETR | person det | 2024–25 | Apache-2.0 (verified) | Apache (released with repo; COCO-trained) | **OK** | 2–4 GB | Recommended primary detector |
| D-FINE | github.com/Peterande/D-FINE | person det | 2025 | Apache-2.0 (verified) | Apache (as repo) | **OK** | 2–4 GB | SOTA-class real-time DETR |
| YOLOX | github.com/Megvii-BaseDetection/YOLOX | person det | 2023 | Apache-2.0 (verified) | Apache | **OK** | <2 GB | Also used by DWPose |
| Grounding DINO | github.com/IDEA-Research/GroundingDINO | open-vocabulary det | 2024 | Apache-2.0 (verified) | Apache | **OK** | ~4–6 GB | Slower; useful for prompts like "person on left" |
| MediaPipe (BlazeFace, Face Mesh/Landmarker, Pose) | github.com/google-ai-edge/mediapipe | face det and landmarks | active | Apache-2.0 (verified) | Apache per model cards (not re-fetched) | **OK** | CPU | DreamID-V MediaPipe variant (pins mediapipe==0.10.5) |
| YuNet | github.com/opencv/opencv_zoo (face_detection_yunet) | face det | active | MIT (verified dir LICENSE) | MIT | **OK** | CPU | Weak on faces larger than about 300 px; resize input |
| SCRFD / RetinaFace (InsightFace) | github.com/deepinsight/insightface | face det | active | MIT code | **Non-commercial research only** (README, verified) | **NOT OK** | — | Includes buffalo_l and antelopev2 |
| ViTPose (whole-body) | github.com/ViTAE-Transformer/ViTPose | pose (Wan-Animate) | 2023 | Apache-2.0 (verified) | Apache (as repo; COCO-WholeBody) | **OK-c** | 2–6 GB (H) | Wan-Animate uses the vitpose_h_wholebody ONNX |
| DWPose | github.com/IDEA-Research/DWPose | pose (DreamID-V DWPose variant) | 2023 | Apache-2.0 (verified) | Apache (as repo) | **OK-c** | <2 GB | Conditions: the training-dataset terms (COCO, UBody) |
| NLF (SCAIL-2 pose) | github.com/isarandi/nlf | 3D pose | 2025 | MIT (verified) | UNVERIFIED | **UNCLEAR** | ~2–4 GB | Needed only if SCAIL-2 is adopted |

### 3.2 Tracking, re-ID and face embedding (persistent IDs)

| Candidate | URL | Purpose | Last activity | Code lic. | Weights lic. | Verdict | VRAM | Notes |
|---|---|---|---|---|---|---|---|---|
| SAM 2 / 2.1 video predictor | github.com/facebookresearch/sam2 | mask tracking with memory | 2024–25 | Apache-2.0 (verified) | Apache-2.0 (per repo) | **OK** | 4–8 GB (hiera-L, 720p, 20 s) | Core of the pipeline. Prompt with detector boxes and re-prompt on drift |
| SAM 3 / SAM 3.1 | github.com/facebookresearch/sam3 | concept-prompt det, segmentation and tracking ("person") with IDs | SAM 3: 2025-11-19; SAM 3.1 Object Multiplex: 2026-03-27 | **SAM License** (verified): royalty-free and allows commercial use; bans ITAR, military and sanctioned uses and requires legal compliance | same SAM License | **OK-c** | 848M params; VRAM UNVERIFIED (estimated 8–16 GB) | Tracks every person with IDs in one model. Requires CUDA 12.6+ and PyTorch 2.7+. Needs a legal sign-off on the custom license |
| SAMURAI | github.com/yangchris11/samurai | motion-aware SAM2 tracking | 2024–25 | Apache-2.0 (verified) | uses SAM2 weights | **OK** | like SAM2 | Better under occlusion and fast motion; single object per run |
| Cutie | github.com/hkchengrex/Cutie | VOS mask propagation | 2024 | MIT (verified) | UNVERIFIED (released with repo) | **OK-c** | ~2–4 GB | Alternative propagator |
| XMem | github.com/hkchengrex/XMem | VOS | 2023–24 | MIT (verified) | as repo | **OK-c** | ~2 GB | Older |
| DEVA | github.com/hkchengrex/Tracking-Anything-with-DEVA | open-world tracking | 2024 | **CC BY-NC-SA 4.0** (verified) | NC | **NOT OK** | — | |
| ByteTrack | github.com/ifzhang/ByteTrack | box MOT | 2022 | MIT (verified) | YOLOX-based | **OK** | — | Association only |
| BoT-SORT | github.com/NirAharon/BoT-SORT | MOT with camera-motion compensation and re-ID | 2023 | MIT (verified) | re-ID weights on MOT17/20 (dataset terms) | **OK-c** | — | Camera-motion compensation suits viral handheld clips |
| OC-SORT | github.com/noahcao/OC_SORT | MOT robust to occlusion | 2023 | MIT (verified) | — | **OK** | — | |
| Deep-OC-SORT | github.com/GerardMaggiolino/Deep-OC-SORT | OC-SORT with appearance | 2023 | MIT (verified) | re-ID weights: dataset terms | **OK-c** | — | |
| BoxMOT | github.com/mikel-brostrom/boxmot | tracker zoo | active | **AGPL-3.0** (verified) | — | **NOT OK** | — | Use the upstream MIT repos instead |
| StrongSORT | github.com/dyhBUPT/StrongSORT | MOT | 2023 | **GPL-3.0** (verified) | — | **NOT OK** (risky for SaaS; avoid) | — | |
| MOTIP | github.com/MCG-NJU/MOTIP | end-to-end ID prediction | 2025 | Apache-2.0 (verified) | trained on DanceTrack/SportsMOT (dataset terms UNVERIFIED) | **OK-c** | ~8 GB | Research-grade |
| torchreid / OSNet | github.com/KaiyangZhou/deep-person-reid | body re-ID embedding | 2023 | MIT (verified) | Market-1501/MSMT17 weights: datasets research-only (UNVERIFIED per dataset) | **UNCLEAR** | <1 GB | Code fine; retrain on licensed data or use SAM masks plus SFace instead |
| **SFace (OpenCV Zoo)** | github.com/opencv/opencv_zoo (face_recognition_sface) | face embedding | active | Apache-2.0 (verified dir LICENSE) | Apache-2.0 (directory LICENSE covers model files) | **OK-c** | CPU | MobileFaceNet; training data not stated in the README. **Best permissive option found.** Lower accuracy than ArcFace |
| AdaFace | github.com/mk-minchul/AdaFace | face embedding | 2023 | MIT (verified) | trained on MS1MV2/MS1MV3/WebFace4M/CASIA (research-only datasets) | **UNCLEAR → treat as NOT OK** | <1 GB | |
| EdgeFace | github.com/otroshi/edgeface | face embedding | 2024 | BSD-3 (verified) | training data and weights terms UNVERIFIED (Idiap) | **UNCLEAR** | CPU | Ask Idiap |
| facenet-pytorch | github.com/timesler/facenet-pytorch | face embedding and MTCNN | 2023 | MIT (verified) | VGGFace2 and CASIA-WebFace weights (dataset licensing contested or withdrawn) | **UNCLEAR** | <1 GB | Avoid for production |
| InsightFace ArcFace (buffalo_l, antelopev2) | github.com/deepinsight/insightface | face embedding | active | MIT | **non-commercial** (verified) | **NOT OK** | — | A commercial license is sold by InsightFace |
| DeepFace (wrapper) | github.com/serengil/deepface | wrapper | active | MIT (verified) | per wrapped model | inherits | — | Not a solution by itself |

### 3.3 Segmentation and matting (compositing and occlusion)

| Candidate | URL | Code lic. | Weights lic. | Verdict | Notes |
|---|---|---|---|---|---|
| SAM 2.1 | facebookresearch/sam2 | Apache-2.0 | Apache-2.0 | **OK** | Per-person masks and person-on-person occlusion order |
| BiRefNet | github.com/ZhengPeng7/BiRefNet | MIT (verified) | MIT per repo (HF not checked) | **OK-c** | High-resolution edge refinement per frame; may flicker, so smooth temporally |
| MatAnyone / MatAnyone2 | github.com/pq-yang/MatAnyone | **S-Lab NC** (verified) | NC | **NOT OK** | |
| RobustVideoMatting | github.com/PeterL1n/RobustVideoMatting | **GPL-3.0** (verified) | GPL | **NOT OK** (risky; avoid) | |
| face-parsing.PyTorch (BiSeNet) | github.com/zllrunning/face-parsing.PyTorch | MIT (verified) | trained on CelebAMask-HQ (non-commercial dataset) | **UNCLEAR → avoid** | Use MediaPipe Face Mesh polygons for the face, hair and mouth masks instead |
| MediaPipe Face Landmarker / Selfie segmentation | google-ai-edge/mediapipe | Apache-2.0 | Apache | **OK** | Face-region mask and landmarks for QA |
| SAM-HQ | github.com/SysCV/sam-hq | Apache-2.0 (verified) | as repo | **OK-c** | Image-only refinement |

### 3.4 Identity replacement / character replacement

Column "Multi-ID" says whether the model handles several identities: natively, or only through sequential masked passes.

| Candidate | URL | Base | Last activity | Code lic. | Weights lic. | Multi-ID | Verdict | VRAM | Notes |
|---|---|---|---|---|---|---|---|---|---|
| **DreamID-V** | github.com/bytedance/DreamID-V | Wan2.1-1.3B | Code 2026-01-05; DWPose variant 01-10; Faster variant 01-12 | Apache-2.0 (verified) | Weights on HF XuGuo699/DreamID-V; license UNVERIFIED (HF blocked); Wan 1.3B is Apache | Single face in the docs. Multi-person = **sequential per-track passes** with face crops and masks | **OK-c** (confirm HF weights license) | 16 GB with the community port; official VRAM not stated | 832×480 and 1280×720; requirements list mediapipe and no insightface. "Faster" variant has lower VRAM |
| **Wan2.2-Animate-14B** (replacement mode) | github.com/Wan-Video/Wan2.2 | Wan2.2 14B | 2025-09-19; Diffusers 2025-11-13 | Apache-2.0 (verified) | Apache-2.0 (README) | Official preprocessing is "single-person videos ONLY" (verified). Multi-person = custom per-person masks plus sequential passes | **OK-c**: replace the YOLOv10 (AGPL) detector and do not use FLUX-Kontext-dev | Same family as the 80 GB single-GPU guidance for 14B; 24–48 GB with offload and FP8 (estimate) | Uses ViTPose and SAM2 for masks. Whole-body replacement; may alter clothing and body shape |
| **SCAIL-2** | github.com/zai-org/SCAIL-2 | 14B (Wan VAE and T5 bundled) | Multi-reference 2026-06-13; relight LoRA 07-15; training code 08-06 | Apache-2.0 (verified) | HF zai-org/SCAIL-2: UNVERIFIED | Color-coded multi-character masks (animation). Replacement supports `--matchnearest` to pick one of several people (verified README) | **UNCLEAR** (weights) | ~14B class (estimate 40–80 GB) | Optional Gemini prompt enhancer is an external API (not required). Relighting LoRA helps scene-lighting match |
| **MoCha** | github.com/Orange-3DV-Team/MoCha (code in MoCha-Code) | Wan2.1-T2V-14B | Code 2025-10-21; CVPR 2026 | UNVERIFIED (no LICENSE at repo root) | HF: UNVERIFIED | Single character per run using a first-frame mask; multiple reference images | **UNCLEAR** | 14B class | No pose needed, so it handles occlusion well. Also has a ComfyUI path via kijai WanVideoWrapper (avoid GPL-3 ComfyUI in-process, or isolate it) |
| Wan2.1-VACE-1.3B / 14B (MV2V masked editing) | github.com/ali-vilab/VACE | Wan2.1 | 2025-05-14 | Apache-2.0 (verified) | Apache-2.0 (README table) | Masked inpainting plus reference image, run per person | **OK** | 1.3B: about 8–12 GB; 14B: about 40–80 GB (estimate) | Good generic masked-video identity inpaint and repair fallback |
| MultiAnimate | github.com/hyc001/MultiAnimate | Wan2.1-I2V-14B | CVPR 2026 | Apache-2.0 (verified) | UNVERIFIED | Native multi-character (3, or up to 7) via masks | **UNCLEAR** | 14B | **Animation (image to video), not replacement.** Background is regenerated, so it does not fit this product |
| UniAnimate-DiT | github.com/ali-vilab/UniAnimate-DiT | Wan2.1-14B | 2025-04 | UNVERIFIED (no root LICENSE found) | UNVERIFIED | No | **UNCLEAR** | — | About 3 min for 5 s at 480p and about 13 min for 5 s at 720p on A800 with TeaCache (README) |
| Phantom | github.com/Phantom-video/Phantom | Wan | 2025 | Apache-2.0 (verified) | UNVERIFIED | Multi-subject reference-to-video | **OK-c** | 14B | Generation, not replacement |
| MAGREF | github.com/MAGREF-Video/MAGREF | Wan2.1 | 2025 | Apache-2.0 (verified) | UNVERIFIED | Multi-subject reference generation with masks | **OK-c** | 14B | Generation, not replacement |
| BindWeave / HuMo / Lynx / Stand-In | bytedance/BindWeave, Phantom-video/HuMo, bytedance/lynx, WeChatCV/Stand-In | Wan | 2025 | Apache-2.0 (all verified) | UNVERIFIED | BindWeave: multi-subject. Others: single-ID | **OK-c** | 14B (Stand-In is a lightweight adapter) | ID-preserving generation; useful for research into identity adapters, not direct replacement |
| SkyReels-A2 | github.com/SkyworkAI/SkyReels-A2 | — | 2025 | Skywork Community License (verified: commercial use allowed under its terms) | same | Multi-element | **OK-c** | 14B | Read the full PDF terms |
| HunyuanCustom | github.com/Tencent-Hunyuan/HunyuanCustom | Hunyuan | 2025-05 | Tencent Hunyuan Community License (verified) | same | Multi-subject | **NOT OK for global SaaS** | 24–80 GB | License excludes the EU, UK and South Korea |
| VFace (training-free) | github.com/Sanoojan/VFace | Image diffusion face swapper | WACV 2026 | MIT (verified) | depends on the underlying swapper (likely uses an ID encoder) | Per-face | **UNCLEAR** | — | Adds flow-guided temporal smoothing on top of an image swapper |
| CanonSwap | github.com/luoxyhappy/CanonSwap | — | 2025 | **ResearchRAIL-M** (verified) | research | — | **NOT OK** | | |
| FaceFusion | github.com/facefusion/facefusion | per-frame swapper | active (2026) | OpenRAIL-AS (verified) | Bundles InsightFace-derived models (non-commercial) | Multi-face per frame | **NOT OK** as a product core (frame-by-frame plus NC models) | | |
| Deep-Live-Cam / roop | hacksider/Deep-Live-Cam, s0md3v/roop | per-frame inswapper | — | **AGPL-3.0** (verified) | inswapper (NC) | Multi-face | **NOT OK** | | |
| DynamicFace / HiFiVFS / VividFace / Vorch-IR / DreamActor-M2 | — | — | 2025–26 | No public code or license found (VividFace, DynamicFace repo not found; Vorch-IR is a paper, arXiv 2608.05648, with dual-person replacement) | — | Vorch-IR: dual-person natively | **UNCLEAR** (not released or not verifiable) | | Watch Vorch-IR for native two-person replacement |

### 3.5 Temporal consistency, deflicker and face restoration

| Candidate | URL | Code lic. | Weights | Verdict | Notes |
|---|---|---|---|---|---|
| All-In-One-Deflicker | github.com/ChenyangLEI/All-In-One-Deflicker | Apache-2.0 (verified) | as repo | **OK-c** | Per-video optimisation (slow, minutes). Use it only for the masked face region if needed |
| TokenFlow | github.com/omerbt/TokenFlow | MIT (verified) | uses SD (CreativeML OpenRAIL-M) | **OK-c** | Editing method; mostly superseded by the video diffusion approaches |
| KEEP | github.com/jnjaby/KEEP | **S-Lab NC** (verified) | NC | **NOT OK** | |
| CodeFormer | github.com/sczhou/CodeFormer | **S-Lab NC** (verified) | NC | **NOT OK** | |
| GFPGAN | github.com/TencentARC/GFPGAN | Apache-2.0 **except third-party parts**: includes NVIDIA StyleGAN2 Source Code License components (verified in LICENSE) | v1.3/1.4 weights: UNVERIFIED | **OK-c / UNCLEAR** | Use the "clean" architecture (no custom CUDA ops) and get legal review. Per-frame, so it can flicker; use only lightly with temporal blending |
| Real-ESRGAN | github.com/xinntao/Real-ESRGAN | BSD-3 (verified) | as repo | **OK** | Background/general upscale only |
| PGTFormer, BFVR | — | not verified | — | **UNCLEAR** | Not checked in this pass |
| **Preferred approach** | — | — | — | — | The temporal consistency comes from the video-diffusion replacer itself (DreamID-V / Wan), plus latent-space overlap blending between windows, plus mask feathering. Avoid per-frame restorers |

### 3.6 Video inpainting (occlusion edge cases and background leaks)

| Candidate | URL | Code lic. | Weights | Verdict | Notes |
|---|---|---|---|---|---|
| ProPainter | github.com/sczhou/ProPainter | **S-Lab NC** (verified) | NC | **NOT OK** | |
| DiffuEraser | github.com/lixiaowen-xw/DiffuEraser | Apache-2.0, **but requires ProPainter.pth as a prior** (README, verified) plus SD1.5 (OpenRAIL-M) | NC via ProPainter | **NOT OK as shipped** (OK-c only with the prior swapped) | |
| Wan2.1-VACE (MV2V inpaint) | ali-vilab/VACE | Apache-2.0 | Apache-2.0 | **OK** | Recommended inpainter |
| MiniMax-Remover | github.com/zibojia/MiniMax-Remover | **UNVERIFIED** (no LICENSE file found) | UNVERIFIED | **UNCLEAR** | Wan-VAE DiT; fast, with few steps |

### 3.7 Output QA

| Metric | Tool | License | Verdict |
|---|---|---|---|
| Identity similarity (reference vs. output, per track, per frame) | SFace (OpenCV Zoo) cosine similarity. Optionally a licensed ArcFace for internal R&D only | Apache-2.0 | **OK-c** |
| Identity leakage (output face vs. the original person, and vs. other assigned people) | same | | Catches identity swaps between people |
| Flicker / warping error | RAFT optical flow (github.com/princeton-vl/RAFT), warping error inside the face mask | BSD-3 (verified) | **OK** |
| Landmark / lip jitter | MediaPipe Face Landmarker: high-frequency energy of the landmark trajectories, and mouth-open correlation with the source | Apache-2.0 | **OK** |
| Mask leakage | Pixel difference outside the union of masks (must be about 0 after compositing) | — | **OK** |
| Track QA | Per-track ID-switch count, coverage, and mean SFace similarity within the track | — | **OK** |

## 4. License risk register

| # | Item | Risk | Severity | Mitigation |
|---|---|---|---|---|
| 1 | InsightFace models (inswapper, buffalo_l, antelopev2, SCRFD) | NC weights | High | Exclude. Grep dependency trees, including transitive dependencies of FaceFusion-like and VFace-like tools |
| 2 | Face-recognition embeddings in general (AdaFace, facenet, EdgeFace, OSNet) | Weights trained on research-only datasets | High | Use SFace (Apache) for now; buy a commercial FR license or train on licensed or consented data |
| 3 | Wan-Animate preprocessing uses YOLOv10 (AGPL-3.0) | AGPL in the SaaS path | High | Replace with RT-DETRv2 / D-FINE / YOLOX feeding ViTPose |
| 4 | FLUX.1-Kontext-dev (optional Wan-Animate retargeting) | NC weights license (believed; not fetched because HF was blocked) | High | Do not install. Not needed for replacement mode |
| 5 | Ultralytics, BoxMOT, Deep-Live-Cam, roop | AGPL-3.0 | High | Exclude |
| 6 | StrongSORT, RobustVideoMatting | GPL-3.0 | Medium | Exclude |
| 7 | MatAnyone(2), CodeFormer, KEEP, ProPainter, DiffuEraser (prior) | S-Lab NC | High | Exclude |
| 8 | DEVA | CC BY-NC-SA | High | Exclude |
| 9 | HunyuanCustom | Territory exclusion (EU, UK, KR) | High | Exclude for a global app |
| 10 | SAM 3 / 3.1 SAM License | Custom terms: trade controls, no military or ITAR use | Low–Medium | Legal review; SAM 2.1 (Apache) is the fallback |
| 11 | GFPGAN | NVIDIA StyleGAN2 NC components in the third-party section | Medium | Avoid, or use clean architecture after legal review |
| 12 | DreamID-V, SCAIL-2, MoCha, MAGREF, Phantom HF weights | Weights licenses UNVERIFIED (HF blocked) | Medium | Check model cards before launch; DreamID-V first |
| 13 | ComfyUI (GPL-3) and kijai wrappers | GPL; fine server-side, but avoid linking it into distributed code | Low | Use native Diffusers or repo code paths |
| 14 | Wan2GP | License forbids SaaS (prior finding) | High | Exclude |
| 15 | Face-mask polygons from BiSeNet (CelebAMask-HQ) | Dataset NC | Medium | Use MediaPipe landmark polygons |
| 16 | Dataset terms for COCO, MOT and DanceTrack-trained detectors and trackers | Generally permissive or unclear | Low | Note them in the SBOM |

## 5. GPU estimates

These are rough figures. Only the starred figures come from official READMEs; everything else is an extrapolation and should be benchmarked.

**Anchors:**
- *Wan2.1-1.3B: 8.19 GB VRAM; a 5 s 480p clip takes about 4 min on an RTX 4090 without optimisation (Wan2.1 README).
- *UniAnimate-DiT (Wan2.1-14B) with TeaCache: about 3 min for 5 s at 480p, about 13 min for 5 s at 720p on an A800.
- *Wan2.2 14B models: 80 GB for single-GPU 720p without offload.
- DreamID-V reports a 1x speed-up (2x faster) for its Faster variant, and fewer steps (20) for simple scenes.

Seconds of GPU time per **1 s of output video, per replaced person**:

| Stage | VRAM | 4090 (24 GB) | L40S (48 GB) | A100 80 GB | H100 80 GB |
|---|---|---|---|---|---|
| Detection, tracking and masks (RT-DETR + SAM 2.1-L; whole clip, all people) | 6–10 GB | ~2–4 s | ~2–3 s | ~1.5–2.5 s | ~1–2 s |
| DreamID-V 1.3B, 480p (Faster variant) | ~10–16 GB | ~25–50 s | ~20–40 s | ~15–30 s | ~8–18 s |
| DreamID-V 1.3B, 720p | ~16–24 GB | ~90–150 s (borderline VRAM) | ~60–110 s | ~45–80 s | ~25–45 s |
| Wan2.2-Animate-14B, 480p | 40–80 GB (FP8 or offload below that) | impractical (heavy offload; over 300 s) | ~150–250 s (FP8) | ~100–180 s | ~50–100 s |
| Wan2.2-Animate-14B, 720p | 80 GB | no | marginal | ~350–600 s | ~180–300 s |
| VACE-1.3B repair inpaint (small region) | ~10 GB | ~20–40 s | ~15–30 s | ~12–25 s | ~7–15 s |
| QA (SFace, RAFT, MediaPipe) | <4 GB | ~2–5 s | ~2–4 s | ~2–3 s | ~1–2 s |

**Scaling with the number of people:**
- Generation cost grows about **linearly with the number of people replaced (N)**, because each person is one masked pass.
- Tracking and QA are about constant per clip.
- Cropping each person's face or body region to a tight padded crop before generation cuts cost. For example, a crop of about 512×512 instead of the full 720p frame saves roughly 2–3x.
- Example: a 15 s clip at 480p on H100 with DreamID-V and 4 people costs about 4 × 15 × ~13 s ≈ 13 min of GPU time. With 1 person it is about 3–4 min.
- Ways to cut wall-clock time:
  - Run the N passes on N GPUs in parallel when the masks do not overlap, then composite.
  - Run them sequentially when the people overlap or occlude each other, so later passes see the earlier results.
  - Use xDiT USP multi-GPU (supported by DreamID-V).
  - Use step distillation, if it becomes available.

## 6. Recommended pipeline

```
Upload ─► S0 Ingest (ffmpeg normalise fps/res, scene-cut check, ≤20 s)
       ─► S1 Detect: RT-DETRv2 or D-FINE "person" boxes every k frames (+ YuNet/MediaPipe faces)
       ─► S2 Track: SAM 2.1 video predictor seeded by boxes (one object id per person)
            + OC-SORT/BoT-SORT box association (camera-motion compensated) to re-seed on loss
            + Re-entry: SFace face embedding + mask-colour/shape histogram gallery per track → merge IDs
            (Option: SAM 3.1 "person" concept tracking as alternative tracker; A/B test)
            → stable Person 1..4, per-frame masks, depth order (mask overlap + box bottom y)
       ─► S3 UI: user picks Person k ↔ Identity Profile (5–10 consented photos)
       ─► S4 Reference prep: MediaPipe face align, choose 1–3 best frontal crops (SFace quality score)
       ─► S5 Replace, per selected person i (sequential by depth: farthest first):
            crop tube around track i (padded, temporally smoothed bbox) →
            PRIMARY  DreamID-V (MediaPipe or Faster variant) on the face/head tube
            FALLBACK Wan2.2-Animate-14B replacement (custom per-person mask; detector swapped to RT-DETR)
            ALT/EVAL MoCha / SCAIL-2 adapters (pending weights-license check)
            → paste back with feathered SAM mask ∩ (not occluder masks of people in front)
       ─► S6 Repair: VACE-1.3B masked inpaint for seams / occlusion holes (only if QA flags)
       ─► S7 QA gate: SFace ID-sim ≥ τ per track, no cross-ID leakage, RAFT warp error in face mask,
            landmark jitter, outside-mask diff ≈ 0 → auto-retry (new seed / fallback adapter) or fail
       ─► S8 Encode (keep original audio), watermark/C2PA provenance
```

**Key design decisions:**
- **Single-person fast path:** if one person is selected and that person's track covers more than 90% of frames with no overlap, skip ordering and run one DreamID-V pass at 480p, upscaling the face only if needed.
- **Handling N people:** use **sequential masked passes** as the default. They are the only approach that is verified with today's licensed models.
  - Joint multi-reference models (SCAIL-2 multi-reference, MultiAnimate, Vorch-IR dual-person) are worth tracking. SCAIL-2 is the most promising Apache-coded candidate.
  - When two people overlap in the same frames, process the rear person first, and composite the front person's original or replaced pixels over the result using SAM depth order.
- **Temporal consistency:**
  - Generate each person's full tube in one diffusion window where possible: 5–20 s is about 81–321 frames at 16 fps.
  - For longer tubes, use overlapping windows with latent blending, and reuse the same reference and seed per track.
  - Never run a per-frame restorer afterwards.
- **Adapter interface:** `replace(video_tube, mask_tube, refs[], pose?) -> tube`. Implement it for DreamID-V, Wan-Animate, VACE, and later MoCha / SCAIL-2, so that engines can be A/B tested by QA score.
- **Infrastructure:**
  - Tracking runs on a small GPU (L4/4090).
  - Generation runs on L40S, or on H100 for 720p.
  - Use one queue job per person-pass, so the cost per job scales with N.

## 7. Unverified items and next checks

1. Hugging Face model-card licenses (HF was blocked) for: DreamID-V (XuGuo699/DreamID-V), SCAIL-2, MoCha, MAGREF, Phantom, BindWeave, Wan2.2-Animate (the README says Apache-2.0), FLUX.1-Kontext-dev (believed NC), SAM 2.1 checkpoints, and the BiRefNet weights.
2. MoCha, MoCha-Code, UniAnimate-DiT and MiniMax-Remover: no root LICENSE file was found; check the GitHub license badge or sidebar.
3. Whether DreamID-V handles profile views, occlusion, and multiple faces in one frame when given a tight crop and mask. **Benchmark this on internal clips.**
4. The real VRAM and speed of DreamID-V at 480p and 720p on L40S and H100. The 16 GB figure comes from the community ComfyUI port.
5. SAM 3 / 3.1 VRAM and speed for 4-person 720p, 20 s clips; legal acceptance of the SAM License.
6. SFace training data provenance (not stated in the OpenCV Zoo README). Its accuracy on profile and blurred faces may be too weak for re-entry ID merging, so pair it with mask/appearance cues.
7. GFPGAN weights terms; and the PGTFormer, BFVR and Lynx weights.
8. The Vorch-IR (arXiv 2608.05648) and GroupVideo code releases (arXiv was blocked).
9. Exact last-commit dates (the GitHub API was blocked); dates above come from README news entries.
10. Every GPU-time figure in Section 5 that is not starred is an estimate.
