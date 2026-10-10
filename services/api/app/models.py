"""Relational data model (spec §11). PostgreSQL is the source of truth for every
financial and job state; Redis only holds disposable data (rate-limit counters)."""

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _enum(e: type[enum.Enum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [m.value for m in x])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserRole(str, enum.Enum):
    user = "user"
    support = "support"
    admin = "admin"


class UserStatus(str, enum.Enum):
    active = "active"
    banned = "banned"
    deleting = "deleting"
    deleted = "deleted"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str | None] = mapped_column(String(320))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    auth_provider: Mapped[str] = mapped_column(String(20), default="email")  # email|apple|google
    provider_subject: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str | None] = mapped_column(String(2))
    locale: Mapped[str] = mapped_column(String(16), default="en")
    age_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), default=UserRole.user)
    status: Mapped[UserStatus] = mapped_column(_enum(UserStatus, "user_status"), default=UserStatus.active)
    plan: Mapped[str] = mapped_column(String(20), default="free")  # free|pro (derived from entitlements)
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acquisition_source: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_users_email_active", func.lower(email), unique=True, postgresql_where=deleted_at.is_(None)),
        Index("uq_users_provider_subject", auth_provider, provider_subject, unique=True,
              postgresql_where=provider_subject.isnot(None)),
    )


class AuthSession(TimestampMixin, Base):
    """Refresh-token sessions (rotated on every refresh; hash stored, never the token)."""

    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_id: Mapped[str | None] = mapped_column(String(128))


class ProfileStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    rejected = "rejected"
    deleted = "deleted"


class IdentityProfile(TimestampMixin, Base):
    __tablename__ = "identity_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60), default="Me")
    status: Mapped[ProfileStatus] = mapped_column(_enum(ProfileStatus, "profile_status"),
                                                  default=ProfileStatus.draft)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str] = mapped_column(String(32))
    quality_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assets: Mapped[list["IdentityAsset"]] = relationship(back_populates="profile")


class AssetStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded = "uploaded"
    accepted = "accepted"
    rejected = "rejected"
    deleted = "deleted"


class IdentityAsset(TimestampMixin, Base):
    __tablename__ = "identity_assets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity_profiles.id", ondelete="CASCADE"),
                                                  index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # photo|video
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime: Mapped[str] = mapped_column(String(64))
    declared_size: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[AssetStatus] = mapped_column(_enum(AssetStatus, "asset_status"),
                                                default=AssetStatus.pending_upload)
    rejection_reason: Mapped[str | None] = mapped_column(String(120))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile: Mapped[IdentityProfile] = relationship(back_populates="assets")


class Template(TimestampMixin, Base):
    __tablename__ = "templates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40), index=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    preview_url: Mapped[str | None] = mapped_column(String(512))
    duration_s: Mapped[int] = mapped_column(Integer, default=5)
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")
    credit_cost: Mapped[int] = mapped_column(Integer, default=10)
    est_seconds: Mapped[int] = mapped_column(Integer, default=90)
    accepts_text: Mapped[bool] = mapped_column(Boolean, default=False)
    locale_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    safety_tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    pro_only: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id", use_alter=True, name="fk_templates_current_version"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # V3 (spec §3/§26). Defaults keep pre-V3 templates public + approved, so nothing changes for them.
    creator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    visibility: Mapped[str] = mapped_column(String(16), default="public", server_default="public")
    # draft|private|unlisted|public|blocked
    moderation_status: Mapped[str] = mapped_column(String(16), default="approved", server_default="approved")
    # pending|approved|rejected|review
    commercial_rights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")

    __table_args__ = (CheckConstraint("credit_cost >= 0", name="ck_templates_cost_nonneg"),)


class TemplateVersion(Base):
    """Immutable prompt recipe snapshot. Never shown to clients."""

    __tablename__ = "template_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    prompt_recipe: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    model_capability: Mapped[str] = mapped_column(String(40), default="identity_i2v")
    preferred_model: Mapped[str] = mapped_column(String(60), default="wan22_ti2v_5b")
    fallback_model: Mapped[str | None] = mapped_column(String(60))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # V3: canonical source clip (analysed into person tracks), provider-neutral config and pricing rule.
    source_video_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_videos.id", ondelete="SET NULL"))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    credit_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    slots: Mapped[list["TemplatePersonSlot"]] = relationship(order_by="TemplatePersonSlot.position")

    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_versions_version"),)


class TemplatePersonSlot(Base):
    """Variable-length person slots (1..N, spec §26). `slot_id` is stable across versions so analytics,
    saved assignments and deep links survive re-ingestion; `track_id` maps to the source clip's tracks."""

    __tablename__ = "template_person_slots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    template_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_versions.id", ondelete="CASCADE"),
                                                           index=True)
    slot_id: Mapped[str] = mapped_column(String(32))
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(60))
    track_id: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("template_version_id", "slot_id", name="uq_template_slots_slot"),
                      UniqueConstraint("template_version_id", "track_id", name="uq_template_slots_track"))


