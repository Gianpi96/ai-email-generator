"""
Pydantic v2 schemas — strict validation, no ORM leakage to the transport layer.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Shared ────────────────────────────────────────────────────────────────────


class OrmBase(BaseModel):
    model_config = {"from_attributes": True}


# ── Auth ──────────────────────────────────────────────────────────────────────


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=8, max_length=128)]
    full_name: Annotated[str | None, Field(max_length=255)] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit.")
        return v


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(OrmBase):
    id: uuid.UUID
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime


# ── Email Generation ──────────────────────────────────────────────────────────

EMAIL_TYPES = [
    "formal",
    "informal",
    "follow_up",
    "introduction",
    "complaint",
    "apology",
    "commercial",
    "cold_outreach",
    "thank_you",
    "invitation",
]


class GenerateEmailRequest(BaseModel):
    email_type: Annotated[str, Field(description="Type of email to generate")]
    recipient: Annotated[str, Field(min_length=2, max_length=255)]
    context: Annotated[str, Field(min_length=10, max_length=2000)]
    language: Annotated[str, Field(default="en", max_length=10)] = "en"
    tone: Annotated[str | None, Field(max_length=50)] = None

    @field_validator("email_type")
    @classmethod
    def validate_email_type(cls, v: str) -> str:
        if v not in EMAIL_TYPES:
            raise ValueError(f"email_type must be one of: {', '.join(EMAIL_TYPES)}")
        return v


class GeneratedEmailResponse(OrmBase):
    id: uuid.UUID
    email_type: str
    recipient: str
    context: str
    subject: str
    body: str
    ai_provider: str
    ai_model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    created_at: datetime


# ── History filters ───────────────────────────────────────────────────────────


class HistoryFilters(BaseModel):
    """Query parameters for GET /history."""

    page: Annotated[int, Field(ge=1, default=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=100, default=20)] = 20

    # Filters
    email_type: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    search: str | None = None  # full-text search in subject, body, recipient
    ai_provider: str | None = None

    # Sorting
    sort_by: str = "created_at"  # created_at | email_type | recipient
    sort_order: str = "desc"  # asc | desc

    @field_validator("email_type")
    @classmethod
    def validate_email_type(cls, v: str | None) -> str | None:
        if v and v not in EMAIL_TYPES:
            raise ValueError(f"email_type must be one of: {', '.join(EMAIL_TYPES)}")
        return v

    @field_validator("sort_by")
    @classmethod
    def validate_sort_by(cls, v: str) -> str:
        allowed = {"created_at", "email_type", "recipient", "subject"}
        if v not in allowed:
            raise ValueError(f"sort_by must be one of: {', '.join(allowed)}")
        return v

    @field_validator("sort_order")
    @classmethod
    def validate_sort_order(cls, v: str) -> str:
        if v not in {"asc", "desc"}:
            raise ValueError("sort_order must be 'asc' or 'desc'")
        return v

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


# ── Pagination ────────────────────────────────────────────────────────────────


class PaginationParams(BaseModel):
    page: Annotated[int, Field(ge=1, default=1)]
    page_size: Annotated[int, Field(ge=1, le=100, default=20)]

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginatedResponse(BaseModel):
    items: list[GeneratedEmailResponse]
    total: int
    page: int
    page_size: int
    pages: int
    # Extra metadata
    filters_applied: dict = {}
