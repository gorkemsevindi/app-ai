FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -r -u 10001 app
WORKDIR /srv
COPY services/api/pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0" "psycopg[binary]>=3.2" \
    "alembic>=1.13" "pydantic-settings>=2.4" "pyjwt[crypto]>=2.9" "boto3>=1.35" "redis>=5.0" "httpx>=0.27" \
    "python-multipart>=0.0.9" "email-validator>=2.2"
COPY services/api/ ./
USER app
EXPOSE 8000
# Migrations run as a separate release step (see DEPLOYMENT.md), never on every replica start.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--workers", "2"]