class TemplateMetricDaily(Base):
    """Template metrics aggregated separately from operational tables (spec §3); feeds ranking + dashboards."""

    __tablename__ = "template_metrics_daily"
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    opens: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    generations: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    paid_uses: Mapped[int] = mapped_column(Integer, default=0)
    reports: Mapped[int] = mapped_column(Integer, default=0)
    credits_charged: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class JobStatus(str, enum.Enum):
    queued = "queued"
    preprocessing = "preprocessing"
    generating = "generating"
    postprocessing = "postprocessing"
    moderation = "moderation"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_STATUSES = {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}


class JobKind(str, enum.Enum):
    template = "template"            # identity profile x template (classic flow)
    analysis = "analysis"            # multi-person: detect + track people in an uploaded video (no credits)
    multi_replace = "multi_replace"  # multi-person: replace assigned people in an uploaded video
    studio_shot = "studio_shot"      # AI Studio: render one storyboard shot
    studio_assemble = "studio_assemble"  # AI Studio: assemble rendered shots + captions + audio
    character_asset = "character_asset"  # V6: one character identity image (preview / master / view)


class GenerationJob(TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[JobKind] = mapped_column(_enum(JobKind, "job_kind"), default=JobKind.template)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity_profiles.id", ondelete="SET NULL"))
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id"))
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("template_versions.id"))
    source_video_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_videos.id", ondelete="SET NULL"),
                                                              index=True)
    # Model routing snapshot taken at creation (template version or remote config).
    preferred_model: Mapped[str] = mapped_column(String(60))
    fallback_model: Mapped[str | None] = mapped_column(String(60))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # kind-specific worker parameters
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), default=JobStatus.queued)
    queue_class: Mapped[str] = mapped_column(String(16), default="free")
    user_text: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(80))
    credit_cost: Mapped[int] = mapped_column(Integer)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    studio_project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    billing_state: Mapped[str | None] = mapped_column(String(12))  # reserved|settled|released (NULL: free/legacy)
    est_cost_usd: Mapped[float | None] = mapped_column(Float)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    model_used: Mapped[str | None] = mapped_column(String(60))
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(String(500))
    watermark: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outputs: Mapped[list["GenerationOutput"]] = relationship(back_populates="job")

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_jobs_user_idem"),
        Index("ix_jobs_claim", "queue_class", "created_at", postgresql_where=(status == JobStatus.queued)),
        Index("ix_jobs_lease", "lease_expires_at",
              postgresql_where=status.in_([JobStatus.preprocessing, JobStatus.generating,
                                           JobStatus.postprocessing, JobStatus.moderation])),
    )


class SourceVideoStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    analyzing = "analyzing"
    ready = "ready"
    rejected = "rejected"
    failed = "failed"
    deleted = "deleted"


class SourceVideo(TimestampMixin, Base):
    """A user-uploaded real video for multi-person replacement. Rights/consent attested at creation."""

    __tablename__ = "source_videos"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime: Mapped[str] = mapped_column(String(64))
    declared_size: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[SourceVideoStatus] = mapped_column(_enum(SourceVideoStatus, "source_video_status"),
                                                      default=SourceVideoStatus.pending_upload)
    rights_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attestation_version: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    analysis_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # flags, model versions, timings
    rejection_reason: Mapped[str | None] = mapped_column(String(120))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    persons: Mapped[list["VideoPerson"]] = relationship(back_populates="video", order_by="VideoPerson.track_id")


class VideoPerson(Base):
    """One persistent track (same physical person across the whole clip, incl. exits/re-entries)."""

    __tablename__ = "video_persons"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_video_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_videos.id", ondelete="CASCADE"),
                                                       index=True)
    track_id: Mapped[int] = mapped_column(Integer)          # 1-based, shown as "Person {track_id}"
    first_frame: Mapped[int] = mapped_column(Integer)
    last_frame: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[float] = mapped_column(Float)          # fraction of frames where visible
    face_visible_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    median_face_px: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))
    selectable: Mapped[bool] = mapped_column(Boolean, default=True)
    flags: Mapped[list[str]] = mapped_column(JSONB, default=list)   # too_small, heavy_occlusion, ...
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    video: Mapped[SourceVideo] = relationship(back_populates="persons")

    __table_args__ = (UniqueConstraint("source_video_id", "track_id", name="uq_video_persons_track"),)


