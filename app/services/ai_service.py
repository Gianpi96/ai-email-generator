"""
AIService — the core orchestration layer for AI email generation.

Responsibilities:
  1. Select the correct prompt template based on email_type
  2. Call the AI provider via AIProvider protocol
  3. Log every request to AIRequestLog (always, even on error)
  4. Persist the generated email to GeneratedEmail (only on success)
  5. Return a structured response to the caller

Key design decision — dual session pattern:
  - self._db      : sessione principale per GeneratedEmail
  - self._audit_db: sessione SEPARATA per AIRequestLog

  Questo garantisce che i log degli errori vengano sempre salvati,
  anche quando la sessione principale fa rollback per un errore AI.
  Senza questa separazione, i log degli errori verrebbero persi silenziosamente.

Prompt templates available:
  - formal, commercial, follow_up, complaint, introduction
  - thank_you, cold_outreach, apology, invitation, informal
"""

import json
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AIProviderError, AIProviderRateLimitError, AIProviderTimeoutError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.models.models import AIRequestLog, GeneratedEmail
from app.schemas.schemas import GenerateEmailRequest, GeneratedEmailResponse
from app.services.ai_provider import AIEmailResult, AIProvider

logger = get_logger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# Cost estimation (USD per 1M tokens)
# ─────────────────────────────────────────────────────────────────────────────

_COST_PER_1M_INPUT: dict[str, float] = {
    "llama-3.3-70b-versatile": 0.0,
    "llama-3.1-8b-instant": 0.0,
    "mixtral-8x7b-32768": 0.0,
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
    "gpt-3.5-turbo": 0.50,
    "claude-sonnet-4-20250514": 3.00,
    "claude-haiku-4-5-20251001": 0.25,
}

