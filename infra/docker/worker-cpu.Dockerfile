# Dev/CI worker: mock adapters + real tracking/compositing/encode (no GPU).
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN useradd -r -u 10001 worker
WORKDIR /srv
RUN pip install --no-cache-dir "httpx>=0.27" "numpy>=1.26" "opencv-python-headless>=4.10"
COPY services/worker/ ./
USER worker
ENV WORKER_MODELS=mock,mock_mp_analyzer,mock_mp WORKER_ENV=dev
CMD ["python", "-m", "worker.runner"]
