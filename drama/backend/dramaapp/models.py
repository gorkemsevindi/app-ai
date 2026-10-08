"""Core data model (spec §8). Portable across PostgreSQL (prod) and SQLite (tests).

Design notes (see docs/adr/0003-data-model.md):
- Script/scene/shot structure is stored as a versioned JSON document per episode (ScriptVersion);
  the renderer derives Scenes/Shots from it. Normalise later if editing tools need row-level locks.
- Money is integer minor units + ISO currency. Credits use currency "CRD".
- Ledger is append-only double-entry: LedgerTransaction (idempotency_key UNIQUE) + LedgerEntry rows
  that sum to zero per currency. Balances are always derived from entries.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid() -> str:
    return uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(UTC)


class TS:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---------------------------------------------------------------------------------------- identity
class User(Base, TS):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # viewer|creator|admin
    locale: Mapped[str] = mapped_column(String(8), default="tr")
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreatorProfile(Base, TS):
    __tablename__ = "creator_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    handle: Mapped[str] = mapped_column(String(40), unique=True)
    bio: Mapped[str] = mapped_column(Text, default="")
    kyc_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|verified|rejected
    tax_form_status: Mapped[str] = mapped_column(String(16), default="none")
    payout_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    daily_spend_cap_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------------------- catalog
class Series(Base, TS):
    __tablename__ = "series"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    logline: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(32), default="drama")
    language: Mapped[str] = mapped_column(String(8), default="tr")
    age_rating: Mapped[str] = mapped_column(String(8), default="13+")
    visual_style: Mapped[str] = mapped_column(String(32), default="cinematic")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published|taken_down
    bible: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # wizard inputs, budget, episode duration
    pricing: Mapped[dict] = mapped_column(JSON, default=dict)  # per-series monetization override
    cover_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regions_blocked: Mapped[list] = mapped_column(JSON, default=list)
    rights_expire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_disclosure: Mapped[dict] = mapped_column(JSON, default=dict)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seasons: Mapped[list["Season"]] = relationship(back_populates="series", order_by="Season.number")


class Season(Base, TS):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("series_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(160), default="")
    series: Mapped[Series] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="season", order_by="Episode.number")


class Episode(Base, TS):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), index=True)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    synopsis: Mapped[str] = mapped_column(Text, default="")
    # draft|rendering|rendered|in_review|approved|published|rejected|taken_down
    status: Mapped[str] = mapped_column(String(16), default="draft")
    target_duration_s: Mapped[int] = mapped_column(Integer, default=60)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_script_version: Mapped[int] = mapped_column(Integer, default=0)
    video_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # final mp4
    hls_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    captions_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    qc_report: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)  # models, versions, rights, AI label
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    season: Mapped[Season] = relationship(back_populates="episodes")


class ScriptVersion(Base, TS):
    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("episode_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(32))  # llm provider id or "manual"
    content: Mapped[dict] = mapped_column(JSON)  # {"scenes":[{"location","mood","lines":[...]}]}


# ---------------------------------------------------------------------------------------- characters
class Character(Base, TS):
    __tablename__ = "characters"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40), default="supporting")
    personality: Mapped[str] = mapped_column(Text, default="")
    # Character DNA: look (skin/hair/eyes/outfit/…), voice profile, wardrobe, relationships
    dna: Mapped[dict] = mapped_column(JSON, default=dict)
    voice: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    likeness_source: Mapped[str] = mapped_column(String(16), default="fictional")  # fictional|real_person
    rights_grant_id: Mapped[str | None] = mapped_column(ForeignKey("rights_grants.id"), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CharacterVersion(Base, TS):
    __tablename__ = "character_versions"
    __table_args__ = (UniqueConstraint("character_id", "version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    character_id: Mapped[str] = mapped_column(ForeignKey("characters.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)


# ---------------------------------------------------------------------------------------- rights
class RightsGrant(Base, TS):
    """Recorded, revocable authorization to use a real person's face/voice (spec §3 face swap)."""

    __tablename__ = "rights_grants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    grantee_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # creator using it
    subject_name: Mapped[str] = mapped_column(String(120))
    subject_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    grant_type: Mapped[str] = mapped_column(String(24))  # self|licensed_actor|authorized_upload
    scope: Mapped[dict] = mapped_column(JSON, default=dict)  # {"face":bool,"voice":bool,"series_id":..}
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)  # consent text hash, asset ids, ip, ua
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|approved|rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------------------- assets / jobs
class Asset(Base, TS):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # video|hls|audio|image|captions|character_ref|...
    storage_key: Mapped[str] = mapped_column(String(400))
    mime: Mapped[str] = mapped_column(String(80))
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # incl. provenance + synthetic label
    character_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class GenerationJob(Base, TS):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    episode_id: Mapped[str | None] = mapped_column(ForeignKey("episodes.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # episode_render
    quality: Mapped[str] = mapped_column(String(8), default="preview")  # preview|final
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued|running|succeeded|failed|cancelled|needs_review
    priority: Mapped[int] = mapped_column(Integer, default=100)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    estimate_credits: Mapped[int] = mapped_column(Integer, default=0)
    max_spend_credits: Mapped[int] = mapped_column(Integer, default=0)
    spent_credits: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    steps: Mapped[list["JobStep"]] = relationship(order_by="JobStep.seq", cascade="all, delete-orphan")


class JobStep(Base, TS):
    """One idempotent, resumable pipeline step with its input/output manifest (spec §4)."""

    __tablename__ = "job_steps"
    __table_args__ = (UniqueConstraint("job_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|failed|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_credits: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderCall(Base, TS):
    __tablename__ = "provider_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capability: Mapped[str] = mapped_column(String(16))  # llm|tts|image|video|lipsync|music|asr
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    model_version: Mapped[str] = mapped_column(String(40), default="")
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    units: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    seed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ---------------------------------------------------------------------------------------- social
class WatchEvent(Base, TS):
    __tablename__ = "watch_events"
    __table_args__ = (Index("ix_watch_ep_user", "episode_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)  # client-generated, dedup
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    anon_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    position_s: Mapped[float] = mapped_column(Float)
    watched_s: Mapped[float] = mapped_column(Float, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)


class Follow(Base, TS):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("user_id", "series_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id"))


class Comment(Base, TS):
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)


class Report(Base, TS):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    reporter_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_type: Mapped[str] = mapped_column(String(16))  # series|episode|comment|user|character
    target_id: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(32))  # impersonation|nonconsensual|minor_safety|ip|spam|...
    details: Mapped[str] = mapped_column(Text, default="")
    case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ModerationCase(Base, TS):
    __tablename__ = "moderation_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16))  # report|auto|rights|appeal
    priority: Mapped[int] = mapped_column(Integer, default=50)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|actioned|dismissed|appealed
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


# ---------------------------------------------------------------------------------------- commerce
class Purchase(Base, TS):
    __tablename__ = "purchases"
    __table_args__ = (UniqueConstraint("store", "store_transaction_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    store: Mapped[str] = mapped_column(String(16))  # apple|google|stripe|sandbox
    store_transaction_id: Mapped[str] = mapped_column(String(128))
    product_type: Mapped[str] = mapped_column(String(16))  # episode|bundle5|season|credits|subscription
    series_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    episode_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gross_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="verified")  # verified|refunded
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Entitlement(Base, TS):
    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("user_id", "episode_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    series_id: Mapped[str] = mapped_column(String(32))
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id"))
    purchase_id: Mapped[str | None] = mapped_column(ForeignKey("purchases.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(16))  # purchase|subscription|grant
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LedgerTransaction(Base, TS):
    __tablename__ = "ledger_transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    memo: Mapped[str] = mapped_column(Text, default="")
    ref: Mapped[dict] = mapped_column(JSON, default=dict)
    reverses_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entries: Mapped[list["LedgerEntry"]] = relationship(cascade="all, delete-orphan")


class LedgerEntry(Base, TS):
    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_account", "account", "currency"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    txn_id: Mapped[str] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)  # +debit/-credit convention: see ledger.py
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RevenueAllocation(Base, TS):
    __tablename__ = "revenue_allocations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    purchase_id: Mapped[str] = mapped_column(ForeignKey("purchases.id"), unique=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    gross_minor: Mapped[int] = mapped_column(Integer)
    tax_minor: Mapped[int] = mapped_column(Integer)
    store_fee_minor: Mapped[int] = mapped_column(Integer)
    net_minor: Mapped[int] = mapped_column(Integer)
    creator_minor: Mapped[int] = mapped_column(Integer)
    platform_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    share_bps: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reversed: Mapped[bool] = mapped_column(Boolean, default=False)


class Payout(Base, TS):
    __tablename__ = "payouts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested|settled|rejected
    rail: Mapped[str] = mapped_column(String(24), default="sandbox")
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedEvent(Base, TS):
    """Inbound webhook / async event dedup (spec §9: event IDs and deduplication)."""

    __tablename__ = "processed_events"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))


class OutboxEvent(Base, TS):
    """Domain events (GenerationCompleted, PurchaseVerified, ...) for async consumers."""

    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    type: Mapped[str] = mapped_column(String(48), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    dedup_key: Mapped[str] = mapped_column(String(160), unique=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base, TS):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Experiment(Base, TS):
    __tablename__ = "experiments"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    variants: Mapped[dict] = mapped_column(JSON, default=dict)  # {"A": {...weight, config}, "B": ...}
    active: Mapped[bool] = mapped_column(Boolean, default=True)
