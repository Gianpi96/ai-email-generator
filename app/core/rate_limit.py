"""
Rate limiting con slowapi.

Limiti configurati:
- Globale:      100 req/minuto per IP  (tutti gli endpoint)
- Endpoint AI:   10 req/minuto per IP  (solo /emails/generate)

slowapi è un wrapper di limits per FastAPI/Starlette.
Usa la memoria locale come storage (sufficiente per singolo processo).
Per deployment multi-processo usare Redis come storage.

Installazione richiesta: pip install slowapi
"""

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ── Limiter globale ───────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,  # identifica il client per IP
    default_limits=["100/minute"],  # limite globale per tutti gli endpoint
    headers_enabled=True,  # aggiunge header X-RateLimit-* alla risposta
    # storage_uri="redis://localhost:6379"  # decommentare per Redis in produzione
)

# ── Limite specifico per endpoint AI ─────────────────────────────────────────

# Da usare come decorator sui singoli endpoint:
#   @limiter.limit("10/minute")
#   async def generate_email(...): ...
#
# Il limite "10/minute" sovrascrive il default "100/minute" per quell'endpoint.

AI_RATE_LIMIT = "10/minute"
DEFAULT_RATE_LIMIT = "100/minute"
