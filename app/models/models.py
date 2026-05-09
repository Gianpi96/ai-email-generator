"""
ORM models — one table per domain concept.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


# ── Users ─────────────────────────────────────────────────────────────────────


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)

    emails: Mapped[list["GeneratedEmail"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="select"
    )
    ai_requests: Mapped[list["AIRequestLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"


# ── Generated Emails ──────────────────────────────────────────────────────────


class GeneratedEmail(TimestampMixin, Base):
    __tablename__ = "generated_emails"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Request payload
    email_type: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)

    # AI response
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Provider metadata
    ai_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, default=None)

    # Link to the raw AI request log
    request_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_request_logs.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(back_populates="emails")
    request_log: Mapped["AIRequestLog | None"] = relationship(
        back_populates="generated_email",
        foreign_keys=[request_log_id],
    )

    def __repr__(self) -> str:
        return f"<GeneratedEmail id={self.id} type={self.email_type!r}>"


# ── AI Request Log ─────────────────────────────────────────────────────────────


class AIRequestLog(TimestampMixin, Base):
    """
    Full audit log of every AI API call.
    Stores prompt, raw response, token usage, estimated cost and errors.
    """

    __tablename__ = "ai_request_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Request details
    email_type: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_template: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_used: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    tone: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Provider info
    ai_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False)

    # Response
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cost tracking (USD)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status: "success" | "error" | "timeout" | "rate_limited"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Duration in milliseconds
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="ai_requests")
    generated_email: Mapped["GeneratedEmail | None"] = relationship(
        back_populates="request_log",
        foreign_keys="GeneratedEmail.request_log_id",
        uselist=False,
    )

    def __repr__(self) -> str:
        return f"<AIRequestLog id={self.id} status={self.status!r} cost=${self.estimated_cost_usd}>"