class AudioAsset(TimestampMixin, Base):
    """User-supplied soundtrack for lip-sync (spec §27 audio_mode=custom). Rights attested at creation."""

    __tablename__ = "audio_assets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime: Mapped[str] = mapped_column(String(64))
    declared_size: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending_upload")  # pending_upload|ready|rejected|deleted
    rights_basis: Mapped[str] = mapped_column(String(20))  # own|licensed
    rights_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attestation_version: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobAssignment(Base):
    __tablename__ = "job_assignments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(Integer)
    profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("identity_profiles.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("job_id", "track_id", name="uq_job_assignments_track"),)


class GenerationOutput(TimestampMixin, Base):
    __tablename__ = "generation_outputs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True)
    video_key: Mapped[str] = mapped_column(String(512))
    thumbnail_key: Mapped[str | None] = mapped_column(String(512))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    codec: Mapped[str] = mapped_column(String(16), default="h264")
    watermarked: Mapped[bool] = mapped_column(Boolean, default=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[GenerationJob] = relationship(back_populates="outputs")


class ModelRun(Base):
    """One attempt of one job on one worker: cost attribution + benchmarking."""

    __tablename__ = "model_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(60))
    gpu_provider: Mapped[str | None] = mapped_column(String(40))
    gpu_type: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed|lost
    gpu_seconds: Mapped[float | None] = mapped_column(Float)
    est_cost_usd: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # apple|google|revenuecat|stripe
    product_id: Mapped[str] = mapped_column(String(120))
    original_transaction_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))  # active|grace|billing_retry|expired|revoked|refunded
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    environment: Mapped[str] = mapped_column(String(20), default="production")
    last_granted_period: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("provider", "original_transaction_id", name="uq_subs_provider_otid"),)


class Purchase(TimestampMixin, Base):
    __tablename__ = "purchases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    product_id: Mapped[str] = mapped_column(String(120))
    transaction_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # consumable|subscription
    status: Mapped[str] = mapped_column(String(20))  # verified|refunded|revoked
    environment: Mapped[str] = mapped_column(String(20), default="production")
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("provider", "transaction_id", name="uq_purchases_provider_txn"),)


class LedgerReason(str, enum.Enum):
    signup_bonus = "signup_bonus"
    purchase = "purchase"
    subscription_grant = "subscription_grant"
    generation_debit = "generation_debit"
    refund = "refund"
    promo = "promo"
    admin_adjust = "admin_adjust"
    purchase_reversal = "purchase_reversal"
    # V4 Stage A: `generation_debit` is the reservation, `refund` the release; settle confirms the charge.
    generation_settle = "generation_settle"
    expire = "expire"
    license_fee = "license_fee"  # V4 Stage D: AI actor licence purchase


class CreditLedger(Base):
    """Append-only (enforced by DB trigger, see migration). Balance = SUM(delta)."""

    __tablename__ = "credit_ledger"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[LedgerReason] = mapped_column(_enum(LedgerReason, "ledger_reason"))
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("balance_after >= 0", name="ck_ledger_balance_nonneg"),)


class CreditLot(Base):
    """A grant of credits in one bucket with its own expiry. Immutable: what is left in a lot is
    `granted + SUM(credit_allocations.amount)`, derived from append-only rows, never a mutable counter."""

    __tablename__ = "credit_lots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    bucket: Mapped[str] = mapped_column(String(20))  # promo|subscription|purchased|reward|adjustment|legacy
    source_ledger_id: Mapped[int] = mapped_column(ForeignKey("credit_ledger.id", ondelete="RESTRICT"), unique=True)
    granted: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (CheckConstraint("granted > 0", name="ck_credit_lots_granted_pos"),)


class CreditAllocation(Base):
    """Which lots a ledger entry consumed (negative) or returned to (positive). Append-only."""

    __tablename__ = "credit_allocations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ledger_id: Mapped[int] = mapped_column(ForeignKey("credit_ledger.id", ondelete="RESTRICT"), index=True)
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_lots.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)

    __table_args__ = (CheckConstraint("amount <> 0", name="ck_credit_allocations_nonzero"),)


# ---------------------------------------------------------------- AI Studio (V4 Stage B)

class StudioProject(TimestampMixin, Base):
    __tablename__ = "studio_projects"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")
    language: Mapped[str] = mapped_column(String(8), default="en")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|planned|rendering|ready|failed
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rendered_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    output_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    budget_credits: Mapped[int | None] = mapped_column(Integer)  # per-project cost cap chosen by the user
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # V7: an episode of a production. Limits (long episodes) and the fiction content policy come from it.
    production_episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    content_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class StudioProjectVersion(Base):
    """Immutable snapshot of the storyboard (scenes, shots, dialogue, captions, audio). Edits and restores
    create new versions; nothing is overwritten."""

    __tablename__ = "studio_project_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studio_projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(20))  # director|edit|restore
    brief: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    storyboard: Mapped[dict[str, Any]] = mapped_column(JSONB)
    director: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # provider, model, label
    # V5: immutable intent, creative mode, strategy version, composition, seed, candidate scores, similarity
    creative: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_studio_version"),)


