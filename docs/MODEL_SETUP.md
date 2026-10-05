# Model setup (GPU node)

| Adapter | Upstream | Weights | Min VRAM | Env |
|---|---|---|---|---|
| `dreamid_v` | github.com/bytedance/DreamID-V (MediaPipe variant) | HF model card — **verify licence before launch** | 16 GB | `DREAMIDV_REPO`, `DREAMIDV_CKPT`, `DREAMIDV_CMD` |
| `wan22_ti2v_5b` | github.com/Wan-Video/Wan2.2 | Wan2.2-TI2V-5B (Apache-2.0) | 24 GB | `WAN22_REPO`, `WAN22_TI2V_CKPT`, `WAN22_TI2V_CMD` |
| `wan22_animate_14b` | Wan2.2 `animate-14B` replacement mode | Apache-2.0 | 48 GB (FP8/offload lower) | `WAN22_ANIMATE_CKPT`, `WAN22_ANIMATE_CMD` |
| `mp_analyzer` | ONNX person detector (RT-DETRv2 / D-FINE / YOLOX, Apache-2.0) + OpenCV Zoo YuNet (MIT) + SFace (Apache-2.0) | — | 8 GB | `MP_DETECTOR_ONNX`, `MP_YUNET_ONNX`, `MP_SFACE_ONNX`, `MP_SAFETY_CLASSIFIER` |
| `dreamid_v_mp` / `wan22_animate_mp` | per-person passes of the above | — | as above | `MP_MIN_IDENTITY_SIM` |

The `*_CMD` templates in `worker/adapters/command.py` are starting points: validate each upstream CLI at the
pinned commit and override via env. **Do not** use Wan2.2-Animate's bundled YOLOv10 detector (AGPL) or the
optional FLUX-Kontext-dev step (non-commercial); never install InsightFace model packs.

Benchmark: run each adapter on the 10 seed templates and record VRAM, GPU-seconds, failure rate,
identity similarity (SFace) and a 1–5 human score in `model_runs.metrics` (A/B script: Phase 6).
