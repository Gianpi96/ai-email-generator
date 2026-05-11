"""
Rate limiting con slowapi.

Limiti configurati:
- Globale:      100 req/minuto per IP  (tutti gli endpoint)
- Endpoint AI:   10 req/minuto per IP  (solo /emails/generate)
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    headers_enabled=True,
)

AI_RATE_LIMIT = "10/minute"
DEFAULT_RATE_LIMIT = "100/minute"