class StudioShotRender(Base):
    """A rendered (or rendering) shot, keyed by the hash of everything that affects its pixels. Versions whose
    shot has the same hash reuse it: editing one shot never re-renders (or re-charges) the others."""

    __tablename__ = "studio_shot_renders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studio_projects.id", ondelete="CASCADE"), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    shot_key: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|ready|failed
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"))
    video_key: Mapped[str | None] = mapped_column(String(512))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("project_id", "content_hash", name="uq_studio_shot_hash"),)


class StudioCharacter(TimestampMixin, Base):
    """Project-scoped (or reusable) character. A real likeness is only usable through the owner's own consented
    identity profile plus an active consent receipt; fictional characters have no reference images."""

    __tablename__ = "studio_characters"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("studio_projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(500), default="")
    traits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # wardrobe/costume presets etc.
    identity_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("identity_profiles.id", ondelete="SET NULL"))
    voice_permission: Mapped[str] = mapped_column(String(20), default="none")  # none|tts_stock (no cloning)
    actor_license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # licensed AI actor (Stage D)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudioEditOperation(Base):
    """One conversational or timeline edit: typed operations proposed against a base version, previewed with a
    cost delta, then applied as a new version (or rejected). Nothing is overwritten; undo = restore."""

    __tablename__ = "studio_edit_operations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studio_projects.id", ondelete="CASCADE"), index=True)
    base_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    result_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source: Mapped[str] = mapped_column(String(20))  # chat|timeline
    instruction: Mapped[str | None] = mapped_column(String(1000))
    editor: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ops: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(24))  # proposed|needs_clarification|unsupported|applied|rejected
    clarification: Mapped[str | None] = mapped_column(String(500))
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConsentReceipt(Base):
    """Evidence that a person allowed a use of their likeness/voice. Revocation stops future use."""

    __tablename__ = "consent_receipts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[str] = mapped_column(String(30))  # studio_character
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"likeness": true, "voice": false}
    terms_version: Mapped[str] = mapped_column(String(32))
    statement: Mapped[str] = mapped_column(String(300))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- creator economy (V4 Stage D)

class CreatorProfile(TimestampMixin, Base):
    """Public creator identity. Payout/KYC data lives with the external KYC/payout provider; only its reference
    and status are kept here (least privilege: ordinary services never see bank or tax details)."""

    __tablename__ = "creator_profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    handle: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(60))
    bio: Mapped[str] = mapped_column(String(300), default="")
    payout_country: Mapped[str | None] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|suspended
    terms_version: Mapped[str] = mapped_column(String(32))
    terms_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payout_status: Mapped[str] = mapped_column(String(16), default="none")  # none|kyc_pending|verified|blocked
    kyc_ref: Mapped[str | None] = mapped_column(String(120))


class RevenuePolicy(Base):
    """Versioned, immutable revenue-share rules. An earning stores the policy it was computed with, and
    settlements snapshot them, so a later policy can never rewrite history."""

    __tablename__ = "revenue_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CreatorEarning(Base):
    """Append-only creator money ledger (USD micros). Accruals, clawbacks, payouts and adjustments are rows;
    a balance is always SUM(amount_micros)."""

    __tablename__ = "creator_earnings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    # template_share|referral_commission|actor_license|clawback|payout|adjustment
    kind: Mapped[str] = mapped_column(String(24))
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    gross_basis_micros: Mapped[int] = mapped_column(BigInteger, default=0)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("revenue_policies.id", ondelete="RESTRICT"))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    attribution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    license_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)  # fraud signals only
    settlement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # set only on payout rows
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # end of the refund/fraud hold window
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Settlement(TimestampMixin, Base):
    """A payout batch for one creator: frozen snapshot of the included earnings and policies."""

    __tablename__ = "settlements"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount_micros: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))  # pending|held|approved|paid|cancelled
    risk: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    external_ref: Mapped[str | None] = mapped_column(String(120))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettlementItem(Base):
    __tablename__ = "settlement_items"
    earning_id: Mapped[int] = mapped_column(ForeignKey("creator_earnings.id", ondelete="RESTRICT"), primary_key=True)
    settlement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("settlements.id", ondelete="CASCADE"), index=True)


class CreatorRiskHold(Base):
    __tablename__ = "creator_risk_holds"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    signals: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|released|confirmed_fraud
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(300))


