import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth
class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    locale: str = "en"
    age_confirmed: bool
    terms_accepted: bool
    acquisition_source: str | None = Field(default=None, max_length=64)
    device_id: str | None = Field(default=None, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    device_id: str | None = None


class SocialIn(BaseModel):
    provider: Literal["apple", "google"]
    id_token: str
    nonce: str | None = None
    country: str | None = None
    locale: str = "en"
    age_confirmed: bool = False
    terms_accepted: bool = False
    display_name: str | None = Field(default=None, max_length=80)
    device_id: str | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    needs_consent: bool = False


class ConsentIn(BaseModel):
    age_confirmed: bool
    terms_accepted: bool
    country: str | None = None


class MeOut(ORM):
    id: uuid.UUID
    email: str | None
    display_name: str | None
    country: str | None
    locale: str
    role: str
    plan: str
    plan_expires_at: datetime | None
    credits: int = 0
    needs_consent: bool = False


class MePatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    locale: str | None = Field(default=None, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=2)


# ---- identity
class ProfileCreateIn(BaseModel):
    name: str = Field(default="Me", max_length=60)
    consent_own_likeness: bool = Field(description="I am the person in these photos or have their permission")


class AssetOut(ORM):
    id: uuid.UUID
    kind: str
    mime: str
    status: str
    rejection_reason: str | None


class ProfileOut(ORM):
    id: uuid.UUID
    name: str
    status: str
    quality_report: dict
    created_at: datetime
    assets: list[AssetOut] = []
    thumbnail_url: str | None = None


class UploadSignIn(BaseModel):
    profile_id: uuid.UUID
    mime: str
    size_bytes: int = Field(gt=0)


class UploadSignOut(BaseModel):
    asset_id: uuid.UUID
    upload_url: str
    fields: dict[str, str]
    expires_in: int


# ---- templates
class TemplateOut(ORM):
    id: uuid.UUID
    slug: str
    title: str
    description: str
    category: str
    thumbnail_url: str | None
    preview_url: str | None
    duration_s: int
    aspect_ratio: str
    credit_cost: int
    est_seconds: int
    accepts_text: bool
    pro_only: bool


# ---- generations
class GenerationIn(BaseModel):
    template_id: uuid.UUID
    profile_id: uuid.UUID
    text: str | None = Field(default=None, max_length=200)


class OutputOut(BaseModel):
    video_url: str
    thumbnail_url: str | None
    width: int
    height: int
    duration_ms: int
    watermarked: bool


class GenerationOut(BaseModel):
    id: uuid.UUID
    status: str
    progress: float
    template_id: uuid.UUID
    queue_class: str
    queue_position: int | None = None
    est_seconds_remaining: int | None = None
    credit_cost: int
    refunded: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    output: OutputOut | None = None


class ReportIn(BaseModel):
    reason: Literal["sexual", "minor_safety", "impersonation", "harassment", "violence", "copyright",
                    "low_quality", "other"]
    details: str | None = Field(default=None, max_length=1000)


class CreditsOut(BaseModel):
    balance: int
    history: list[dict]


# ---- purchases
class PurchaseVerifyIn(BaseModel):
    platform: Literal["ios", "android"]
    product_id: str
    # iOS: StoreKit 2 signed transaction JWS; Android: purchase token
    receipt: str
    idempotency_key: str = Field(min_length=8, max_length=80)


class AnalyticsIn(BaseModel):
    name: str = Field(max_length=80)
    props: dict = {}
    device_id: str | None = None
