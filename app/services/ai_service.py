"""
AIService — the core orchestration layer for AI email generation.

Responsibilities:
  1. Select the correct prompt template based on email_type
  2. Call the AI provider via AIProvider protocol
  3. Log every request to AIRequestLog (prompt, response, cost, errors)
  4. Persist the generated email to GeneratedEmail
  5. Return a structured response to the caller

Prompt templates available:
  - formal        : Professional, structured, respectful tone
  - commercial    : Persuasive, benefit-focused, call-to-action
  - follow_up     : Friendly reminder, references previous contact
  - complaint     : Assertive but polite, clear resolution request
  - introduction  : Warm, concise, context-setting first contact
  - thank_you     : Genuine appreciation, specific acknowledgment
  - cold_outreach : Hook-first, value proposition, low-friction CTA
  - apology       : Accountable, empathetic, solution-oriented
  - invitation    : Engaging, clear details, RSVP-focused
  - informal      : Casual, conversational, friendly
"""

import json
import time
import uuid
from dataclasses import dataclass

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
# Cost estimation tables (USD per 1M tokens — approximated for common models)
# ─────────────────────────────────────────────────────────────────────────────

_COST_PER_1M_INPUT: dict[str, float] = {
    # Groq (free tier — cost is effectively $0, tracked for consistency)
    "llama-3.3-70b-versatile": 0.0,
    "llama-3.1-8b-instant": 0.0,
    "mixtral-8x7b-32768": 0.0,
    # OpenAI
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
    "gpt-3.5-turbo": 0.50,
    # Anthropic
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
    """Estimate cost in USD based on token usage and model pricing."""
    input_rate = _COST_PER_1M_INPUT.get(model, 2.50)
    output_rate = _COST_PER_1M_OUTPUT.get(model, 10.00)
    return round(
        (prompt_tokens * input_rate / 1_000_000)
        + (completion_tokens * output_rate / 1_000_000),
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
            "Maintain a respectful, neutral tone throughout. Structure the email with "
            "a clear opening statement, body paragraphs for each point, and a definitive closing."
        ),
        style_guidelines=(
            "- Use 'Dear [Title] [Name]' as salutation\n"
            "- Avoid contractions (use 'I am' not 'I'm')\n"
            "- Use passive voice where appropriate for objectivity\n"
            "- Close with 'Yours sincerely' or 'Kind regards'\n"
            "- Keep paragraphs concise and well-structured"
        ),
    ),

    "commercial": PromptTemplate(
        name="commercial",
        system_context=(
            "You are an expert direct-response copywriter with 15 years of experience "
            "in B2B and B2C sales emails. You know how to craft compelling value "
            "propositions and drive action without being pushy."
        ),
        instruction=(
            "Write a persuasive commercial email. Lead with the strongest benefit. "
            "Address the recipient's pain points, present the solution clearly, "
            "include social proof or urgency if relevant, and close with a single "
            "clear call-to-action."
        ),
        style_guidelines=(
            "- Open with a benefit or problem statement, not a feature\n"
            "- Use 'you' language — focus on the reader, not the sender\n"
            "- Include one clear CTA (call-to-action)\n"
            "- Keep sentences short and punchy\n"
            "- Use bullet points for benefits if listing more than two\n"
            "- Create mild urgency without pressure tactics"
        ),
    ),

    "follow_up": PromptTemplate(
        name="follow_up",
        system_context=(
            "You are a relationship-focused business professional who excels at "
            "maintaining momentum in conversations without being intrusive. "
            "Your follow-up emails always feel timely and helpful, never annoying."
        ),
        instruction=(
            "Write a follow-up email that references a previous interaction. "
            "Be concise and respectful of the recipient's time. Gently remind them "
            "of the value discussed, provide any requested information, and make "
            "it easy for them to respond or take the next step."
        ),
        style_guidelines=(
            "- Reference the previous contact naturally in the opening line\n"
            "- Keep the email short — ideally under 150 words\n"
            "- Offer a clear, low-friction next step\n"
            "- Do not guilt-trip or pressure — stay positive\n"
            "- Use a friendly but professional tone"
        ),
    ),

    "complaint": PromptTemplate(
        name="complaint",
        system_context=(
            "You are a professional advocate skilled at articulating grievances "
            "clearly and constructively. You balance firmness with courtesy, "
            "always focusing on resolution rather than blame."
        ),
        instruction=(
            "Write a complaint email that clearly describes the issue, its impact, "
            "and the desired resolution. Be assertive and factual without being "
            "aggressive. Maintain a professional tone that demands respect while "
            "leaving room for a constructive response."
        ),
        style_guidelines=(
            "- State the issue clearly and factually in the first paragraph\n"
            "- Describe the impact on the sender (time, cost, inconvenience)\n"
            "- Specify the desired resolution explicitly\n"
            "- Set a reasonable response deadline if appropriate\n"
            "- Avoid emotional language, threats, or insults\n"
            "- Close with openness to dialogue"
        ),
    ),

    "introduction": PromptTemplate(
        name="introduction",
        system_context=(
            "You are a networking expert who crafts memorable first impressions. "
            "Your introduction emails are warm, specific, and leave the recipient "
            "wanting to learn more."
        ),
        instruction=(
            "Write an introduction email. Establish who the sender is, why they are "
            "reaching out to this specific person, and what value the connection could "
            "bring to both parties. End with a clear, low-commitment next step."
        ),
        style_guidelines=(
            "- Open with something specific about the recipient to show research\n"
            "- Keep background about the sender brief and relevant\n"
            "- Focus on mutual benefit, not just what the sender wants\n"
            "- Propose a specific, easy next step (a call, a coffee, a quick reply)\n"
            "- Tone: warm, confident, not overly formal"
        ),
    ),

    "thank_you": PromptTemplate(
        name="thank_you",
        system_context=(
            "You are a relationship expert who understands the power of genuine "
            "appreciation. Your thank-you messages feel personal, specific, and "
            "never formulaic."
        ),
        instruction=(
            "Write a sincere thank-you email. Be specific about what is being "
            "appreciated and why it mattered. Make the recipient feel genuinely "
            "valued. If appropriate, mention how you plan to use their help or "
            "what positive outcome resulted."
        ),
        style_guidelines=(
            "- Be specific — name exactly what you are thankful for\n"
            "- Share the impact of their help or gesture\n"
            "- Keep it concise — gratitude does not need to be long\n"
            "- Avoid generic phrases like 'I just wanted to say thank you'\n"
            "- Warm, personal tone — this is not a formal letter"
        ),
    ),

    "cold_outreach": PromptTemplate(
        name="cold_outreach",
        system_context=(
            "You are a growth expert who has sent thousands of cold emails with "
            "exceptional open and reply rates. You know the first line must hook, "
            "the value must be crystal clear, and the ask must be tiny."
        ),
        instruction=(
            "Write a cold outreach email. The opening line must immediately grab "
            "attention (reference something specific about the recipient or their "
            "company). State the value proposition in one sentence. Make a small, "
            "specific ask that is easy to say yes to."
        ),
        style_guidelines=(
            "- First line: specific observation about recipient (not generic flattery)\n"
            "- Value prop: one sentence, what's in it for them\n"
            "- Social proof: one brief reference if available in context\n"
            "- CTA: ask for something tiny (15 min call, a quick reply, a yes/no)\n"
            "- Total length: under 100 words ideally\n"
            "- No attachments mentioned, no lengthy introductions"
        ),
    ),

    "apology": PromptTemplate(
        name="apology",
        system_context=(
            "You are a communications specialist in crisis and relationship repair. "
            "You craft apologies that feel genuine, take full accountability, and "
            "restore trust through concrete action plans."
        ),
        instruction=(
            "Write a sincere apology email. Take clear accountability without "
            "making excuses. Acknowledge the impact on the recipient, explain what "
            "went wrong briefly, and most importantly describe concrete steps being "
            "taken to prevent recurrence."
        ),
        style_guidelines=(
            "- Open with the apology directly — do not bury it\n"
            "- Acknowledge the specific impact on the recipient\n"
            "- Accept responsibility without excessive justification\n"
            "- State clearly what corrective action is being taken\n"
            "- Avoid 'if you were offended' — take full ownership\n"
            "- Close with commitment to the relationship"
        ),
    ),

    "invitation": PromptTemplate(
        name="invitation",
        system_context=(
            "You are an event communications specialist who creates invitations "
            "that make recipients feel genuinely excited and personally selected."
        ),
        instruction=(
            "Write an invitation email. Convey the event details clearly "
            "(what, when, where, why it matters). Create excitement and make the "
            "recipient feel their presence is valued. Include a clear RSVP instruction."
        ),
        style_guidelines=(
            "- Lead with the most exciting aspect of the event\n"
            "- Include all logistics: date, time, location, format\n"
            "- Explain why the recipient specifically is invited\n"
            "- Create anticipation with one compelling detail about the experience\n"
            "- Make the RSVP process simple and clear\n"
            "- Tone: warm and welcoming"
        ),
    ),

    "informal": PromptTemplate(
        name="informal",
        system_context=(
            "You write like a real human being — casual, warm, and conversational. "
            "Your emails feel like they were written by a friend, not a corporate bot."
        ),
        instruction=(
            "Write a casual, friendly email. Use natural, conversational language. "
            "Skip the formal structure — write how you would actually talk to someone "
            "you know. Keep it light and genuine."
        ),
        style_guidelines=(
            "- Use contractions freely (it's, I'm, we'll)\n"
            "- Short sentences and paragraphs\n"
            "- First name only in salutation (or none at all)\n"
            "- Can use casual sign-offs: 'Cheers', 'Talk soon', 'Best'\n"
            "- No corporate jargon or buzzwords\n"
            "- A touch of personality is welcome"
        ),
    ),
}