class ActorListing(TimestampMixin, Base):
    """Opt-in licensed likeness: a creator offers *their own* consented identity profile under explicit terms."""

    __tablename__ = "actor_listings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    identity_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("identity_profiles.id", ondelete="CASCADE"))
    consent_receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    display_name: Mapped[str] = mapped_column(String(60))
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB)  # versioned contract terms (see actors.Terms)
    terms_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending_review")  # pending_review|active|paused|removed
    moderation_note: Mapped[str | None] = mapped_column(String(300))


class ActorLicense(Base):
    __tablename__ = "actor_licenses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actor_listings.id", ondelete="RESTRICT"), index=True)
    licensee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_version: Mapped[int] = mapped_column(Integer)
    price_credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|revoked|expired
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LicenseUsageEvent(Base):
    __tablename__ = "license_usage_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    license_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("actor_licenses.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage: Mapped[str] = mapped_column(String(16))  # dispatch|publish
    allowed: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- evaluation + operations (V4 Stage E)

class BenchmarkSet(Base):
    """A fixed, rights-cleared evaluation set. Frozen on first run so results stay comparable over time."""

    __tablename__ = "benchmark_sets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    rights_note: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("name", "version", name="uq_benchmark_set_version"),)


class BenchmarkCase(Base):
    __tablename__ = "benchmark_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_sets.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(40))  # dance|comedy|dialogue|music|ads|history|multi_person|...
    prompt: Mapped[str] = mapped_column(String(1500))
    duration_s: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")

    __table_args__ = (UniqueConstraint("set_id", "key", name="uq_benchmark_case_key"),)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_sets.id", ondelete="RESTRICT"), index=True)
    providers: Mapped[list[str]] = mapped_column(JSONB)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BenchmarkResult(Base):
    """One provider x case render. Human review is blind (reviewers never see the provider)."""

    __tablename__ = "benchmark_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_runs.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("benchmark_cases.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(60))
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    adherence: Mapped[int | None] = mapped_column(Integer)  # 1..5 instruction adherence
    quality: Mapped[int | None] = mapped_column(Integer)    # 1..5 visual quality
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (UniqueConstraint("run_id", "case_id", "provider", name="uq_benchmark_result"),)


class ScheduledRun(Base):
    __tablename__ = "scheduled_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(16))  # running|succeeded|failed|skipped
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- learning foundation (V5 Phase B)

class ConsentRecord(Base):
    """Append-only learning-consent receipts per purpose. The latest row per purpose wins; none = default."""

    __tablename__ = "consent_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))  # technical_improvement|content_training|personalization
    granted: Mapped[bool] = mapped_column(Boolean)
    policy_version: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(20))  # user|account_deletion|data_deletion
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningEvent(Base):
    """Content-free learning record linked to one job (no prompts, media or biometric data). Carries schema
    version, provenance, purpose, a consent snapshot and a retention deadline (spec V5 §8)."""

    __tablename__ = "learning_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(String(16))  # job_outcome|feedback
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    feature: Mapped[str] = mapped_column(String(40))
    intent_category: Mapped[str | None] = mapped_column(String(40))
    creative_mode: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(60))
    prompt_strategy: Mapped[str | None] = mapped_column(String(40))
    resolution: Mapped[str | None] = mapped_column(String(16))
    duration_s: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(16))
    success: Mapped[bool | None] = mapped_column(Boolean)
    error_code: Mapped[str | None] = mapped_column(String(60))
    retries: Mapped[int | None] = mapped_column(Integer)
    latency_s: Mapped[float | None] = mapped_column(Float)
    credits: Mapped[int | None] = mapped_column(Integer)
    est_cost_usd: Mapped[float | None] = mapped_column(Float)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # numeric QA signals only
    rating: Mapped[int | None] = mapped_column(Integer)
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    regenerated: Mapped[bool] = mapped_column(Boolean, default=False)
    consent: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    purpose: Mapped[str] = mapped_column(String(32), default="technical_improvement")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    __table_args__ = (UniqueConstraint("job_id", "kind", name="uq_learning_event_job_kind"),)


