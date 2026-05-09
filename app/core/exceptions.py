"""
Domain exceptions — mapped to HTTP responses in the exception handlers.
"""

from http import HTTPStatus


class AppError(Exception):
    """Base class for all application errors."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    detail: str = "An unexpected error occurred."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        super().__init__(self.detail)


# ── Auth ──────────────────────────────────────────────────────────────────────

class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    detail = "Could not validate credentials."


class InvalidTokenError(AuthenticationError):
    detail = "Token is invalid or has expired."


class PermissionDeniedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    detail = "You do not have permission to perform this action."


# ── Resources ─────────────────────────────────────────────────────────────────

class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    detail = "Resource not found."


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    detail = "Resource already exists."


# ── AI Provider ───────────────────────────────────────────────────────────────

class AIProviderError(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    detail = "AI provider returned an error."


class AIProviderRateLimitError(AIProviderError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    detail = "AI provider rate limit exceeded. Please retry later."


class AIProviderTimeoutError(AIProviderError):
    detail = "AI provider request timed out."


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationError(AppError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    detail = "Validation failed."