# Default fallback template
_DEFAULT_TEMPLATE = _TEMPLATES["formal"]


def get_template(email_type: str) -> PromptTemplate:
    """Return the prompt template for the given email type."""
    return _TEMPLATES.get(email_type, _DEFAULT_TEMPLATE)


def build_prompt(
    template: PromptTemplate,
    recipient: str,
    context: str,
    language: str,
    tone: str | None,
) -> str:
    """Assemble the full prompt string from template + request parameters."""
    tone_clause = f"\n- Tone override: {tone}" if tone else ""
    language_name = {
        "it": "Italian", "en": "English", "fr": "French",
        "de": "German", "es": "Spanish", "pt": "Portuguese",
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

    Usage:
        service = AIService(db=session, ai=provider)
        result = await service.generate_and_save(request, user_id)
    """

    def __init__(self, db: AsyncSession, ai: AIProvider) -> None:
        self._db = db
        self._ai = ai

    async def generate_and_save(
        self,
        request: GenerateEmailRequest,
        user_id: uuid.UUID,
    ) -> GeneratedEmailResponse:
        """
        Full pipeline:
        1. Select prompt template
        2. Build prompt
        3. Call AI provider (with error handling)
        4. Persist AIRequestLog (always, even on error)
        5. Persist GeneratedEmail (only on success)
        6. Return response schema
        """
        from app.schemas.schemas import GeneratedEmailResponse

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

        # ── Persist AIRequestLog ──────────────────────────────────────────────
        request_log = AIRequestLog(
            user_id=user_id,
            email_type=request.email_type,
            prompt_template=template.name,
            prompt_used=prompt,
            language=request.language,
            tone=request.tone,
            ai_provider=result.provider if result else settings.ai_provider,
            ai_model=model_name,
            raw_response=raw_response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost,
            status=status,
            error_type=error_type,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self._db.add(request_log)
        await self._db.flush()

        # ── Re-raise if error (log is already saved) ──────────────────────────
        if result is None:
            await self._db.commit()
            if status == "rate_limited":
                raise AIProviderRateLimitError(error_message)
            elif status == "timeout":
                raise AIProviderTimeoutError(error_message)
            else:
                raise AIProviderError(error_message or "Unknown AI error")

        # ── Persist GeneratedEmail ────────────────────────────────────────────
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
        """Return recent AI request logs for the user (for debugging / cost review)."""
        from sqlalchemy import select
        q = (
            select(AIRequestLog)
            .where(AIRequestLog.user_id == user_id)
            .order_by(AIRequestLog.created_at.desc())
            .limit(limit)
        )
        result = await self._db.execute(q)
        return list(result.scalars().all())