class ModelPerformanceAggregate(Base):
    """Technical memory: daily roll-up per provider x feature x creative mode (no user identities)."""

    __tablename__ = "model_performance_aggregates"
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60), primary_key=True)
    feature: Mapped[str] = mapped_column(String(40), primary_key=True)
    creative_mode: Mapped[str] = mapped_column(String(16), primary_key=True)
    jobs: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    p50_latency_s: Mapped[float | None] = mapped_column(Float)
    p95_latency_s: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ratings: Mapped[int] = mapped_column(Integer, default=0)
    rating_mean: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    errors: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PromptStrategy(Base):
    """Registry of prompt-optimization strategy versions actually used (V5 §3, versioned & reversible)."""

    __tablename__ = "prompt_strategies"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(String(300))
    config_sha256: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|retired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SimilarityAudit(Base):
    """Auditable near-duplicate check of a plan against content lawfully available for comparison."""

    __tablename__ = "similarity_audits"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject: Mapped[str] = mapped_column(String(20))
    category: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(30))
    threshold: Mapped[float] = mapped_column(Float)
    top_kind: Mapped[str | None] = mapped_column(String(20))
    top_ref: Mapped[str | None] = mapped_column(String(64))
    top_score: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(20))  # ok|near_duplicate|intentional_reuse
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class LearningPolicyVersion(Base):
    """A versioned adaptive policy (e.g. routing). Lifecycle: draft -> shadow -> ab -> active, with rollback;
    promotion is always a human decision backed by evidence, rollback can be automatic (V5 §6)."""

    __tablename__ = "learning_policy_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(20))  # routing
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|shadow|ab|active|rolled_back|retired
    rollout_pct: Mapped[int] = mapped_column(Integer, default=0)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    history: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("kind", "version", name="uq_learning_policy_version"),)


class ExperimentAssignment(Base):
    """Server-side, sticky experiment assignment (one row per user and experiment)."""

    __tablename__ = "experiment_assignments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    experiment_key: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    variant: Mapped[str] = mapped_column(String(20))  # baseline|candidate
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("experiment_key", "user_id", name="uq_experiment_assignment"),)


class ModelRegistryVersion(Base):
    """Governance record for a self-hosted / fine-tuned model artifact (V5 Phase E). It can only reach the router
    after licence, data-rights, model-card, evaluation and two-person human approval gates pass."""

    __tablename__ = "model_registry_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider_name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    base_model: Mapped[str] = mapped_column(String(120))
    license: Mapped[str] = mapped_column(String(120))
    attestations: Mapped[dict[str, Any]] = mapped_column(JSONB)  # licence/data/rights checks + evidence refs
    model_card: Mapped[dict[str, Any]] = mapped_column(JSONB)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    usd_per_second: Mapped[float] = mapped_column(Float)
    eval_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    approvals: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="registered")
    # registered|approved|deployed|rejected|retired
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("provider_name", "version", name="uq_model_registry_version"),)


class ShareLink(Base):
    """Server-issued share link ("Try this template"). The public token is `code.signature`; only `code`
    is stored. Links point at a template, never at a user's private output video."""

    __tablename__ = "share_links"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"))
    campaign: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralClick(Base):
    __tablename__ = "referral_clicks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    link_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("share_links.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(10))  # web|app
    ip_hash: Mapped[str] = mapped_column(String(64))  # keyed hash, never the raw IP
    platform: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Attribution(Base):
    """At most one referral attribution per user (first touch). Earnings policy comes later (Stage D)."""

    __tablename__ = "attributions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("share_links.id", ondelete="SET NULL"))
    click_id: Mapped[int | None] = mapped_column(ForeignKey("referral_clicks.id", ondelete="SET NULL"))
    referrer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"))
    campaign: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(20), default="first_touch")
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # window/rules snapshot at attribution
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Report(TimestampMixin, Base):
    __tablename__ = "reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"),
                                                     index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"))
    character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)  # V6
    reason: Mapped[str] = mapped_column(String(40))
    details: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)  # open|actioned|dismissed


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reports.id", ondelete="SET NULL"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_jobs.id", ondelete="SET NULL"))
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="human")  # auto_input|auto_output|human
    action: Mapped[str] = mapped_column(String(40))  # block_input|block_output|remove_output|ban|dismiss|warn
    category: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FeatureFlag(TimestampMixin, Base):
    __tablename__ = "feature_flags"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    description: Mapped[str] = mapped_column(String(300), default="")
    public: Mapped[bool] = mapped_column(Boolean, default=False)  # exposed via remote config


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    variants: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {"control": 50, "b": 50}
    countries: Mapped[list[str]] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(80), index=True)
    props: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class WebhookEvent(Base):
    """Durable inbox: every billing webhook is stored before any business logic runs."""

    __tablename__ = "webhook_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|processed|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)


# ---------------------------------------------------------------- V6 creator character identity ecosystem

class Character(TimestampMixin, Base):
    """A persistent digital actor. `id` is the immutable character UUID; the public handle is
    `@<creator handle>/<handle>` and is unique per creator only (display names need not be unique)."""

    __tablename__ = "characters"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    handle: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|private|unlisted|public|suspended|deleted
    origin: Mapped[str] = mapped_column(String(16), default="synthetic")  # synthetic|real_person
    identity_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # real_person: own profile
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locked_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    moderation_status: Mapped[str] = mapped_column(String(16), default="none")  # none|flagged|cleared|removed
    moderation_note: Mapped[str | None] = mapped_column(String(300))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("creator_id", "handle", name="uq_character_creator_handle"),)


