"""
Request logging middleware.

Logga ogni richiesta HTTP con:
- metodo, path, query string
- status code della risposta
- tempo di risposta in ms
- IP del client
- User-Agent

Usa il modulo logging standard di Python (non structlog)
come richiesto dalla consegna.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Logger standard Python — separato da structlog usato altrove
request_logger = logging.getLogger("app.requests")


def setup_request_logger() -> None:
    """
    Configura il logger delle request con un handler su stdout
    e un formato leggibile.
    Chiamare una volta sola al momento dell'avvio dell'app.
    """
    if request_logger.handlers:
        return  # già configurato

    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    request_logger.addHandler(handler)
    request_logger.setLevel(logging.INFO)
    request_logger.propagate = False  # evita duplicati con il root logger


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware ASGI che logga ogni request/response.

    Output esempio:
        2026-05-09 10:00:01 | INFO     | POST /api/v1/emails/generate | 201 | 1842ms | 192.168.1.1
        2026-05-09 10:00:02 | INFO     | GET  /api/v1/emails/history  | 200 | 12ms   | 192.168.1.1
        2026-05-09 10:00:03 | WARNING  | POST /api/v1/auth/login       | 401 | 8ms    | 192.168.1.2
    """

    # Path da non loggare (health check e metrics)
    _SKIP_PATHS = {"/health", "/metrics", "/favicon.ico"}

    def __init__(self, app: ASGIApp, *, log_headers: bool = False) -> None:
        super().__init__(app)
        self.log_headers = log_headers

    async def dispatch(self, request: Request, call_next) -> Response:
        # Salta path di sistema
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        # Recupera IP reale (considera proxy/load balancer)
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "-")
        query = f"?{request.url.query}" if request.url.query else ""

        # Misura tempo di risposta
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)

        status_code = response.status_code
        method = request.method
        path = request.url.path

        # Scegli il livello di log in base allo status code
        if status_code >= 500:
            log_fn = request_logger.error
        elif status_code >= 400:
            log_fn = request_logger.warning
        else:
            log_fn = request_logger.info

        log_fn(
            "%-6s %-45s | %d | %5dms | %s | %s",
            method,
            f"{path}{query}",
            status_code,
            duration_ms,
            client_ip,
            user_agent[:80],  # tronca user-agent lunghi
        )

        # Aggiunge header di risposta con il tempo (utile per debug da client)
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        return response

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Recupera l'IP reale del client, gestendo proxy e load balancer."""
        # X-Forwarded-For: ip1, ip2, ip3 — il primo è il client reale
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        # X-Real-IP (nginx)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip
        # Fallback: IP diretto
        if request.client:
            return request.client.host
        return "unknown"
