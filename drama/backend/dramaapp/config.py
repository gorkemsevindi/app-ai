"""Runtime configuration. Every commercial decision (prices, revenue share, providers) is a setting,
never a constant buried in code. Values come from env vars prefixed DRAMA_ (see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRAMA_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "sqlite:///./drama-dev.db"
    jwt_secret: str = "dev-only-insecure-jwt-secret-change-me-0123456789"
    media_signing_secret: str = "dev-only-insecure-media-secret-change-me"
    access_token_minutes: int = 60 * 24
    storage_dir: Path = Path("./var/storage")
    public_base_url: str = "http://localhost:8000"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- provider routing (all switchable without code changes) ---------------------------------
    llm_provider: str = "local_template"  # local_template | anthropic
    anthropic_model: str = "claude-opus-5-5"
    tts_provider: str = "espeak_local"  # espeak_local | elevenlabs (adapter stub until key+contract)
    music_provider: str = "procedural_local"
    video_provider: str = "studio_preview_local"  # local 2D performance renderer
    lipsync_provider: str = "viseme_local"

    # --- generation budget / abuse controls ---------------------------------------------------------
    max_render_retries: int = 2
    daily_spend_cap_credits: int = 5_000  # per creator account, credits
    signup_bonus_credits: int = 1_000  # free trial (commercial decision: confirm)
    job_lease_seconds: int = 300
    # platform render fee on top of provider pass-through cost (commercial decision: confirm)
    render_credits_per_min_preview: int = 5
    render_credits_per_min_final: int = 20

    # --- monetization policy (commercial decisions; defaults from spec, require owner approval) ------
    currency: str = "USD"
    default_free_episodes: int = 5
    default_episode_price_minor: int = 99
    default_bundle5_price_minor: int = 399
    default_season_price_minor: int = 999
    creator_share_bps: int = 6000  # 60% of distributable net
    store_fee_bps: dict[str, int] = Field(
        default_factory=lambda: {"apple": 1500, "google": 1500, "stripe": 290, "sandbox": 1500}
    )
    indirect_tax_bps: int = 0  # set per region once tax advice is in; prices assumed tax-inclusive
    earnings_hold_days: int = 30  # refund/chargeback window before earnings become available
    min_payout_minor: int = 5000
    sandbox_store_secret: str = "dev-sandbox-store-secret"

    # --- publishing / safety -----------------------------------------------------------------------
    manual_publish_gate: bool = True
    preview_height: int = 640
    final_height: int = 1280


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    return s
