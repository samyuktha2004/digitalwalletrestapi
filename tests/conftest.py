"""Test wiring.

Every test runs against a real PostgreSQL database. There is no SQLite fallback
on purpose: SQLite has no `SELECT ... FOR UPDATE`, so it would happily pass the
concurrency test while proving nothing.
"""

from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app

# NullPool: pytest-asyncio gives each test a fresh event loop, and a pooled
# asyncpg connection cannot be reused across loops ("Event loop is closed").
# Concurrency is unaffected — every request still opens its own connection.
test_engine = create_async_engine(
    settings.test_database_url,
    poolclass=NullPool,
    connect_args={"server_settings": {"lock_timeout": "5000"}},
)
TestSessionLocal = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)


async def _override_get_session() -> AsyncIterator:
    async with TestSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


app.dependency_overrides[get_session] = _override_get_session


@pytest_asyncio.fixture
async def clean_db() -> AsyncIterator[None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client(clean_db: None) -> AsyncIterator[AsyncClient]:
    # ASGITransport skips the app lifespan, so the production engine in
    # app.db.session is never opened during tests.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0
    ) as c:
        yield c


async def make_user(
    client: AsyncClient, username: str, balance: Decimal | str = "0.00"
) -> dict[str, str]:
    """Register a user, fund the wallet, return an auth header."""
    r = await client.post("/auth/register", json={"username": username, "password": "password123"})
    assert r.status_code == 201, r.text

    r = await client.post(
        "/auth/login", data={"username": username, "password": "password123"}
    )
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    if Decimal(balance) > 0:
        r = await client.post("/wallet/deposit", json={"amount": str(balance)}, headers=headers)
        assert r.status_code == 200, r.text
        assert Decimal(r.json()["balance"]) == Decimal(balance)
    return headers


@pytest.fixture
def seed_balance() -> Decimal:
    return Decimal("1000.00")
