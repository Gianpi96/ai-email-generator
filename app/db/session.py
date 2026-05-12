"""
Async SQLAlchemy engine, session factory, and dependency injection helper.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.settings import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_pre_ping=True,
    echo=settings.debug,
)

AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_audit_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Sessione DB separata e indipendente per l'audit log.

    Problema risolto: quando una chiamata AI fallisce, il get_db principale
    fa rollback di tutta la transazione — incluso il AIRequestLog dell'errore.
    Questo significa che i log degli errori vengono persi silenziosamente.

    Soluzione: usare una sessione separata per scrivere i log di errore,
    con il proprio commit/rollback indipendente dalla sessione principale.
    Così anche se la request fallisce con 500, il log viene sempre salvato.
    """
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
