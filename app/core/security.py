"""
JWT creation / validation and password hashing utilities.
Uses bcrypt directly instead of passlib to avoid version compatibility issues.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.core.settings import get_settings

settings = get_settings()


# ── Passwords ─────────────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    pwd_bytes = plain.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── Tokens ────────────────────────────────────────────────────────────────────


def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + expires_delta
    payload["iat"] = datetime.now(timezone.utc)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: UUID, email: str) -> str:
    return _create_token(
        {"sub": str(user_id), "email": email, "type": "access"},
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )


def create_refresh_token(user_id: UUID) -> str:
    return _create_token(
        {"sub": str(user_id), "type": "refresh"},
        timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise
