# Production GPU worker. Upstream model repos are cloned at pinned commits and their weights are
# mounted at /models (never baked into images that leave our registry). See docs/MODEL_SETUP.md.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y --no-install-recommends python3.11 python3-pip git ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -r -u 10001 worker
WORKDIR /srv
RUN pip3 install --no-cache-dir "httpx>=0.27" "numpy>=1.26" "opencv-python-headless>=4.10" "onnxruntime-gpu>=1.19"
# Model runtimes live in their own venv so upstream dependency pins never clash with ours.
RUN python3.11 -m venv /opt/modelenv && /opt/modelenv/bin/pip install --no-cache-dir \
    "torch==2.5.1" "torchvision==0.20.1" --index-url https://download.pytorch.org/whl/cu124
ARG WAN22_COMMIT=main
ARG DREAMIDV_COMMIT=main
RUN git clone https://github.com/Wan-Video/Wan2.2 /models-src/wan22 && git -C /models-src/wan22 checkout ${WAN22_COMMIT} \
    && git clone https://github.com/bytedance/DreamID-V /models-src/dreamidv && git -C /models-src/dreamidv checkout ${DREAMIDV_COMMIT} \
    && /opt/modelenv/bin/pip install --no-cache-dir -r /models-src/wan22/requirements.txt \
    && /opt/modelenv/bin/pip install --no-cache-dir -r /models-src/dreamidv/requirements.txt
COPY services/worker/ ./
USER worker
ENV WORKER_ENV=production MODEL_PYTHON=/opt/modelenv/bin/python WAN22_REPO=/models-src/wan22 \
    DREAMIDV_REPO=/models-src/dreamidv
CMD ["python3.11", "-m", "worker.runner"]
