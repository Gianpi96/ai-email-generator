"""
Application factory — keeps main.py thin and testable.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.v1.router import api_router
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.db.session import engine
from app.models.models import Base  # noqa: F401 — needed to register tables

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    configure_logging()
    logger.info("Starting up", env=settings.app_env)

    # Create tables (use Alembic migrations in production)
    if settings.app_env != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured")

    yield

    await engine.dispose()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="AI-powered email generation backend with JWT auth and history.",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Middleware ─────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Exception handlers ────────────────────────────────────────────────────
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("Application error", detail=exc.detail, path=request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", path=request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal server error occurred."},
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(api_router)

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"], include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.app_version}

    # ── Prometheus metrics ────────────────────────────────────────────────────
    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    return app
