FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" "sqlalchemy>=2" "psycopg[binary]" alembic pydantic-settings \
    pyjwt bcrypt httpx python-multipart email-validator numpy pillow "anthropic>=1.0"
COPY backend/ ./
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
ENV DRAMA_STORAGE_DIR=/data/storage DRAMA_ENV=production
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn dramaapp.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
