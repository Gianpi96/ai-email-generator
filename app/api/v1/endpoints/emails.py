"""
/api/v1/emails — generation, history with filters, CSV export.
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser
from app.core.rate_limit import AI_RATE_LIMIT, limiter
from app.db.session import get_db
from app.schemas.schemas import (
    EMAIL_TYPES,
    GenerateEmailRequest,
    GeneratedEmailResponse,
    HistoryFilters,
    PaginatedResponse,
)
from app.services.ai_provider import AIProvider, get_ai_provider
from app.services.ai_service import AIService
from app.services.email_service import EmailService

router = APIRouter(prefix="/emails", tags=["Emails"])


# ── Generate (rate limited: 10/min per IP) ────────────────────────────────────


@router.post(
    "/generate",
    response_model=GeneratedEmailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an AI email using a type-specific prompt template",
    description=(
        "Selects the prompt template matching `email_type`, calls the configured "
        "AI provider, logs the full request (prompt, response, cost, duration) "
        "to the audit log, and persists the generated email.\n\n"
        "**Rate limit:** 10 requests/minute per IP."
    ),
)
@limiter.limit(AI_RATE_LIMIT)
async def generate_email(
    request: Request,  # richiesto da slowapi per leggere l'IP
    response: Response,  # richiesto da slowapi per iniettare gli header X-RateLimit-*
    body: GenerateEmailRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    ai: Annotated[AIProvider, Depends(get_ai_provider)],
) -> GeneratedEmailResponse:
    service = AIService(db=db, ai=ai)
    return await service.generate_and_save(body, user_id=current_user.id)


# ── History with filters (rate limited: default 100/min) ─────────────────────


@router.get(
    "/history",
    response_model=PaginatedResponse,
    summary="Paginated email history with filters",
    description="""
Returns a paginated list of generated emails for the current user.

**Filters available:**
- `email_type` — filter by type (formal, commercial, follow_up, etc.)
- `date_from` / `date_to` — filter by creation date range (ISO 8601)
- `search` — full-text search across subject, body, recipient, context
- `ai_provider` — filter by provider (groq, openai, anthropic)

**Sorting:**
- `sort_by` — field to sort by: `created_at`, `email_type`, `recipient`, `subject`
- `sort_order` — `asc` or `desc` (default: `desc`)

**Rate limit:** 100 requests/minute per IP.
    """,
)
async def get_history(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    email_type: str | None = Query(
        default=None,
        description=f"Filter by email type. One of: {', '.join(EMAIL_TYPES)}",
    ),
    date_from: datetime | None = Query(
        default=None,
        description="Filter emails created after this date (ISO 8601)",
    ),
    date_to: datetime | None = Query(
        default=None,
        description="Filter emails created before this date (ISO 8601)",
    ),
    search: str | None = Query(
        default=None,
        description="Search text in subject, body, recipient and context",
        max_length=200,
    ),
    ai_provider: str | None = Query(
        default=None,
        description="Filter by AI provider (groq, openai, anthropic)",
    ),
    sort_by: str = Query(
        default="created_at",
        description="Sort field: created_at | email_type | recipient | subject",
    ),
    sort_order: str = Query(
        default="desc",
        description="Sort direction: asc | desc",
    ),
) -> PaginatedResponse:
    filters = HistoryFilters(
        page=page,
        page_size=page_size,
        email_type=email_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        ai_provider=ai_provider,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    service = EmailService(db=db)
    return await service.get_history(current_user.id, filters)


# ── CSV Export ────────────────────────────────────────────────────────────────


@router.get(
    "/history/export",
    summary="Export email history as CSV",
    description="""
Downloads all matching emails as a UTF-8 CSV file.

Accepts the **same filters** as `GET /history`.
Uses **StreamingResponse** — streamed in batches of 100 rows.

**Rate limit:** 100 requests/minute per IP.
    """,
    response_class=StreamingResponse,
)
async def export_history_csv(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    email_type: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    ai_provider: str | None = Query(default=None),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc"),
) -> StreamingResponse:
    filters = HistoryFilters(
        page=1,
        page_size=100,
        email_type=email_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        ai_provider=ai_provider,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    service = EmailService(db=db)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    type_part = f"_{email_type}" if email_type else ""
    filename = f"emails{type_part}_{date_str}.csv"

    return StreamingResponse(
        service.export_csv_stream(current_user.id, filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── AI Request Logs ───────────────────────────────────────────────────────────


@router.get(
    "/logs",
    summary="AI request audit logs — prompt, cost, errors",
)
async def get_ai_logs(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    ai: Annotated[AIProvider, Depends(get_ai_provider)],
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by status: success | error | timeout | rate_limited",
    ),
) -> list[dict]:
    service = AIService(db=db, ai=ai)
    logs = await service.get_request_logs(current_user.id, limit=limit)
    if status_filter:
        logs = [l for l in logs if l.status == status_filter]
    return [
        {
            "id": str(log.id),
            "email_type": log.email_type,
            "prompt_template": log.prompt_template,
            "ai_provider": log.ai_provider,
            "ai_model": log.ai_model,
            "status": log.status,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "total_tokens": log.total_tokens,
            "estimated_cost_usd": log.estimated_cost_usd,
            "duration_ms": log.duration_ms,
            "error_type": log.error_type,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


# ── Single email ──────────────────────────────────────────────────────────────


@router.get(
    "/{email_id}",
    response_model=GeneratedEmailResponse,
    summary="Get a single generated email by ID",
)
async def get_email(
    email_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GeneratedEmailResponse:
    service = EmailService(db=db)
    return await service.get_by_id(email_id, current_user.id)


@router.delete(
    "/{email_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a generated email",
)
async def delete_email(
    email_id: uuid.UUID,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    service = EmailService(db=db)
    await service.delete(email_id, current_user.id)
