"""
AI provider abstraction.
Supports Anthropic, OpenAI, and Groq (free tier).
"""

import json
from dataclasses import dataclass
from typing import Protocol

import anthropic
import openai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.exceptions import AIProviderError, AIProviderRateLimitError, AIProviderTimeoutError
from app.core.logging import get_logger
from app.core.settings import get_settings

logger = get_logger(__name__)
settings = get_settings()


# ── Result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AIEmailResult:
    subject: str
    body: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


# ── Protocol ──────────────────────────────────────────────────────────────────


class AIProvider(Protocol):
    async def generate_email(
        self,
        email_type: str,
        recipient: str,
        context: str,
        language: str = "en",
        tone: str | None = None,
    ) -> AIEmailResult: ...


# ── Prompt ────────────────────────────────────────────────────────────────────


def _build_prompt(
    email_type: str, recipient: str, context: str, language: str, tone: str | None
) -> str:
    tone_clause = f" The tone should be {tone}." if tone else ""
    return f"""You are an expert email copywriter.

Write a professional email with the following requirements:
- Email type: {email_type}
- Recipient: {recipient}
- Language: {language}{tone_clause}
- Key context / points to include:
{context}

Respond ONLY with a JSON object (no markdown fences) in this exact format:
{{
  "subject": "<email subject line>",
  "body": "<full email body with proper formatting, salutation and sign-off>"
}}"""


# ── Anthropic ─────────────────────────────────────────────────────────────────


class AnthropicProvider:
    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    @retry(
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APITimeoutError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def generate_email(
        self,
        email_type: str,
        recipient: str,
        context: str,
        language: str = "en",
        tone: str | None = None,
    ) -> AIEmailResult:
        prompt = _build_prompt(email_type, recipient, context, language, tone)
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError as exc:
            raise AIProviderRateLimitError() from exc
        except anthropic.APITimeoutError as exc:
            raise AIProviderTimeoutError() from exc
        except anthropic.APIError as exc:
            raise AIProviderError(str(exc)) from exc

        try:
            parsed = json.loads(response.content[0].text.strip())
        except json.JSONDecodeError as exc:
            raise AIProviderError("AI returned non-JSON output.") from exc

        return AIEmailResult(
            subject=parsed["subject"],
            body=parsed["body"],
            provider="anthropic",
            model=self._model,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )


# ── OpenAI ────────────────────────────────────────────────────────────────────


class OpenAIProvider:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    @retry(
        retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def generate_email(
        self,
        email_type: str,
        recipient: str,
        context: str,
        language: str = "en",
        tone: str | None = None,
    ) -> AIEmailResult:
        prompt = _build_prompt(email_type, recipient, context, language, tone)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                max_tokens=1024,
                temperature=0.7,
            )
        except openai.RateLimitError as exc:
            raise AIProviderRateLimitError() from exc
        except openai.APITimeoutError as exc:
            raise AIProviderTimeoutError() from exc
        except openai.APIError as exc:
            raise AIProviderError(str(exc)) from exc

        try:
            parsed = json.loads(response.choices[0].message.content or "")
        except json.JSONDecodeError as exc:
            raise AIProviderError("AI returned non-JSON output.") from exc

        usage = response.usage
        return AIEmailResult(
            subject=parsed["subject"],
            body=parsed["body"],
            provider="openai",
            model=self._model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )


# ── Groq (free tier) ──────────────────────────────────────────────────────────


class GroqProvider:
    """
    Groq uses an OpenAI-compatible API — we reuse the openai SDK
    pointing it at Groq's base URL.
    """

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not configured.")
        self._client = openai.AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self._model = settings.groq_model

    @retry(
        retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def generate_email(
        self,
        email_type: str,
        recipient: str,
        context: str,
        language: str = "en",
        tone: str | None = None,
    ) -> AIEmailResult:
        prompt = _build_prompt(email_type, recipient, context, language, tone)
        log = logger.bind(provider="groq", model=self._model)
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.7,
            )
        except openai.RateLimitError as exc:
            log.warning("Groq rate limit hit")
            raise AIProviderRateLimitError() from exc
        except openai.APITimeoutError as exc:
            log.error("Groq timeout")
            raise AIProviderTimeoutError() from exc
        except openai.APIError as exc:
            log.error("Groq API error", error=str(exc))
            raise AIProviderError(str(exc)) from exc

        raw = response.choices[0].message.content or ""
        # Groq sometimes wraps JSON in markdown fences — strip them
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.error("Failed to parse Groq response", raw=raw[:200])
            raise AIProviderError("AI returned non-JSON output.") from exc

        usage = response.usage
        return AIEmailResult(
            subject=parsed["subject"],
            body=parsed["body"],
            provider="groq",
            model=self._model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )


# ── Factory ───────────────────────────────────────────────────────────────────


def get_ai_provider() -> AIProvider:
    match settings.ai_provider:
        case "anthropic":
            return AnthropicProvider()
        case "openai":
            return OpenAIProvider()
        case "groq":
            return GroqProvider()
        case _:
            raise RuntimeError(f"Unknown AI provider: {settings.ai_provider!r}")
