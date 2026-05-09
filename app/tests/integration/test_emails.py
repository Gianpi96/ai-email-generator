"""
Integration tests for the email generation API.
Uses an in-memory SQLite DB and a mocked AI provider.
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import Base, get_db
from app.main import create_app
from app.services.ai_provider import AIEmailResult, get_ai_provider

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def mock_ai():
    provider = AsyncMock()
    provider.generate_email.return_value = AIEmailResult(
        subject="Test Subject",
        body="Test email body.",
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        prompt_tokens=100,
        completion_tokens=50,
    )
    return provider


@pytest.fixture()
async def client(db_session, mock_ai) -> AsyncGenerator[AsyncClient, None]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_ai_provider] = lambda: mock_ai

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def _register_and_login(client: AsyncClient) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4()}@test.com",
        "password": "Secure123",
        "full_name": "Test User",
    })
    res = await client.post("/api/v1/auth/login", json={
        "email": "test@test.com",
        "password": "Secure123",
    })
    # In a real test, we'd return the token; simplified here
    return res.json().get("access_token", "")


class TestAuthEndpoints:
    async def test_register_success(self, client: AsyncClient):
        res = await client.post("/api/v1/auth/register", json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": "Secure123",
            "full_name": "Alice",
        })
        assert res.status_code == 201
        data = res.json()
        assert "id" in data
        assert data["email"].endswith("@example.com")

    async def test_register_weak_password(self, client: AsyncClient):
        res = await client.post("/api/v1/auth/register", json={
            "email": "bob@example.com",
            "password": "weak",
        })
        assert res.status_code == 422

    async def test_register_duplicate_email(self, client: AsyncClient):
        payload = {"email": f"{uuid.uuid4()}@dup.com", "password": "Secure123"}
        await client.post("/api/v1/auth/register", json=payload)
        res = await client.post("/api/v1/auth/register", json=payload)
        assert res.status_code == 409


class TestEmailEndpoints:
    async def _get_token(self, client: AsyncClient) -> str:
        email = f"{uuid.uuid4()}@example.com"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "Secure123",
        })
        res = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "Secure123",
        })
        return res.json()["access_token"]

    async def test_generate_email_success(self, client: AsyncClient):
        token = await self._get_token(client)
        res = await client.post(
            "/api/v1/emails/generate",
            json={
                "email_type": "formal",
                "recipient": "CEO John Smith",
                "context": "Requesting a meeting to discuss Q3 results.",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 201
        data = res.json()
        assert data["subject"] == "Test Subject"
        assert data["ai_provider"] == "anthropic"

    async def test_generate_email_unauthenticated(self, client: AsyncClient):
        res = await client.post("/api/v1/emails/generate", json={
            "email_type": "formal",
            "recipient": "Test",
            "context": "Some context here for testing purposes.",
        })
        assert res.status_code == 403

    async def test_history_returns_paginated(self, client: AsyncClient):
        token = await self._get_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        await client.post("/api/v1/emails/generate", json={
            "email_type": "thank_you",
            "recipient": "Team",
            "context": "Thank the team for their hard work this quarter.",
        }, headers=headers)
        res = await client.get("/api/v1/emails/history", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "items" in data
        assert "total" in data
