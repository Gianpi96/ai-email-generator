"""
Application settings — loaded from environment / .env file.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "AI Email Generator"
    app_version: str = "1.0.0"
    debug: bool = False

    # ── Server ────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Database ──────────────────────────────────────────────
    database_url: str = Field(..., description="Async SQLAlchemy DB URL")
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # ── JWT ───────────────────────────────────────────────────
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # ── AI Providers ──────────────────────────────────────────
    ai_provider: Literal["anthropic", "openai", "groq"] = "groq"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-20250514"
    groq_api_key: str | None = None
    groq_model: str = "llama-3.3-70b-versatile"

    # ── Rate Limiting ─────────────────────────────────────────
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # ── CORS ──────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("database_url", mode="before")
    @classmethod
    def fix_db_url(cls, v: str) -> str:
        """
        Railway fornisce DATABASE_URL come postgres:// o postgresql://
        SQLAlchemy asyncpg richiede postgresql+asyncpg://
        Questo validator converte automaticamente il formato.
        """
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("anthropic_api_key", "openai_api_key", "groq_api_key", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        return v or None

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