_COST_PER_1M_OUTPUT: dict[str, float] = {
    "llama-3.3-70b-versatile": 0.0,
    "llama-3.1-8b-instant": 0.0,
    "mixtral-8x7b-32768": 0.0,
    "gpt-4o": 10.00,
    "gpt-4o-mini": 0.60,
    "gpt-3.5-turbo": 1.50,
    "claude-sonnet-4-20250514": 15.00,
    "claude-haiku-4-5-20251001": 1.25,
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_rate = _COST_PER_1M_INPUT.get(model, 2.50)
    output_rate = _COST_PER_1M_OUTPUT.get(model, 10.00)
    return round(
        (prompt_tokens * input_rate / 1_000_000) + (completion_tokens * output_rate / 1_000_000),
        8,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Prompt Templates
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    system_context: str
    instruction: str
    style_guidelines: str


_TEMPLATES: dict[str, PromptTemplate] = {
    "formal": PromptTemplate(
        name="formal",
        system_context=(
            "You are a senior corporate communications expert specializing in "
            "formal business correspondence. Your writing is precise, respectful, "
            "and adheres strictly to professional etiquette."
        ),
        instruction=(
            "Write a formal business email. Use professional salutations and closings. "
            "Maintain a respectful, neutral tone throughout."
        ),
        style_guidelines=(
            "- Use 'Dear [Title] [Name]' as salutation\n"
            "- Avoid contractions\n"
            "- Close with 'Yours sincerely' or 'Kind regards'\n"
            "- Keep paragraphs concise and well-structured"
        ),
    ),
    "commercial": PromptTemplate(
        name="commercial",
        system_context=(
            "You are an expert direct-response copywriter with 15 years of experience "
            "in B2B and B2C sales emails."
        ),
        instruction=(
            "Write a persuasive commercial email. Lead with the strongest benefit. "
            "Close with a single clear call-to-action."
        ),
        style_guidelines=(
            "- Open with a benefit or problem statement\n"
            "- Use 'you' language\n"
            "- Include one clear CTA\n"
            "- Keep sentences short and punchy"
        ),
    ),
    "follow_up": PromptTemplate(
        name="follow_up",
        system_context=(
            "You are a relationship-focused business professional who excels at "
            "maintaining momentum without being intrusive."
        ),
        instruction=("Write a follow-up email. Be concise and respectful of the recipient's time."),
        style_guidelines=(
            "- Reference the previous contact naturally\n"
            "- Keep under 150 words\n"
            "- Offer a clear, low-friction next step"
        ),
    ),
    "complaint": PromptTemplate(
        name="complaint",
        system_context=(
            "You are a professional advocate skilled at articulating grievances "
            "clearly and constructively."
        ),
        instruction=("Write a complaint email. Be assertive and factual without being aggressive."),
        style_guidelines=(
            "- State the issue clearly in the first paragraph\n"
            "- Describe the impact\n"
            "- Specify the desired resolution\n"
            "- Avoid emotional language"
        ),
    ),
    "introduction": PromptTemplate(
        name="introduction",
        system_context=(
            "You are a networking expert who crafts memorable "
            "first impressions."
        ),
        instruction=(
            "Write an introduction email. Establish who the "
            "sender is and why they are reaching out."
        ),
        style_guidelines=(
            "- Open with something specific about the recipient\n"
            "- Focus on mutual benefit\n"
            "- Propose a specific, easy next step"
        ),
    ),
    "thank_you": PromptTemplate(
        name="thank_you",
        system_context=(
            "You are a relationship expert who understands "
            "the power of genuine appreciation."
        ),
        instruction=(
            "Write a sincere thank-you email. Be specific about "
            "what is being appreciated."
        ),
        style_guidelines=(
            "- Name exactly what you are thankful for\n- Share the impact\n- Keep it concise"
        ),
    ),
    "cold_outreach": PromptTemplate(
        name="cold_outreach",
        system_context=(
            "You are a growth expert who has sent thousands of cold emails with "
            "exceptional open and reply rates."
        ),
        instruction=(
            "Write a cold outreach email. The opening line must immediately grab attention. "
            "Make a small, specific ask."
        ),
        style_guidelines=(
            "- First line: specific observation about recipient\n"
            "- Value prop: one sentence\n"
            "- CTA: ask for something tiny\n"
            "- Total length: under 100 words"
        ),
    ),
    "apology": PromptTemplate(
        name="apology",
        system_context=(
            "You are a communications specialist in crisis "
            "and relationship repair."
        ),
        instruction=(
            "Write a sincere apology email. Take clear "
            "accountability without making excuses."
        ),
        style_guidelines=(
            "- Open with the apology directly\n"
            "- Acknowledge the specific impact\n"
            "- State corrective action\n"
            "- Avoid 'if you were offended'"
        ),
    ),
    "invitation": PromptTemplate(
        name="invitation",
        system_context="You are an event communications specialist.",
        instruction="Write an invitation email with clear event details and RSVP instruction.",
        style_guidelines=(
            "- Lead with the most exciting aspect\n"
            "- Include all logistics: date, time, location\n"
            "- Make the RSVP simple and clear"
        ),
    ),
    "informal": PromptTemplate(
        name="informal",
        system_context="You write like a real human being — casual, warm, and conversational.",
        instruction="Write a casual, friendly email using natural conversational language.",
        style_guidelines=("- Use contractions freely\n- Short sentences\n- No corporate jargon"),
    ),
}

_DEFAULT_TEMPLATE = _TEMPLATES["formal"]


def get_template(email_type: str) -> PromptTemplate:
    return _TEMPLATES.get(email_type, _DEFAULT_TEMPLATE)


def build_prompt(
    template: PromptTemplate,
    recipient: str,
    context: str,
    language: str,
    tone: str | None,
) -> str:
    tone_clause = f"\n- Tone override: {tone}" if tone else ""
    language_name = {
        "it": "Italian",
        "en": "English",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
        "pt": "Portuguese",
    }.get(language, language.upper())

    return f"""SYSTEM CONTEXT:
{template.system_context}

TASK:
{template.instruction}

STYLE GUIDELINES:
{template.style_guidelines}{tone_clause}

EMAIL PARAMETERS:
- Recipient: {recipient}
- Language: {language_name} (write the entire email in {language_name})
- Key points / context to include:
{context}

OUTPUT FORMAT:
Respond ONLY with a valid JSON object. No markdown fences, no explanation.
{{
  "subject": "<compelling subject line in {language_name}>",
  "body": "<complete email body with salutation, paragraphs, and sign-off in {language_name}>"
}}"""


# ─────────────────────────────────────────────────────────────────────────────
# AIService
# ─────────────────────────────────────────────────────────────────────────────


class AIService:
    """
    Orchestrates AI email generation with full audit logging.

    Dual session pattern:
    - _db       : sessione principale (GeneratedEmail, commit/rollback con la request)
    - _audit_db : sessione separata (AIRequestLog, commit indipendente)

    Questo garantisce che i log vengano sempre salvati, anche in caso di errore.
    """

    def __init__(self, db: AsyncSession, audit_db: AsyncSession, ai: AIProvider) -> None:
        self._db = db
        self._audit_db = audit_db
        self._ai = ai

    async def _save_request_log(self, log_data: dict) -> AIRequestLog:
        """
        Salva il log su una sessione separata con commit immediato.
        Garantisce la persistenza anche se la sessione principale fa rollback.
        """
        request_log = AIRequestLog(**log_data)
        self._audit_db.add(request_log)
        await self._audit_db.flush()
        await self._audit_db.commit()
        return request_log

    async def generate_and_save(
        self,
        request: GenerateEmailRequest,
        user_id: uuid.UUID,
    ) -> GeneratedEmailResponse:
        template = get_template(request.email_type)
        prompt = build_prompt(
            template=template,
            recipient=request.recipient,
            context=request.context,
            language=request.language,
            tone=request.tone,
        )

        log = logger.bind(
            user_id=str(user_id),
            email_type=request.email_type,
            template=template.name,
            provider=settings.ai_provider,
        )
        log.info("Starting AI email generation")

        # ── Call AI provider ──────────────────────────────────────────────────
        start_ms = int(time.monotonic() * 1000)
        result: AIEmailResult | None = None
        status = "success"
        error_type: str | None = None
        error_message: str | None = None
        raw_response: str | None = None

        try:
            result = await self._ai.generate_email(
                email_type=request.email_type,
                recipient=request.recipient,
                context=request.context,
                language=request.language,
                tone=request.tone,
            )
            raw_response = json.dumps({"subject": result.subject, "body": result.body})
            log.info(
                "AI generation successful",
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )

        except AIProviderRateLimitError as exc:
            status = "rate_limited"
            error_type = "RateLimitError"
            error_message = str(exc)
            log.warning("AI rate limit hit")

        except AIProviderTimeoutError as exc:
            status = "timeout"
            error_type = "TimeoutError"
            error_message = str(exc)
            log.error("AI request timed out")

        except AIProviderError as exc:
            status = "error"
            error_type = type(exc).__name__
            error_message = str(exc)
            log.error("AI provider error", error=str(exc))

        duration_ms = int(time.monotonic() * 1000) - start_ms

        # ── Estimate cost ─────────────────────────────────────────────────────
        prompt_tokens = result.prompt_tokens if result else None
        completion_tokens = result.completion_tokens if result else None
        total_tokens = (
            (prompt_tokens or 0) + (completion_tokens or 0)
            if (prompt_tokens is not None or completion_tokens is not None)
            else None
        )
        model_name = result.model if result else settings.groq_model
        estimated_cost = (
            _estimate_cost(model_name, prompt_tokens or 0, completion_tokens or 0)
            if result
            else None
        )

        # ── Persist AIRequestLog (sessione separata — sempre salvato) ─────────
        request_log = await self._save_request_log(
            {
                "user_id": user_id,
                "email_type": request.email_type,
                "prompt_template": template.name,
                "prompt_used": prompt,
                "language": request.language,
                "tone": request.tone,
                "ai_provider": result.provider if result else settings.ai_provider,
                "ai_model": model_name,
                "raw_response": raw_response,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": estimated_cost,
                "status": status,
                "error_type": error_type,
                "error_message": error_message,
                "duration_ms": duration_ms,
            }
        )

        # ── Re-raise se errore (il log è già salvato in modo sicuro) ─────────
        if result is None:
            if status == "rate_limited":
                raise AIProviderRateLimitError(error_message)
            elif status == "timeout":
                raise AIProviderTimeoutError(error_message)
            else:
                raise AIProviderError(error_message or "Unknown AI error")

        # ── Persist GeneratedEmail (sessione principale) ───────────────────────
        email = GeneratedEmail(
            user_id=user_id,
            email_type=request.email_type,
            recipient=request.recipient,
            context=request.context,
            subject=result.subject,
            body=result.body,
            ai_provider=result.provider,
            ai_model=result.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            request_log_id=request_log.id,
        )
        self._db.add(email)
        await self._db.flush()

        log.info(
            "Email saved",
            email_id=str(email.id),
            log_id=str(request_log.id),
            cost_usd=estimated_cost,
            duration_ms=duration_ms,
        )

        return GeneratedEmailResponse.model_validate(email)

    async def get_request_logs(
        self,
        user_id: uuid.UUID,
        limit: int = 50,
    ) -> list[AIRequestLog]:
        q = (
            select(AIRequestLog)
            .where(AIRequestLog.user_id == user_id)
            .order_by(AIRequestLog.created_at.desc())
            .limit(limit)
        )
        result = await self._audit_db.execute(q)
        return list(result.scalars().all())
