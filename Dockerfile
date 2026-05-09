# ─────────────────────────────────────────────────────────────
# Stage 1 — builder
# Installa le dipendenze in un ambiente isolato.
# Questo stage non va in produzione, serve solo a costruire.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Installa dipendenze di sistema per compilare pacchetti nativi (asyncpg, bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia solo pyproject.toml per sfruttare il layer cache di Docker:
# se il codice cambia ma le dipendenze no, questo layer resta cached.
COPY pyproject.toml .

# Installa le dipendenze in una cartella dedicata (non system-wide)
RUN pip install --upgrade pip setuptools wheel && \
    pip install --prefix=/install -e ".[dev]" || \
    pip install --prefix=/install \
        fastapi uvicorn[standard] sqlalchemy alembic asyncpg \
        pydantic[email] pydantic-settings python-jose[cryptography] \
        bcrypt python-multipart httpx anthropic openai tenacity \
        structlog prometheus-fastapi-instrumentator


# ─────────────────────────────────────────────────────────────
# Stage 2 — production
# Immagine finale leggera: solo runtime, niente build tools.
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Solo le librerie runtime necessarie (no build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copia le dipendenze compilate dallo stage builder
COPY --from=builder /install /usr/local

# Copia il codice dell'applicazione
COPY . .

# Utente non-root per sicurezza
RUN useradd -m -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Comando di default (Railway lo sovrascrive via Procfile o railway.toml)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]