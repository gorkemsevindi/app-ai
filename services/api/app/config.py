from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Values come from the environment (.env in local dev,
    secrets manager in staging/production). No secret has a usable default."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    env: Literal["dev", "test", "staging", "production"] = "dev"
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    jwt_secret: str = Field(default="", description="HS256 signing secret; required outside dev/test")
    jwt_issuer: str = "aivideo-api"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    apple_client_ids: list[str] = []  # bundle id / services id accepted as `aud`
    google_client_ids: list[str] = []

    # Internal worker auth (GPU workers never get DB credentials)
    worker_tokens: list[str] = []

    # Storage (S3-compatible)
    s3_endpoint_url: str | None = None
    s3_public_endpoint_url: str | None = None  # host reachable by mobile clients (e.g. MinIO in dev)
    s3_region: str = "us-east-1"
    s3_bucket_private: str = "aivideo-private"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    upload_url_ttl_s: int = 600
    download_url_ttl_s: int = 900
    cdn_base_url: str | None = None

    # Upload validation
    max_photo_bytes: int = 15 * 1024 * 1024
    max_video_bytes: int = 60 * 1024 * 1024
    min_photos_per_profile: int = 5
    max_assets_per_profile: int = 10

    # Credits / queue
    signup_bonus_credits: int = 30
    job_lease_s: int = 120
    job_max_attempts: int = 3
    queue_weights: dict[str, int] = {"paid_high": 6, "paid_normal": 3, "free": 1}
    max_active_jobs_per_user: int = 2
    global_queue_hard_limit: int = 5000

    # Cost protection
    gpu_daily_budget_usd: float = 200.0

    # Rate limits (requests per minute)
    rl_auth_per_min: int = 10
    rl_generation_per_min: int = 6

    # Billing
    revenuecat_webhook_auth: str | None = None
    # Apple: root certificates downloaded from https://www.apple.com/certificateauthority/ (DER files);
    # app_apple_id is required to accept production server notifications.
    apple_root_cert_paths: list[str] = []
    apple_app_apple_id: int | None = None
    apple_online_checks: bool | None = None  # OCSP revocation checks; default on in staging/production
    # Google Play Developer API service account (JSON key file path, from the secrets manager)
    google_service_account_file: str | None = None
    google_rtdn_token: str | None = None  # shared secret in the Pub/Sub push endpoint URL
    apple_bundle_id: str = "com.example.aivideo"
    google_package_name: str = "com.example.aivideo"

    # Sharing / deep links (V4 Stage A3)
    share_link_secret: str | None = None  # defaults to a key derived from jwt_secret
    share_base_url: str = "http://localhost:8000"  # public origin serving /t/{token} (universal/app links)
    app_scheme: str = "aivideo"
    ios_app_ids: list[str] = []  # "TEAMID.bundle.id" for apple-app-site-association
    android_sha256_fingerprints: list[str] = []  # signing cert fingerprints for assetlinks.json
    app_store_url: str | None = None
    play_store_url: str | None = None

    cors_origins: list[str] = []
    terms_version: str = "2026-10-01"

    @property
    def is_prod_like(self) -> bool:
        return self.env in ("staging", "production")

    def validate_for_runtime(self) -> None:
        if self.is_prod_like:
            missing = [n for n in ("jwt_secret",) if not getattr(self, n)]
            if len(self.jwt_secret) < 32:
                missing.append("jwt_secret(>=32 chars)")
            if not self.worker_tokens:
                missing.append("worker_tokens")
            if missing:
                raise RuntimeError(f"Missing required production settings: {missing}")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_secret and s.env in ("dev", "test"):
        s.jwt_secret = "dev-only-insecure-secret-change-me-0123456789"  # noqa: S105
    s.validate_for_runtime()
    return s
