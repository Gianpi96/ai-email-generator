"""
Integration tests — PostgreSQL reale + AI mockato.

Ogni test ha il proprio engine e sessione per evitare
conflitti di event loop con asyncpg.
"""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_audit_db, get_db
from app.main import create_app
from app.services.ai_provider import AIEmailResult, get_ai_provider

TEST_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/email_generator_test",
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def db_session():
    """Sessione DB fresca per ogni test con tabelle ricreate."""
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()

    await eng.dispose()


@pytest.fixture()
def mock_ai():
    provider = AsyncMock()
    provider.generate_email.return_value = AIEmailResult(
        subject="Test Subject",
        body="Dear Test,\n\nTest body.\n\nBest regards",
        provider="groq",
        model="llama-3.3-70b-versatile",
        prompt_tokens=100,
        completion_tokens=50,
    )
    return provider


@pytest_asyncio.fixture()
async def client(db_session, mock_ai):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_audit_db] = lambda: db_session
    app.dependency_overrides[get_ai_provider] = lambda: mock_ai
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


async def _register_and_login(client: AsyncClient) -> str:
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
    return res.json()["access_token"]


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_success(client):
    res = await client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4()}@example.com",
        "password": "Secure123",
        "full_name": "Alice",
    })
    assert res.status_code == 201
    assert "id" in res.json()


@pytest.mark.asyncio
async def test_register_weak_password(client):
    res = await client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4()}@example.com",
        "password": "weakpass1",
    })
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    email = f"{uuid.uuid4()}@dup.com"
    payload = {"email": email, "password": "Secure123"}
    await client.post("/api/v1/auth/register", json=payload)
    res = await client.post("/api/v1/auth/register", json=payload)
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    email = f"{uuid.uuid4()}@example.com"
    await client.post("/api/v1/auth/register", json={
        "email": email, "password": "Secure123",
    })
    res = await client.post("/api/v1/auth/login", json={
        "email": email, "password": "WrongPass1",
    })
    assert res.status_code == 401


# ── Email generation ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_success(client):
    token = await _register_and_login(client)
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
    assert data["email_type"] == "formal"


@pytest.mark.asyncio
async def test_generate_unauthenticated(client):
    res = await client.post("/api/v1/emails/generate", json={
        "email_type": "formal",
        "recipient": "Test",
        "context": "Some context here for testing purposes.",
    })
    # FastAPI HTTPBearer ritorna 403 se no credentials, 401 se credentials invalide
    assert res.status_code in (401, 403)


@pytest.mark.asyncio
async def test_generate_invalid_type(client):
    token = await _register_and_login(client)
    res = await client.post(
        "/api/v1/emails/generate",
        json={
            "email_type": "invalid_type",
            "recipient": "Test",
            "context": "Some context here for testing purposes.",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 422


# ── History ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_history_empty(client):
    token = await _register_and_login(client)
    res = await client.get(
        "/api/v1/emails/history",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0


@pytest.mark.asyncio
async def test_history_after_generation(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/api/v1/emails/generate", json={
        "email_type": "thank_you",
        "recipient": "Team leader name",
        "context": "Thank the team for their hard work this quarter.",
    }, headers=headers)
    res = await client.get("/api/v1/emails/history", headers=headers)
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.asyncio
async def test_history_filter_by_type(client):
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/api/v1/emails/generate", json={
        "email_type": "formal",
        "recipient": "Manager person name",
        "context": "Requesting approval for the new project budget.",
    }, headers=headers)
    res = await client.get(
        "/api/v1/emails/history?email_type=complaint",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.json()["total"] == 0


@pytest.mark.asyncio
async def test_history_unauthenticated(client):
    res = await client.get("/api/v1/emails/history")
    assert res.status_code in (401, 403)
