"""
AuthService — user registration and login.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.core.settings import get_settings
from app.models.models import User
from app.schemas.schemas import TokenResponse, UserRegisterRequest, UserResponse

settings = get_settings()


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def register(self, data: UserRegisterRequest) -> UserResponse:
        existing = (
            await self._db.execute(select(User).where(User.email == data.email))
        ).scalar_one_or_none()

        if existing:
            raise ConflictError("An account with this email already exists.")

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
        )
        self._db.add(user)
        await self._db.flush()
        return UserResponse.model_validate(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        user = (
            await self._db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise AuthenticationError("Invalid email or password.")

        if not user.is_active:
            raise AuthenticationError("Account is disabled.")

        return TokenResponse(
            access_token=create_access_token(user.id, user.email),
            refresh_token=create_refresh_token(user.id),
            expires_in=settings.jwt_access_token_expire_minutes * 60,
        )

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._db.get(User, user_id)