class CharacterIdentityVersion(Base):
    """Immutable core identity per version. Changing immutable traits = new major version; cosmetic variants
    (wardrobe, lighting presets) = minor versions. A locked version never changes."""

    __tablename__ = "character_identity_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    major: Mapped[int] = mapped_column(Integer)
    minor: Mapped[int] = mapped_column(Integer, default=0)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    change_kind: Mapped[str] = mapped_column(String(10), default="initial")  # initial|major|minor
    original_prompt: Mapped[str] = mapped_column(Text)  # the creator's words, never overwritten
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)  # CharacterSpec (schema cs1)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # draft|master_approved|built|locked|superseded
    master_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    package: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # IdentityPackageManifest
    package_checksum: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("character_id", "major", "minor", name="uq_character_version"),)


class CharacterAsset(Base):
    """One identity image with full provenance. Private storage only; licensees never get these URLs."""

    __tablename__ = "character_assets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    identity_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("character_identity_versions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # seed_preview|master_portrait|master_fullbody|view
    view_key: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|ready|failed|rejected|approved
    storage_key: Mapped[str | None] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(120))
    seed: Mapped[int | None] = mapped_column(BigInteger)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    reference_asset_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    qc: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    creator_review: Mapped[str | None] = mapped_column(String(12))  # approved|rejected
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CharacterQualityReport(Base):
    """IdentityValidationReport: measured, never a guarantee. `method` says whether a face embedder or only
    proxy metrics were available; proxy-only results can never be 'verified'."""

    __tablename__ = "character_quality_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    identity_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    scope: Mapped[str] = mapped_column(String(10))  # build|shot
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    method: Mapped[str] = mapped_column(String(20))  # face_embedding|proxy|none
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verdict: Mapped[str] = mapped_column(String(16))  # pass|warn|fail|insufficient
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CharacterRightsClaim(Base):
    """Append-only originality / rights declaration (V6.1). Real-person references need a consent receipt."""

    __tablename__ = "character_rights_claims"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    origin: Mapped[str] = mapped_column(String(16))
    declaration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_version: Mapped[str] = mapped_column(String(32))
    consent_receipt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CharacterVoiceProfile(Base):
    """Versioned voice choice. Only licensed synthetic TTS voices are usable; consented voice likeness is
    modelled but stays disabled (no cloning provider, separate abuse workflow required)."""

    __tablename__ = "character_voice_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # licensed_tts|consented_likeness
    provider: Mapped[str] = mapped_column(String(60))
    voice_id: Mapped[str] = mapped_column(String(120))
    languages: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    territories: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    allowed_use: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    style: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # accent, timbre, pace, pronunciation
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|revoked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("character_id", "version", name="uq_character_voice_version"),)


class ProjectCastMember(Base):
    """CastBinding: project + character + frozen identity/voice version. Creator updates never change it."""

    __tablename__ = "project_cast_members"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studio_projects.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="RESTRICT"), index=True)
    identity_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    voice_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(String(40))  # key used in the storyboard and in @alias mentions
    lock_mode: Mapped[str] = mapped_column(String(10), default="standard")  # standard|strong|strict
    allowed_variants: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # licence grant (others' characters)
    added_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_cast_alias_active", "project_id", "alias", unique=True,
                            postgresql_where=text("removed_at IS NULL")),)


class CharacterListing(TimestampMixin, Base):
    """Marketplace listing with explicit, versioned licence terms and per-usage price."""

    __tablename__ = "character_listings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    character_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    identity_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    visibility: Mapped[str] = mapped_column(String(10))  # public|unlisted
    status: Mapped[str] = mapped_column(String(16), default="pending_review")
    # pending_review|active|paused|rejected|removed
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_version: Mapped[int] = mapped_column(Integer, default=1)
    certification: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    review_note: Mapped[str | None] = mapped_column(String(300))


class CharacterLicenseGrant(Base):
    __tablename__ = "character_license_grants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("character_listings.id", ondelete="RESTRICT"),
                                                  index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    licensee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|revoked|expired
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(300))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CharacterUsageEvent(Base):
    """CharacterUsageEvent: one per (job, character). Created with the reservation, settled exactly once
    with the job (royalty accrues then), released when the job is refunded."""

    __tablename__ = "character_usage_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    grant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    identity_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    seconds: Mapped[int] = mapped_column(Integer)
    license_credits: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="reserved")  # reserved|settled|released
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("job_id", "character_id", name="uq_character_usage_job"),)


# ---------------------------------------------------------------- V7 AI cinema & short drama factory

