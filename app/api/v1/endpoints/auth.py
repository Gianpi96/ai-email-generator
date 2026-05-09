"""
/api/v1/auth  — registration and login.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.schemas import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user account",
)
async def register(
    data: UserRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    service = AuthService(db)
    return await service.register(data)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Obtain JWT access & refresh tokens",
)
async def login(
    data: UserLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    service = AuthService(db)
    return await service.login(data.email, data.password)
