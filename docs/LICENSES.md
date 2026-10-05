# Licence inventory

Our code: proprietary. Every dependency must be listed here before it ships. No GPL/AGPL code is linked into
our services or the app; GPL tools (if any) run only as separate processes on our servers.

## Backend / worker
| Package | Licence |
|---|---|
| FastAPI, Starlette, Pydantic | MIT |
| SQLAlchemy, Alembic | MIT |
| psycopg 3 | LGPL-3.0 (dynamically linked library, unmodified — OK) |
| PyJWT, httpx, redis-py | MIT / BSD-3 / MIT |
| boto3 / botocore | Apache-2.0 |
| NumPy | BSD-3 |
| opencv-python-headless | Apache-2.0 (bundled FFmpeg LGPL) |
| FFmpeg (system binary, LGPL build without --enable-gpl) | LGPL-2.1+ — **verify distro build flags in the GPU image** |
| onnxruntime-gpu | MIT |

## Mobile
| Package | Licence |
|---|---|
| Expo SDK modules, expo-router | MIT |
| React, React Native | MIT |
| i18next, react-i18next | MIT |

## Models (see research docs for evidence)
| Model | Code | Weights | Status |
|---|---|---|---|
| Wan2.2 (TI2V-5B, Animate-14B) | Apache-2.0 | Apache-2.0 (README) | OK — re-check model cards |
| DreamID-V | Apache-2.0 | unverified (HF blocked during research) | **blocker before launch** |
| SAM 2.1 | Apache-2.0 | Apache-2.0 | OK |
| YuNet / SFace (OpenCV Zoo) | MIT / Apache-2.0 | same | OK (SFace training data provenance unverified) |
| RT-DETRv2 / D-FINE / YOLOX | Apache-2.0 | Apache-2.0 | OK |
| Banned | InsightFace models, Ultralytics/YOLOv10 (AGPL), CodeFormer/KEEP/ProPainter/MatAnyone (S-Lab NC), FaceFusion models, Wan2GP (no SaaS), CausVid (NC) | | never ship |
