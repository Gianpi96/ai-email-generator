"""
EmailService — history with filters, pagination, CSV export.
Generation is delegated to AIService.
"""

import csv
import io
import math
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.models import GeneratedEmail
from app.schemas.schemas import (
    GeneratedEmailResponse,
    HistoryFilters,
    PaginatedResponse,
)

logger = get_logger(__name__)

# CSV columns and their labels
_CSV_COLUMNS = [
    ("id", "ID"),
    ("email_type", "Email Type"),
    ("recipient", "Recipient"),
    ("subject", "Subject"),
    ("body", "Body"),
    ("context", "Context"),
    ("ai_provider", "AI Provider"),
    ("ai_model", "AI Model"),
    ("prompt_tokens", "Prompt Tokens"),
    ("completion_tokens", "Completion Tokens"),
    ("created_at", "Created At"),
]


class EmailService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ── Query builder ─────────────────────────────────────────────────────────

    def _apply_filters(
        self,
        query,
        user_id: uuid.UUID,
        filters: HistoryFilters,
    ):
        """Apply all filters to a base SELECT query."""
        query = query.where(GeneratedEmail.user_id == user_id)

        # Filter: email_type
        if filters.email_type:
            query = query.where(GeneratedEmail.email_type == filters.email_type)

        # Filter: ai_provider
        if filters.ai_provider:
            query = query.where(GeneratedEmail.ai_provider == filters.ai_provider)

        # Filter: date range
        if filters.date_from:
            dt = filters.date_from
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            query = query.where(GeneratedEmail.created_at >= dt)

        if filters.date_to:
            dt = filters.date_to
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            query = query.where(GeneratedEmail.created_at <= dt)

        # Filter: full-text search across subject, body, recipient
        if filters.search:
            term = f"%{filters.search}%"
            query = query.where(
                or_(
                    GeneratedEmail.subject.ilike(term),
                    GeneratedEmail.body.ilike(term),
                    GeneratedEmail.recipient.ilike(term),
                    GeneratedEmail.context.ilike(term),
                )
            )

        return query

    def _apply_sorting(self, query, filters: HistoryFilters):
        """Apply sorting to query."""
        column_map = {
            "created_at": GeneratedEmail.created_at,
            "email_type": GeneratedEmail.email_type,
            "recipient": GeneratedEmail.recipient,
            "subject": GeneratedEmail.subject,
        }
        col = column_map.get(filters.sort_by, GeneratedEmail.created_at)
        order_fn = desc if filters.sort_order == "desc" else asc
        return query.order_by(order_fn(col))

    # ── Public methods ────────────────────────────────────────────────────────

    async def get_history(
        self,
        user_id: uuid.UUID,
        filters: HistoryFilters,
    ) -> PaginatedResponse:
        """Return paginated + filtered email history."""
        base_q = select(GeneratedEmail)
        base_q = self._apply_filters(base_q, user_id, filters)

        # Count total matching rows
        count_q = select(func.count()).select_from(base_q.subquery())
        total: int = (await self._db.execute(count_q)).scalar_one()

        # Fetch page
        rows_q = self._apply_sorting(base_q, filters)
        rows_q = rows_q.offset(filters.offset).limit(filters.page_size)
        rows = (await self._db.execute(rows_q)).scalars().all()

        pages = math.ceil(total / filters.page_size) if total else 0

        # Build filters_applied metadata for response
        filters_applied = {
            k: v
            for k, v in {
                "email_type": filters.email_type,
                "date_from": filters.date_from.isoformat() if filters.date_from else None,
                "date_to": filters.date_to.isoformat() if filters.date_to else None,
                "search": filters.search,
                "ai_provider": filters.ai_provider,
                "sort_by": filters.sort_by,
                "sort_order": filters.sort_order,
            }.items()
            if v is not None
        }

        logger.info(
            "History retrieved",
            user_id=str(user_id),
            total=total,
            page=filters.page,
            filters=filters_applied,
        )

        return PaginatedResponse(
            items=[GeneratedEmailResponse.model_validate(r) for r in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
            pages=pages,
            filters_applied=filters_applied,
        )

    async def get_by_id(self, email_id: uuid.UUID, user_id: uuid.UUID) -> GeneratedEmailResponse:
        row = await self._db.get(GeneratedEmail, email_id)
        if not row or row.user_id != user_id:
            raise NotFoundError("Email not found.")
        return GeneratedEmailResponse.model_validate(row)

    async def delete(self, email_id: uuid.UUID, user_id: uuid.UUID) -> None:
        row = await self._db.get(GeneratedEmail, email_id)
        if not row or row.user_id != user_id:
            raise NotFoundError("Email not found.")
        await self._db.delete(row)

    # ── CSV Export ────────────────────────────────────────────────────────────

    async def export_csv_stream(
        self,
        user_id: uuid.UUID,
        filters: HistoryFilters,
    ) -> AsyncGenerator[str, None]:
        """
        Async generator that yields CSV rows one chunk at a time.
        Used with FastAPI StreamingResponse for memory-efficient export.

        Streams in batches of 100 rows to avoid loading all data into memory.
        """
        BATCH_SIZE = 100

        # Write header
        header_buf = io.StringIO()
        writer = csv.writer(header_buf, quoting=csv.QUOTE_ALL)
        writer.writerow([label for _, label in _CSV_COLUMNS])
        yield header_buf.getvalue()

        # Stream rows in batches
        offset = 0
        total_exported = 0

        while True:
            base_q = select(GeneratedEmail)
            base_q = self._apply_filters(base_q, user_id, filters)
            base_q = self._apply_sorting(base_q, filters)
            base_q = base_q.offset(offset).limit(BATCH_SIZE)

            rows = (await self._db.execute(base_q)).scalars().all()
            if not rows:
                break

            batch_buf = io.StringIO()
            writer = csv.writer(batch_buf, quoting=csv.QUOTE_ALL)

            for row in rows:
                writer.writerow(
                    [
                        str(getattr(row, field)) if getattr(row, field) is not None else ""
                        for field, _ in _CSV_COLUMNS
                    ]
                )

            yield batch_buf.getvalue()
            total_exported += len(rows)
            offset += BATCH_SIZE

            if len(rows) < BATCH_SIZE:
                break

        logger.info(
            "CSV export complete",
            user_id=str(user_id),
            total_exported=total_exported,
        )