class Production(TimestampMixin, Base):
    """A series, film, "create your stars" or life-story production. One shared infrastructure: every episode is
    a Studio project, so rendering, hashing, edits, identity lock and billing are the existing ones."""

    __tablename__ = "productions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # series|film|stars|life_story
    title: Mapped[str] = mapped_column(String(120))
    logline: Mapped[str] = mapped_column(String(600), default="")
    genre: Mapped[str] = mapped_column(String(40), default="drama")
    audience: Mapped[str] = mapped_column(String(40), default="adult")
    # micro|short_series|standard_episode|long_episode|short_film|feature
    format: Mapped[str] = mapped_column(String(20))
    visual_style: Mapped[str] = mapped_column(String(20), default="cinematic")
    language: Mapped[str] = mapped_column(String(8), default="tr")
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9")
    content_rating: Mapped[str] = mapped_column(String(10), default="general")  # general|teen|mature
    profile: Mapped[str] = mapped_column(String(12), default="standard")  # economy|standard|cinema_pro
    status: Mapped[str] = mapped_column(String(16), default="draft")
    active_branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cast: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # [{character_id, alias, lock_mode}]
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    people_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # life stories: people/consent check
    visibility: Mapped[str] = mapped_column(String(10), default="private")  # private (publishing is opt-in)
    policy_version: Mapped[str] = mapped_column(String(20), default="v7-fiction-1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryBranch(Base):
    """Story versions: "don't let her die in episode 4" forks a new branch from episode 4. Earlier episodes are
    shared with the parent branch; nothing is overwritten."""

    __tablename__ = "story_branches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    production_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productions.id", ondelete="CASCADE"), index=True)
    parent_branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fork_episode: Mapped[int | None] = mapped_column(Integer)  # first episode number that differs
    name: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionEpisode(TimestampMixin, Base):
    __tablename__ = "production_episodes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    production_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productions.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("story_branches.id", ondelete="CASCADE"), index=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    synopsis: Mapped[str] = mapped_column(String(2000), default="")
    target_duration_s: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    studio_project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    script: Mapped[str | None] = mapped_column(Text)  # the user's screenplay, kept verbatim
    events: Mapped[list[Any]] = mapped_column(JSONB, default=list)  # declared story events (continuity)
    plan_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    animatic_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pilot_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pilot_keys: Mapped[list[Any]] = mapped_column(JSONB, default=list)

    __table_args__ = (UniqueConstraint("branch_id", "season", "number", name="uq_episode_branch_number"),)


class StoryBible(Base):
    """Versioned project bible (world, characters, relationships, locations, props, wardrobe, mysteries, rules)."""

    __tablename__ = "story_bibles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    production_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("production_id", "version", name="uq_story_bible_version"),)


class EpisodeSnapshot(Base):
    """Immutable canonical world state after an approved episode (+ the deltas it introduced)."""

    __tablename__ = "episode_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("production_episodes.id", ondelete="CASCADE"),
                                                  index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    number: Mapped[int] = mapped_column(Integer)
    studio_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    bible_version: Mapped[int | None] = mapped_column(Integer)
    events: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContinuityFinding(Base):
    __tablename__ = "continuity_findings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    episode_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    studio_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    severity: Mapped[str] = mapped_column(String(10))  # error|warning|info
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(400))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CostEstimate(Base):
    """Estimate snapshot (estimate != quote != actual). Money in credits and currency minor units."""

    __tablename__ = "cost_estimates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    production_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productions.id", ondelete="CASCADE"), index=True)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {episodes: [...], purpose}
    profile: Mapped[str] = mapped_column(String(12))
    profile_version: Mapped[int] = mapped_column(Integer)
    credits_low: Mapped[int] = mapped_column(Integer)
    credits_high: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    amount_low_minor: Mapped[int | None] = mapped_column(BigInteger)
    amount_high_minor: Mapped[int | None] = mapped_column(BigInteger)
    provider_usd_low: Mapped[float | None] = mapped_column(Float)
    provider_usd_high: Mapped[float | None] = mapped_column(Float)
    feasible: Mapped[bool | None] = mapped_column(Boolean)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetAuthorization(Base):
    """The user's explicit approval: a hard cap (credits) for paid production work. Overruns need a new one."""

    __tablename__ = "budget_authorizations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    production_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("productions.id", ondelete="CASCADE"), index=True)
    estimate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cap_credits: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    cap_minor: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|superseded|revoked
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionEvent(Base):
    """Transactional outbox: written in the same transaction as the change it describes."""

    __tablename__ = "production_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    production_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LifeStorySession(TimestampMixin, Base):
    """Guided "Your life, your story" interview: private by default, privacy scan, fact/fiction marking, approval."""

    __tablename__ = "life_story_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    chronology: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    privacy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    production_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
