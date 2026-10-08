# CPU worker for the local pipeline + ffmpeg assembly. GPU/provider workers reuse this image with extra deps.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir "sqlalchemy>=2" "psycopg[binary]" pydantic-settings pyjwt bcrypt numpy pillow fastapi \
    email-validator "anthropic>=1.0"
COPY backend/ ./
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
ENV DRAMA_STORAGE_DIR=/data/storage
CMD ["python", "-m", "dramaapp.worker"]
