# AI Email Generator

[![CI](https://github.com/Gianpi96/ai-email-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/Gianpi96/ai-email-generator/actions)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Backend API per la generazione di email professionali tramite intelligenza artificiale. Supporta 10 tipologie di email con prompt template dedicati, audit log completo e export CSV.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| ![FastAPI](https://img.shields.io/badge/-FastAPI-009688?logo=fastapi&logoColor=white) | REST API framework |
| ![PostgreSQL](https://img.shields.io/badge/-PostgreSQL-4169E1?logo=postgresql&logoColor=white) | Database relazionale |
| ![SQLAlchemy](https://img.shields.io/badge/-SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white) | ORM async |
| ![Docker](https://img.shields.io/badge/-Docker-2496ED?logo=docker&logoColor=white) | Containerizzazione |
| ![Groq](https://img.shields.io/badge/-Groq-FF6B35?logoColor=white) | AI provider (gratuito) |
| ![Pydantic](https://img.shields.io/badge/-Pydantic-E92063?logo=pydantic&logoColor=white) | Validazione dati |

---

## Features

- **10 prompt template** distinti — `formal`, `commercial`, `follow_up`, `complaint`, `cold_outreach`, `apology`, `invitation`, `introduction`, `thank_you`, `informal`
- **JWT Authentication** — register, login, refresh token
- **AI Audit Log** — ogni chiamata AI viene salvata con prompt usato, token, costo stimato USD e durata ms
- **History con filtri** — ricerca full-text, filtro per tipo/data/provider, ordinamento, paginazione
- **Export CSV** — download delle email con `StreamingResponse` (batch da 100 righe, memory-efficient)
- **Rate Limiting** — 100 req/min globali, 10 req/min sull'endpoint AI (slowapi)
- **Request Logging** — ogni request loggata con metodo, path, status code e tempo risposta (logging standard Python)
- **Prometheus metrics** — endpoint `/metrics` per monitoring

---

## Avvio locale

```bash
git clone https://github.com/Gianpi96/ai-email-generator.git
cd ai-email-generator
cp .env.example .env   # aggiungi GROQ_API_KEY su console.groq.com (gratuito)
docker-compose up --build
```

API disponibile su **http://localhost:8000/docs**

---

## Demo live

> 🚧 Deploy su Railway — temporaneamente offline (piano gratuito esaurito).
> Il progetto è completamente funzionante in locale con Docker.

---

## Screenshot Swagger UI

```
http://localhost:8000/docs
```

![Swagger UI](docs/swagger.png)

> Per generare lo screenshot: avvia il progetto e cattura la pagina `/docs`.

---

## Struttura progetto

```
ai-email-generator/
├── main.py                          # Entrypoint uvicorn
├── pyproject.toml                   # Dipendenze e configurazione tool
├── Dockerfile                       # Build produzione
├── docker-compose.yml               # Stack locale (API + PostgreSQL)
├── .env.example                     # Template variabili d'ambiente
│
├── app/
│   ├── main.py                      # App factory + middleware + lifespan
│   │
│   ├── api/v1/
│   │   ├── deps.py                  # Dependency injection (CurrentUser)
│   │   ├── router.py                # Aggregatore router
│   │   └── endpoints/
│   │       ├── auth.py              # POST /register, /login
│   │       └── emails.py            # POST /generate, GET /history, /export
│   │
│   ├── core/
│   │   ├── settings.py              # Configurazione via pydantic-settings
│   │   ├── security.py              # JWT + bcrypt
│   │   ├── exceptions.py            # Gerarchia eccezioni dominio
│   │   ├── logging.py               # structlog configuration
│   │   ├── middleware.py            # Request logging middleware
│   │   └── rate_limit.py            # slowapi limiter
│   │
│   ├── db/session.py                # Async engine + get_db dependency
│   │
│   ├── models/models.py             # ORM: User, GeneratedEmail, AIRequestLog
│   ├── schemas/schemas.py           # Pydantic v2 I/O schemas
│   │
│   └── services/
│       ├── ai_provider.py           # Protocol + Anthropic/OpenAI/Groq
│       ├── ai_service.py            # Prompt templates + orchestrazione
│       ├── auth_service.py          # Register / login
│       └── email_service.py         # History, filtri, CSV export
│
├── migrations/env.py                # Alembic async
└── tests/
    └── integration/test_emails.py
```

---

## Variabili d'ambiente

Copia `.env.example` in `.env` e configura:

| Variabile | Descrizione | Obbligatoria |
|-----------|-------------|:---:|
| `DATABASE_URL` | URL PostgreSQL (asyncpg) | ✅ |
| `JWT_SECRET_KEY` | Chiave segreta min 32 char | ✅ |
| `GROQ_API_KEY` | API key Groq (gratuita) | ✅ |
| `AI_PROVIDER` | `groq` / `openai` / `anthropic` | ✅ |
| `APP_ENV` | `development` / `production` | ✅ |
| `GROQ_MODEL` | Default: `llama-3.3-70b-versatile` | ❌ |
| `DEBUG` | Abilita SQL logging | ❌ |

---

## API Endpoints

| Method | Path | Auth | Rate limit | Descrizione |
|--------|------|:----:|:----------:|-------------|
| `POST` | `/api/v1/auth/register` | ❌ | 100/min | Registra nuovo utente |
| `POST` | `/api/v1/auth/login` | ❌ | 100/min | Login, ritorna JWT |
| `POST` | `/api/v1/emails/generate` | ✅ | **10/min** | Genera email con AI |
| `GET` | `/api/v1/emails/history` | ✅ | 100/min | History con filtri e paginazione |
| `GET` | `/api/v1/emails/history/export` | ✅ | 100/min | Export CSV (streaming) |
| `GET` | `/api/v1/emails/logs` | ✅ | 100/min | Audit log chiamate AI |
| `GET` | `/api/v1/emails/{id}` | ✅ | 100/min | Singola email |
| `DELETE` | `/api/v1/emails/{id}` | ✅ | 100/min | Elimina email |
| `GET` | `/health` | ❌ | — | Healthcheck |
| `GET` | `/metrics` | ❌ | — | Prometheus metrics |

---

## Contribuire

1. Forka il repository
2. Crea un branch: `git checkout -b feat/nome-feature`
3. Committa le modifiche: `git commit -m "feat: descrizione"`
4. Pusha: `git push origin feat/nome-feature`
5. Apri una Pull Request

Usa [Conventional Commits](https://www.conventionalcommits.org/) per i messaggi di commit.

---

## Licenza

MIT © [Gianpaolo Ingrassia](https://github.com/Gianpi96)
