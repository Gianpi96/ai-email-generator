"""
Integration tests — usano PostgreSQL reale (disponibile in CI via GitHub Actions service)
e mockano il provider AI per non fare chiamate reali alle API esterne.

Come funziona:
- Il DB viene creato/distrutto per ogni test session
- Il provider AI è mockato — nessuna chiamata reale a Groq/OpenAI/Anthropic
- La sessione audit_db è la stessa di db per semplicità nei test
"""

import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_audit_db, get_db
from app.main import create_app
from app.services.ai_provider import AIEmailResult, get_ai_provider

# URL del DB di test — in CI viene iniettata come variabile d'ambiente
TEST_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/email_generator_test",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def engine():
    """Crea il DB e le tabelle una volta per tutta la session di test."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture()
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Sessione DB pulita per ogni test con rollback automatico."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def mock_ai() -> AsyncMock:
    """Provider AI mockato — risponde sempre con dati fissi."""
    provider = AsyncMock()
    provider.generate_email.return_value = AIEmailResult(
        subject="Test Subject",
        body="Dear John,\n\nTest email body.\n\nBest regards",
        provider="groq",
        model="llama-3.3-70b-versatile",
        prompt_tokens=100,
        completion_tokens=50,
    )
    return provider


@pytest.fixture()
async def client(db_session: AsyncSession, mock_ai: AsyncMock) -> AsyncGenerator[AsyncClient, None]:
    """Client HTTP con dipendenze override per DB e AI."""
    app = create_app()

    # Override entrambe le sessioni con la stessa sessione di test
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_audit_db] = lambda: db_session
    app.dependency_overrides[get_ai_provider] = lambda: mock_ai

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ── Helpers ───────────────────────────────────────────────────────────────────

async def register_and_login(client: AsyncClient) -> tuple[str, str]:
    """Registra un nuovo utente e ritorna (email, access_token)."""
    email = f"{uuid.uuid4()}@test.com"
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Secure123",
        "full_name": "Test User",
    })
    res = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Secure123",
    })
    return email, res.json()["access_token"]


# ── Auth tests ────────────────────────────────────────────────────────────────

class TestAuth:
    async def test_register_success(self, client: AsyncClient) -> None:
        res = await client.post("/api/v1/auth/register", json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": "Secure123",
            "full_name": "Alice",
        })
        assert res.status_code == 201
        data = res.json()
        assert "id" in data
        assert data["is_active"] is True

    async def test_register_weak_password_no_uppercase(self, client: AsyncClient) -> None:
        res = await client.post("/api/v1/auth/register", json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": "weakpass1",
        })
        assert res.status_code == 422

    async def test_register_weak_password_no_digit(self, client: AsyncClient) -> None:
        res = await client.post("/api/v1/auth/register", json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": "WeakPass",
        })
        assert res.status_code == 422

    async def test_register_duplicate_email(self, client: AsyncClient) -> None:
        email = f"{uuid.uuid4()}@dup.com"
        payload = {"email": email, "password": "Secure123"}
        await client.post("/api/v1/auth/register", json=payload)
        res = await client.post("/api/v1/auth/register", json=payload)
        assert res.status_code == 409

    async def test_login_success(self, client: AsyncClient) -> None:
        email, token = await register_and_login(client)
        assert token is not None
        assert len(token) > 10

    async def test_login_wrong_password(self, client: AsyncClient) -> None:
        email = f"{uuid.uuid4()}@example.com"
        await client.post("/api/v1/auth/register", json={
            "email": email, "password": "Secure123",
        })
        res = await client.post("/api/v1/auth/login", json={
            "email": email, "password": "WrongPass1",
        })
        assert res.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient) -> None:
        res = await client.post("/api/v1/auth/login", json={
            "email": "nobody@example.com",
            "password": "Secure123",
        })
        assert res.status_code == 401


# ── Email generation tests ────────────────────────────────────────────────────

class TestEmailGeneration:
    async def test_generate_success(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        res = await client.post(
            "/api/v1/emails/generate",
            json={
                "email_type": "formal",
                "recipient": "CEO John Smith",
                "context": "Requesting a meeting to discuss Q3 results.",
                "language": "en",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 201
        data = res.json()
        assert data["subject"] == "Test Subject"
        assert data["ai_provider"] == "groq"
        assert data["email_type"] == "formal"
        assert "id" in data

    async def test_generate_unauthenticated(self, client: AsyncClient) -> None:
        res = await client.post("/api/v1/emails/generate", json={
            "email_type": "formal",
            "recipient": "Test",
            "context": "Some context here for testing purposes.",
        })
        assert res.status_code == 403

    async def test_generate_invalid_email_type(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        res = await client.post(
            "/api/v1/emails/generate",
            json={
                "email_type": "nonexistent_type",
                "recipient": "Test",
                "context": "Some context here for testing purposes.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422

    async def test_generate_context_too_short(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        res = await client.post(
            "/api/v1/emails/generate",
            json={
                "email_type": "formal",
                "recipient": "Test",
                "context": "short",  # min 10 chars
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 422


# ── History tests ─────────────────────────────────────────────────────────────

class TestHistory:
    async def test_history_empty(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        res = await client.get(
            "/api/v1/emails/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_history_after_generation(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # Genera un'email
        await client.post("/api/v1/emails/generate", json={
            "email_type": "thank_you",
            "recipient": "Team",
            "context": "Thank the team for their hard work this quarter.",
        }, headers=headers)

        res = await client.get("/api/v1/emails/history", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 1
        assert data["items"][0]["email_type"] == "thank_you"

    async def test_history_filter_by_type(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        await client.post("/api/v1/emails/generate", json={
            "email_type": "formal",
            "recipient": "Manager",
            "context": "Requesting approval for the new project budget.",
        }, headers=headers)

        # Filtra per tipo esistente
        res = await client.get(
            "/api/v1/emails/history?email_type=formal",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["total"] == 1

        # Filtra per tipo inesistente
        res = await client.get(
            "/api/v1/emails/history?email_type=complaint",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["total"] == 0

    async def test_history_search(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        await client.post("/api/v1/emails/generate", json={
            "email_type": "commercial",
            "recipient": "Mario Rossi",
            "context": "Proposta software gestione email AI.",
        }, headers=headers)

        res = await client.get(
            "/api/v1/emails/history?search=Mario",
            headers=headers,
        )
        assert res.status_code == 200
        assert res.json()["total"] == 1

    async def test_history_pagination(self, client: AsyncClient) -> None:
        _, token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # Genera 3 email
        for email_type in ["formal", "informal", "thank_you"]:
            await client.post("/api/v1/emails/generate", json={
                "email_type": email_type,
                "recipient": "Test recipient name",
                "context": "Context for pagination test email generation.",
            }, headers=headers)

        # Prima pagina con page_size=2
        res = await client.get(
            "/api/v1/emails/history?page=1&page_size=2",
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3
        assert data["pages"] == 2

    async def test_history_unauthenticated(self, client: AsyncClient) -> None:
        res = await client.get("/api/v1/emails/history")
        assert res.status_code == 403


# ── Health check ──────────────────────────────────────────────────────────────

class TestHealth:
    async def test_health_ok(self, client: AsyncClient) -> None:
        res = await client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
