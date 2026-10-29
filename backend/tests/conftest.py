"""Shared pytest fixtures.

Tests use a dedicated Postgres database (``chessmind_test``). The schema
is dropped and re-created from SQLAlchemy metadata at the start of each
test session.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://chessmind:chessmind@localhost:5432/chessmind_test",
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg://chessmind:chessmind@localhost:5432/chessmind_test",
)
os.environ.setdefault("STOCKFISH_PATH", os.path.abspath("../engines/stockfish-bin"))

from chessmind.app import app  # noqa: E402
from chessmind.config import get_settings  # noqa: E402
from chessmind.db import get_session  # noqa: E402
from chessmind.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def _schema() -> AsyncIterator[None]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=None)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(_schema) -> AsyncIterator[AsyncSession]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=None)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
